#ifndef SCREEN_H
#define SCREEN_H
#include "lvgl.h"
#include "cJSON.h"
#include <stdbool.h>
#include <stdint.h>
#define WIDTH 800
#define HEIGHT 480
#define LEFT 103
#define RIGHT 781
enum {
    SETTINGS_ROW_HEIGHT = 78,
    SETTINGS_CARD_GAP = 16,
    SETTINGS_ROW_PITCH = SETTINGS_ROW_HEIGHT + SETTINGS_CARD_GAP
};
LV_FONT_DECLARE(font_ui_12);
LV_FONT_DECLARE(font_ui_13);
LV_FONT_DECLARE(font_ui_14);
LV_FONT_DECLARE(font_ui_15);
LV_FONT_DECLARE(font_ui_16);
LV_FONT_DECLARE(font_ui_17);
LV_FONT_DECLARE(font_ui_18);
LV_FONT_DECLARE(font_ui_20);
LV_FONT_DECLARE(font_ui_24);
LV_FONT_DECLARE(font_ui_25);
LV_FONT_DECLARE(font_ui_30);
LV_FONT_DECLARE(font_heading_28);
typedef struct {
    cJSON *data;
    lv_obj_t *screen, *nav, *content, *overlay, *title, *body, *footer;
    lv_obj_t *current[3], *target[3], *temperature_arrow[3], *positions[3], *extrusion_temp,
        *status_label, *time_label, *layer_label, *progress, *percent;
    char route[48], control_tab[24], settings_tab[24], history[80][48];
    int history_count;
    bool select_feeding_cfs;
    int shown_temperature[3];
    bool temperature_shown[3], wifi_joining, file_waiting, nav_dirty;
    char wifi_ssid[100], notice_slot[16];
    char assets[1024], socket[108], selected_slot[16], cfs_unit[16], file[1024], mapping_file[1024];
    char timezone_draft[128];
    char filter[20], number_text[32], number_title[100], number_kind[24], number_key[64];
    char confirm[16384], confirm_message[1024], pending_route[48], keyboard_kind[24];
    char keyboard_value[128], keyboard_title[100], mapping_tool[16];
    cJSON *draft, *mapping;
    double number_min, number_max, step, zoom, plate_x, plate_y;
    bool number_replace, number_open, keyboard_visible, password_visible, dirty, edit_dirty,
        connected, rendered, printer_fault_open, ever_ready, service_link, ever_service;
    uint64_t last_notice, filament_notice_id;
    uint64_t restart_generation, restart_result_after;
    uint32_t restart_started;
    bool restart_waiting, restart_seen_disconnect, restart_expired;
    char restart_action[24];
    char last_fault[4096], last_static_stamp[80];
    cJSON *object_selection;
    lv_color_t accent;
    unsigned long revision;
    bool printing, paused, fixture;
    char command_record[1024];
} NativeScreen;
extern NativeScreen app;
typedef enum {
    CONNECTION_STARTING,
    CONNECTION_READY,
    CONNECTION_LOST,
    CONNECTION_SERVICE_LOST
} ConnectionMode;
cJSON *jget(const cJSON *, const char *);
const char *jstr(const cJSON *, const char *, const char *);
double jnum(const cJSON *, const char *, double);
bool jbool(const cJSON *, const char *, bool);
cJSON *J(const char *);
const char *S(const char *);
double N(const char *);
bool B(const char *);
char *request_json(const char *action, const char *key, const char *value);
void mesh_view(void);
void mesh_fixture(const cJSON *);
bool mesh_action(const char *);
bool mesh_control_key(const char *, char *, size_t);
void platform_test_touch(int, int, bool);
void platform_touch_config(int, int, bool, bool, bool);
void platform_init(bool framebuffer, const char *input, int rotation, bool window);
void platform_pump(void);
bool platform_closed(void);
bool platform_pressed(void);
void platform_cancel_touch(void);
void platform_snapshot(const char *);
bool platform_send(const char *);
char *platform_receive(void);
bool platform_linked(void);
void platform_shutdown(void);
uint64_t platform_millis(void);
void ui_geometry(const char *);
void ui_init(const char *assets, const char *socket);
void ui_data(cJSON *);
void ui_route(const char *, bool);
void ui_render(void);
ConnectionMode ui_connection_mode(void);
void ui_transport_lost(void);
void ui_update(void);
void ui_action(const char *);
void ui_send(cJSON *);
void ui_command(const char *);
void ui_notice(const char *, const char *);
void ui_confirm(const char *, const char *);
void ui_number(const char *, const char *, const char *, double, double, double);
void ui_close(void);
void ui_keyboard(const char *, const char *, const char *);
bool ui_locked(void);
bool ui_unavailable(const char *, const char *);
char *ui_view_signature(void);
bool ui_section_root(void);
void ui_header(const char *);
void modal_message(const char *);
void ui_tabs(bool, const char *);
lv_obj_t *box(lv_obj_t *, int, int, int, int, bool);
lv_obj_t *text(lv_obj_t *, const char *, int, int, int, const lv_font_t *, bool);
lv_obj_t *button(lv_obj_t *, const char *, int, int, int, int, const char *, bool);
void icon(lv_obj_t *, const char *, int, int, int, lv_color_t);
lv_obj_t *button_fixed(lv_obj_t *, const char *, int, int, int, int, const char *, bool);
const char *ui_control_reason(const char *);
bool views_control_key(const char *, char *, size_t);
lv_obj_t *button_with_icon(lv_obj_t *, const char *, const char *, int, int, int, int, const char *,
                           bool);
void row(lv_obj_t *, int, const char *, const char *, const char *, const char *);
void footer(const char *, const char *, const char *, const char *);
void center_message(const char *, const char *, const char *, const char *);
lv_obj_t *scroll_area(int, int);
void views_render(void);
void views_action(const char *);
void view_home(void);
void views_overlay(void);
void views_refresh(void);
const char *slot_label(const char *);
cJSON *slot_record(const char *);
void modal_frame(const char *, int, int, int, int, bool);
void close_modal(void);
#endif
