#define _POSIX_C_SOURCE 200809L
#include "screen.h"
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
#ifdef SCREEN_X11
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#endif
static uint32_t pixels[WIDTH * HEIGHT];
static lv_color_t draw_pixels[WIDTH * 64];
static lv_disp_draw_buf_t draw_buffer;
static lv_disp_drv_t display;
/* LVGL retains the registered driver pointer for the input timer's lifetime. */
static lv_indev_drv_t input_driver;
static int fb = -1, input = -1, ipc = -1, rotation = 90;
static uint8_t *memory;
static size_t memory_size, stride;
static bool closed, pressed, delivered_pressed, raw_delivered_pressed, wake_touch;
static int tx, ty, raw_x, raw_y;
static int touch_max_x = 480, touch_max_y = 800;
static bool touch_swap, touch_invert_x, touch_invert_y;
static struct {
    int x, y;
    bool pressed;
} samples[256];
static unsigned sample_read, sample_write;
static uint64_t last_touch_event, input_retry_at;
static char input_device[1024];
static void queue_sample(void) {
    unsigned next = (sample_write + 1) % 256;
    if (next == sample_read)
        sample_read = (sample_read + 1) % 256;
    samples[sample_write].x = tx;
    samples[sample_write].y = ty;
    samples[sample_write].pressed = pressed;
    sample_write = next;
}
void platform_cancel_touch(void) {
    pressed = delivered_pressed = raw_delivered_pressed = wake_touch = false;
    sample_read = sample_write = 0;
    lv_indev_reset(NULL, NULL);
}
void platform_test_touch(int x, int y, bool down) {
    last_touch_event = platform_millis();
    tx = x;
    ty = y;
    pressed = down;
    queue_sample();
}
void platform_touch_config(int x, int y, bool swap, bool inv_x, bool inv_y) {
    touch_max_x = x;
    touch_max_y = y;
    touch_swap = swap;
    touch_invert_x = inv_x;
    touch_invert_y = inv_y;
}

