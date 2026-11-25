#define _POSIX_C_SOURCE 200809L
#include "screen.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
extern void ui_tick(void);
static char *read_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        perror(path);
        exit(1);
    }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    rewind(f);
    if (n < 0 || n > 4 * 1024 * 1024)
        exit(1);
    char *s = calloc(n + 1, 1);
    if (fread(s, 1, n, f) != (size_t)n)
        exit(1);
    fclose(f);
    return s;
}
int main(int argc, char **argv) {
    const char *assets = "touchscreen/assets/generated", *socket = "/tmp/openklipper-screen.sock",
               *snapshot = NULL, *geometry = NULL, *actions = NULL, *record = NULL, *fixture = NULL,
               *route = "home", *input = NULL;
    bool fb = false, window = true;
    int rotate = 90, mx = 480, my = 800;
    bool swap = false, invert_x = false, invert_y = false;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--assets") && i + 1 < argc)
            assets = argv[++i];
        else if (!strcmp(argv[i], "--socket") && i + 1 < argc)
            socket = argv[++i];
        else if (!strcmp(argv[i], "--snapshot") && i + 1 < argc) {
            snapshot = argv[++i];
            window = false;
        } else if (!strcmp(argv[i], "--fixture") && i + 1 < argc)
            fixture = argv[++i];
        else if (!strcmp(argv[i], "--geometry") && i + 1 < argc)
            geometry = argv[++i];
        else if (!strcmp(argv[i], "--record-commands") && i + 1 < argc)
            record = argv[++i];
        else if (!strcmp(argv[i], "--actions") && i + 1 < argc)
            actions = argv[++i];
        else if (!strcmp(argv[i], "--page") && i + 1 < argc)
            route = argv[++i];
        else if (!strcmp(argv[i], "--input") && i + 1 < argc)
            input = argv[++i];
        else if (!strcmp(argv[i], "--rotate") && i + 1 < argc)
            rotate = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--touch-max-x") && i + 1 < argc)
            mx = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--touch-max-y") && i + 1 < argc)
            my = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--touch-swap-xy"))
            swap = true;
        else if (!strcmp(argv[i], "--touch-invert-x"))
            invert_x = true;
        else if (!strcmp(argv[i], "--touch-invert-y"))
            invert_y = true;
        else if (!strcmp(argv[i], "--framebuffer")) {
            fb = true;
            window = false;
        } else if (!strcmp(argv[i], "--headless"))
            window = false;
        else {
            fprintf(stderr,
                    "Usage: %s [--framebuffer --input /dev/input/event0 --rotate 90] [--assets "
                    "DIR] [--socket PATH] [--fixture JSON --page NAME --snapshot output.ppm]\n",
                    argv[0]);
            return 2;
        }
    }
    if (actions && !fixture) {
        fprintf(stderr, "Scripted UI actions require a read-only fixture\n");
        return 2;
    }
    if (mx <= 0 || my <= 0 || (rotate != 0 && rotate != 90 && rotate != 180 && rotate != 270)) {
        fprintf(stderr, "Invalid panel calibration/orientation\n");
        return 2;
    }
    lv_init();
    platform_touch_config(mx, my, swap, invert_x, invert_y);
    platform_init(fb, input, rotate, window);
    ui_init(assets, socket);
    app.fixture = fixture != NULL;
    if (record)
        snprintf(app.command_record, sizeof(app.command_record), "%s", record);
    if (fixture) {
        char *raw = read_file(fixture);
        cJSON *data = cJSON_Parse(raw);
        free(raw);
        if (!data) {
            fprintf(stderr, "Invalid fixture\n");
            return 1;
        }
        ui_data(data);
    }
    ui_route(route, true);
    ui_render();
    if (actions) {
        char *raw = read_file(actions);
        cJSON *steps = cJSON_Parse(raw);
        free(raw);
        cJSON *step;
        cJSON_ArrayForEach(step, steps) {
            cJSON *queued = cJSON_GetObjectItemCaseSensitive(step, "queue_touch");
            if (cJSON_IsArray(queued))
                platform_test_touch((int)jnum(queued, "0", 0), (int)jnum(queued, "1", 0),
                                    jbool(queued, "2", false));
            cJSON *tap = cJSON_GetObjectItemCaseSensitive(step, "tap");
            if (cJSON_IsArray(tap)) {
                int x = (int)jnum(tap, "0", 0), y = (int)jnum(tap, "1", 0);
                platform_test_touch(x, y, true);
                lv_tick_inc(20);
                lv_timer_handler();
                platform_test_touch(x, y, false);
                lv_tick_inc(20);
                lv_timer_handler();
                ui_tick();
            }
            if (cJSON_IsString(step))
                ui_action(step->valuestring);
            else if (cJSON_IsObject(step) && cJSON_GetObjectItemCaseSensitive(step, "snapshot"))
                ui_data(cJSON_Duplicate(cJSON_GetObjectItemCaseSensitive(step, "snapshot"), true));
            else if (cJSON_IsObject(step) && jnum(step, "advance_ms", 0) > 0)
                lv_tick_inc((uint32_t)jnum(step, "advance_ms", 0));
            else if (cJSON_IsObject(step) && jbool(step, "transport_lost", false))
                ui_transport_lost();
            ui_tick();
            lv_tick_inc(20);
            lv_timer_handler();
            ui_tick();
            lv_refr_now(NULL);
        }
        cJSON_Delete(steps);
    }
    if (snapshot) {
        lv_refr_now(NULL);
        if (geometry)
            ui_geometry(geometry);
        platform_snapshot(snapshot);
        platform_shutdown();
        return 0;
    }
    uint64_t last = platform_millis();
    while (!platform_closed()) {
        uint64_t now = platform_millis();
        lv_tick_inc(now - last);
        last = now;
        platform_pump();
        if (!fixture) {
            char *raw = platform_receive();
            if (raw) {
                ui_data(cJSON_Parse(raw));
                free(raw);
            }
            if (!platform_linked())
                ui_transport_lost();
        }
        ui_tick();
        uint32_t wait = lv_timer_handler();
        if (wait > 8)
            wait = 8;
        struct timespec pause = {0, (long)(wait ? wait : 1) * 1000000};
        nanosleep(&pause, NULL);
    }
    platform_shutdown();
    return 0;
}