static char *receive_buffer;
static size_t receive_length;
static uint64_t reconnect_at;
#ifdef SCREEN_X11
static Display *xd;
static Window window;
static GC gc;
static XImage *xi;
static Atom wm_delete;
#endif
uint64_t platform_millis(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}
static void stop(int sig) {
    (void)sig;
    closed = true;
}
static void flush(lv_disp_drv_t *driver, const lv_area_t *area, lv_color_t *colours) {
    for (int y = area->y1; y <= area->y2; y++)
        for (int x = area->x1; x <= area->x2; x++) {
            uint32_t value = colours->full | 0xff000000u;
            colours++;
            if (x < 0 || y < 0 || x >= WIDTH || y >= HEIGHT)
                continue;
            pixels[y * WIDTH + x] = value;
            if (memory) {
                int px = x, py = y;
                if (rotation == 90) {
                    px = HEIGHT - 1 - y;
                    py = x;
                } else if (rotation == 270) {
                    px = y;
                    py = WIDTH - 1 - x;
                } else if (rotation == 180) {
                    px = WIDTH - 1 - x;
                    py = HEIGHT - 1 - y;
                }
                size_t offset = (size_t)py * stride + (size_t)px * 4;
                if (offset + 4 <= memory_size)
                    memcpy(memory + offset, &value, 4);
            }
        }
#ifdef SCREEN_X11
    if (xd && lv_disp_flush_is_last(driver)) {
        XPutImage(xd, window, gc, xi, 0, 0, 0, 0, WIDTH, HEIGHT);
        XFlush(xd);
    }
#endif
    lv_disp_flush_ready(driver);
}
static void read_input(lv_indev_drv_t *driver, lv_indev_data_t *data) {
    (void)driver;
    if (sample_read != sample_write) {
        data->point.x = samples[sample_read].x;
        data->point.y = samples[sample_read].y;
        data->state = samples[sample_read].pressed ? LV_INDEV_STATE_PR : LV_INDEV_STATE_REL;
        sample_read = (sample_read + 1) % 256;
        data->continue_reading = sample_read != sample_write;
    } else {
        data->point.x = tx;
        data->point.y = ty;
        data->state = pressed ? LV_INDEV_STATE_PR : LV_INDEV_STATE_REL;
    }
    bool down = data->state == LV_INDEV_STATE_PR;
    if (down && !raw_delivered_pressed) {
        if (!app.fixture)
            platform_send("{\"action\":\"activity\"}");
        wake_touch = B("platform.asleep");
    }
    raw_delivered_pressed = down;
    if (wake_touch) {
        data->state = LV_INDEV_STATE_REL;
        if (!down)
            wake_touch = false;
    }
    delivered_pressed = data->state == LV_INDEV_STATE_PR;
}
void platform_init(bool framebuffer, const char *device, int angle, bool use_window) {
    rotation = angle;
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    if (framebuffer) {
        struct fb_var_screeninfo v;
        struct fb_fix_screeninfo f;
        fb = open("/dev/fb0", O_RDWR);
        if (fb < 0 || ioctl(fb, FBIOGET_VSCREENINFO, &v) || ioctl(fb, FBIOGET_FSCREENINFO, &f)) {
            perror("framebuffer");
            exit(1);
        }
        int w = (rotation == 90 || rotation == 270) ? HEIGHT : WIDTH,
            h = (rotation == 90 || rotation == 270) ? WIDTH : HEIGHT;
        if (v.bits_per_pixel != 32 || v.xres != (unsigned)w || v.yres != (unsigned)h ||
            v.red.offset != 16 || v.green.offset != 8 || v.blue.offset != 0) {
            fprintf(stderr, "Unsupported framebuffer geometry/format\n");
            exit(1);
        }
        memory_size = f.smem_len;
        stride = f.line_length;
        memory = mmap(NULL, memory_size, PROT_READ | PROT_WRITE, MAP_SHARED, fb, 0);
        if (memory == MAP_FAILED) {
            perror("mmap");
            exit(1);
        }
    }
    if (device) {
        snprintf(input_device, sizeof(input_device), "%s", device);
        input = open(device, O_RDONLY | O_NONBLOCK);
        if (input < 0) {
            perror("touch input");
            exit(1);
        }
    }
#ifdef SCREEN_X11
    if (use_window) {
        xd = XOpenDisplay(NULL);
        if (!xd) {
            fprintf(stderr, "No X11 display; use --snapshot for offscreen rendering\n");
            exit(1);
        }
        int screen = DefaultScreen(xd);
        window = XCreateSimpleWindow(xd, RootWindow(xd, screen), 0, 0, WIDTH, HEIGHT, 0, 0, 0);
        XStoreName(xd, window, "OpenKlipper native touchscreen - 800 x 480");
        XSelectInput(xd, window,
                     ExposureMask | ButtonPressMask | ButtonReleaseMask | PointerMotionMask |
                         StructureNotifyMask);
        wm_delete = XInternAtom(xd, "WM_DELETE_WINDOW", False);
        XSetWMProtocols(xd, window, &wm_delete, 1);
        XMapWindow(xd, window);
        gc = XCreateGC(xd, window, 0, NULL);
        xi = XCreateImage(xd, DefaultVisual(xd, screen), 24, ZPixmap, 0, (char *)pixels, WIDTH,
                          HEIGHT, 32, WIDTH * 4);
    }
#else
    (void)use_window;
#endif
    lv_disp_draw_buf_init(&draw_buffer, draw_pixels, NULL, WIDTH * 64);
    lv_disp_drv_init(&display);
    display.hor_res = WIDTH;
    display.ver_res = HEIGHT;
    display.draw_buf = &draw_buffer;
    display.flush_cb = flush;
    lv_disp_drv_register(&display);
    lv_indev_drv_init(&input_driver);
    input_driver.type = LV_INDEV_TYPE_POINTER;
    input_driver.read_cb = read_input;
    lv_indev_drv_register(&input_driver);
}
void platform_pump(void) {
    if (input < 0 && *input_device && platform_millis() >= input_retry_at) {
        input_retry_at = platform_millis() + 1000;
        input = open(input_device, O_RDONLY | O_NONBLOCK);
    }
    if (input >= 0) {
        struct input_event event;
        int events = 0;
        ssize_t got = 0;
        while (events++ < 128 &&
               (got = read(input, &event, sizeof(event))) == (ssize_t)sizeof(event)) {
            last_touch_event = platform_millis();
            if (event.type == EV_SYN && event.code == SYN_DROPPED) {
                platform_cancel_touch();
                continue;
            }
            if (event.type == EV_ABS) {
                if (event.code == ABS_X || event.code == ABS_MT_POSITION_X)
                    raw_x = event.value;
                else if (event.code == ABS_Y || event.code == ABS_MT_POSITION_Y)
                    raw_y = event.value;
            } else if (event.type == EV_KEY && event.code == BTN_TOUCH)
                pressed = event.value != 0;
            if (event.type == EV_SYN) {
                double x = touch_swap ? raw_y : raw_x, y = touch_swap ? raw_x : raw_y;
                int fw = (rotation == 90 || rotation == 270) ? HEIGHT : WIDTH,
                    fh = (rotation == 90 || rotation == 270) ? WIDTH : HEIGHT;
                x = x * fw / touch_max_x;
                y = y * fh / touch_max_y;
                if (touch_invert_x)
                    x = fw - 1 - x;
                if (touch_invert_y)
                    y = fh - 1 - y;
                if (rotation == 90) {
                    tx = (int)y;
                    ty = HEIGHT - 1 - (int)x;
                } else if (rotation == 270) {
                    tx = WIDTH - 1 - (int)y;
                    ty = (int)x;
                } else if (rotation == 180) {
                    tx = WIDTH - 1 - (int)x;
                    ty = HEIGHT - 1 - (int)y;
                } else {
                    tx = (int)x;
                    ty = (int)y;
                }
                tx = tx < 0 ? 0 : tx >= WIDTH ? WIDTH - 1 : tx;
                ty = ty < 0 ? 0 : ty >= HEIGHT ? HEIGHT - 1 : ty;
                queue_sample();
            }
        }
        if (events <= 128 && got < 0 && (errno == EIO || errno == ENODEV)) {
            platform_cancel_touch();
            close(input);
            input = -1;
            input_retry_at = platform_millis() + 1000;
        }
    }
#ifdef SCREEN_X11
    while (xd && XPending(xd)) {
        XEvent e;
        XNextEvent(xd, &e);
        if (e.type == Expose) {
            XPutImage(xd, window, gc, xi, 0, 0, 0, 0, WIDTH, HEIGHT);
        } else if (e.type == ButtonPress || e.type == ButtonRelease) {
            tx = e.xbutton.x;
            ty = e.xbutton.y;
            pressed = e.type == ButtonPress;
            queue_sample();
        } else if (e.type == MotionNotify) {
            tx = e.xmotion.x;
            ty = e.xmotion.y;
            queue_sample();
        } else if (e.type == ClientMessage && (Atom)e.xclient.data.l[0] == wm_delete)
            closed = true;
    }
#endif
}
bool platform_closed(void) {
    return closed;
}
bool platform_pressed(void) {
    if (pressed && platform_millis() - last_touch_event > 5000)
        platform_cancel_touch();
    /* Do not delete a pressed widget before LVGL consumes its queued release. */
    return pressed || delivered_pressed || sample_read != sample_write;
}
void platform_snapshot(const char *path) {
    FILE *file = fopen(path, "wb");
    if (!file) {
        perror(path);
        exit(1);
    }
    fprintf(file, "P6\n%d %d\n255\n", WIDTH, HEIGHT);
    for (int i = 0; i < WIDTH * HEIGHT; i++) {
        uint8_t rgb[] = {pixels[i] >> 16, pixels[i] >> 8, pixels[i]};
        fwrite(rgb, 1, 3, file);
    }
    fclose(file);
}
static bool connect_ipc(void) {
    if (ipc >= 0)
        return true;
    if (platform_millis() < reconnect_at)
        return false;
    reconnect_at = platform_millis() + 1000;
    ipc = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un addr = {.sun_family = AF_UNIX};
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", app.socket);
    if (connect(ipc, (struct sockaddr *)&addr, sizeof(addr))) {
        close(ipc);
        ipc = -1;
        receive_length = 0;
        return false;
    }
    fcntl(ipc, F_SETFL, O_NONBLOCK);
    return true;
}
bool platform_send(const char *text) {
    if (!connect_ipc())
        return false;
    size_t n = strlen(text);
    char *line = malloc(n + 2);
    memcpy(line, text, n);
    line[n] = '\n';
    line[n + 1] = 0;
    ssize_t sent = send(ipc, line, n + 1, MSG_NOSIGNAL);
    free(line);
    if (sent != (ssize_t)n + 1) {
        close(ipc);
        ipc = -1;
        receive_length = 0;
        return false;
    }
    return true;
}
bool platform_linked(void) {
    return ipc >= 0;
}
char *platform_receive(void) {
    if (!connect_ipc())
        return NULL;
    for (int i = 0; i < 32; i++) {
        char chunk[65536];
        ssize_t n = recv(ipc, chunk, sizeof(chunk), 0);
        if (n > 0) {
            if (receive_length + n > 4 * 1024 * 1024) {
                close(ipc);
                ipc = -1;
                receive_length = 0;
                return NULL;
            }
            char *next = realloc(receive_buffer, receive_length + n + 1);
            if (!next)
                exit(1);
            receive_buffer = next;
            memcpy(receive_buffer + receive_length, chunk, n);
            receive_length += n;
            receive_buffer[receive_length] = 0;
        } else {
            if (!n || (errno != EAGAIN && errno != EWOULDBLOCK)) {
                close(ipc);
                ipc = -1;
                receive_length = 0;
                return NULL;
            }
            break;
        }
    }
    if (!receive_buffer)
        return NULL;
    char *last = NULL, *p = receive_buffer;
    while ((p = memchr(p, '\n', receive_length - (size_t)(p - receive_buffer)))) {
        last = p;
        p++;
    }
    if (!last)
        return NULL;
    char *start = last;
    while (start > receive_buffer && start[-1] != '\n')
        start--;
    char *result = strndup(start, last - start);
    size_t consumed = (size_t)(last - receive_buffer) + 1;
    memmove(receive_buffer, receive_buffer + consumed, receive_length - consumed);
    receive_length -= consumed;
    receive_buffer[receive_length] = 0;
    return result;
}
void platform_shutdown(void) {
    if (ipc >= 0)
        close(ipc);
    if (input >= 0)
        close(input);
    if (memory)
        munmap(memory, memory_size);
    if (fb >= 0)
        close(fb);
    free(receive_buffer);
}
