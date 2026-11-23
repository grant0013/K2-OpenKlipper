#define _POSIX_C_SOURCE 200809L
#include "screen.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
NativeScreen app;
static lv_color_t bg, panel, edge, fg, muted;
static lv_obj_t *number_label, *number_set;
static void number_validate(void);
static char modal_kind[32];
static bool rebuild;
static lv_obj_t *current_scroll;
static char rendered_route[48];
static struct {
    char route[48];
    int offset;
} scroll_memory[80];
static int scroll_count;
static void save_scroll(void) {
    if (!current_scroll || !lv_obj_is_valid(current_scroll))
        return;
    int index = 0;
    for (; index < scroll_count; index++)
        if (!strcmp(scroll_memory[index].route, rendered_route))
            break;
    if (index >= 80)
        return;
    if (index == scroll_count) {
        snprintf(scroll_memory[index].route, 48, "%s", rendered_route);
        scroll_count++;
    }
    scroll_memory[index].offset = lv_obj_get_scroll_y(current_scroll);
}
static void restore_scroll(void) {
    if (!current_scroll)
        return;
    lv_obj_update_layout(current_scroll);
    for (int i = 0; i < scroll_count; i++)
        if (!strcmp(scroll_memory[i].route, app.route)) {
            lv_obj_scroll_to_y(current_scroll, scroll_memory[i].offset, LV_ANIM_OFF);
            break;
        }
}

static void action_event(lv_event_t *event) {
    char *action = lv_event_get_user_data(event);
    if (lv_event_get_code(event) == LV_EVENT_DELETE) {
        free(action);
        return;
    }
    if (lv_event_get_code(event) == LV_EVENT_CLICKED) {
        char *copy = strdup(action);
        ui_action(copy);
        free(copy);
    }
}
static void bind(lv_obj_t *object, const char *action) {
    if (action && *action) {
        char *copy = strdup(action);
        lv_obj_set_user_data(object, copy);
        lv_obj_add_event_cb(object, action_event, LV_EVENT_ALL, copy);
    }
}
lv_obj_t *box(lv_obj_t *parent, int x, int y, int width, int height, bool selected) {
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, width, height);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_radius(o, 12, 0);
    lv_obj_set_style_bg_color(o, selected ? lv_color_mix(app.accent, bg, 38) : panel, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(o, selected ? app.accent : edge, 0);
    lv_obj_set_style_border_width(o, 1, 0);
    return o;
}
lv_obj_t *text(lv_obj_t *parent, const char *value, int x, int y, int width, const lv_font_t *font,
               bool secondary) {
    lv_obj_t *o = lv_label_create(parent);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_width(o, width);
    lv_label_set_long_mode(o, LV_LABEL_LONG_WRAP);
    lv_label_set_text(o, value ? value : "");
    lv_obj_set_style_text_font(o, font, 0);
    lv_obj_set_style_text_color(o, secondary ? muted : fg, 0);
    return o;
}
void icon(lv_obj_t *parent, const char *name, int x, int y, int size, lv_color_t colour) {
    char path[1300], key[100];
    snprintf(key, sizeof(key), "platform.icons.%s", name);
    const char *override = S(key);
    if (*override)
        snprintf(path, sizeof(path), "A:%s", override);
    else
        snprintf(path, sizeof(path), "A:%s/%s-%d.png", app.assets, name, size);
    lv_obj_t *o = lv_img_create(parent);
    lv_img_set_src(o, path);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_style_img_recolor(o, colour, 0);
    lv_obj_set_style_img_recolor_opa(o, *override ? 0 : 255, 0);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_CLICKABLE);
}
/* Connection handling owns the whole screen, independently of page layout. */
ConnectionMode ui_connection_mode(void) {
    if (!app.service_link && app.ever_service)
        return CONNECTION_SERVICE_LOST;
    const char *state = S("status.webhooks.state");
    bool complete = app.connected && !strcmp(state, "ready") &&
                    cJSON_GetArraySize(J("settings")) > 0 && cJSON_GetArraySize(J("heaters")) > 0;
    if (complete && (app.ever_ready || !cJSON_IsObject(J("status.k2_motor_bus")) ||
                     B("status.k2_motor_bus.motors_ready")))
        return CONNECTION_READY;
    return app.ever_ready || !strcmp(state, "error") || !strcmp(state, "shutdown")
               ? CONNECTION_LOST
               : CONNECTION_STARTING;
}
void ui_transport_lost(void) {
    if (!app.connected && !app.service_link)
        return;
    app.connected = false;
    app.service_link = false;
    app.rendered = false;
    close_modal();
    app.dirty = true;
}
static bool recovery_action(const char *action) {
    const char *command = !strcmp(action, "confirmed") ? app.confirm : action;
    if (!strcmp(command, "restart:klipper") || !strcmp(command, "restart:firmware"))
        return true;
    cJSON *request = cJSON_Parse(command);
    const char *name = jstr(request, "action", "");
    bool allowed = (!strcmp(name, "restart") || !strcmp(name, "firmware_restart")) &&
                   jbool(request, "confirm", false);
    cJSON_Delete(request);
    return allowed;
}
const char *ui_control_reason(const char *action) {
    char key[160], path[200];
    if (!action || !views_control_key(action, key, sizeof(key)))
        return "";
    snprintf(path, sizeof(path), "availability.%s", key);
    cJSON *value = J(path);
    return cJSON_IsObject(value) && !jbool(value, "enabled", true)
               ? jstr(value, "reason", "Unavailable")
               : "";
}
static void sync_availability(lv_obj_t *object) {
    const char *action = lv_obj_get_user_data(object);
    if (action) {
        if (*ui_control_reason(action)) {
            lv_obj_add_state(object, LV_STATE_DISABLED | LV_STATE_USER_1);
            lv_obj_set_style_opa(object, 100, 0);
        } else if (lv_obj_has_state(object, LV_STATE_USER_1)) {
            lv_obj_clear_state(object, LV_STATE_DISABLED | LV_STATE_USER_1);
            lv_obj_set_style_opa(object, 255, 0);
        }
    }
    for (unsigned i = 0; i < lv_obj_get_child_cnt(object); i++)
        sync_availability(lv_obj_get_child(object, i));
}
static lv_obj_t *make_button(lv_obj_t *parent, const char *label, int x, int y, int width,
                             int height, const char *action, bool selected, bool large_caption) {
    /* Compact controls grow around their centres. Keep the on-screen keyboard
     * touch targets within its own row pitch; Home retains its reviewed control dimensions. */
    if ((strcmp(app.route, "home") || (app.overlay && parent == app.body)) && height >= 40 &&
        height < 60) {
        int target = app.keyboard_visible ? 52 : 60;
        bool header = parent == app.content && action &&
                      ((!strcmp(action, "back") && label && !strncmp(label, "icon:", 5)) ||
                       !strcmp(action, "refresh_files"));
        if (header) {
            width = height = 56;
            y = 15;
            if (!strcmp(action, "refresh_files"))
                x = 617;
        } else if (height < target) {
            y -= (target - height) / 2;
            height = target;
        }
    }
    lv_obj_t *o = box(parent, x, y, width, height, selected);
    lv_obj_add_flag(o, LV_OBJ_FLAG_CLICKABLE);
    int smallest = width < height ? width : height;
    if (smallest < 52)
        lv_obj_set_ext_click_area(o, (52 - smallest) / 2);
    lv_obj_set_style_bg_color(o, lv_color_mix(app.accent, panel, 55), LV_STATE_PRESSED);
    lv_obj_set_style_border_color(o, app.accent, LV_STATE_PRESSED);
    if (!action || !*action || *ui_control_reason(action)) {
        lv_obj_add_state(o, LV_STATE_DISABLED);
        if (action && *ui_control_reason(action))
            lv_obj_add_state(o, LV_STATE_USER_1);
        lv_obj_set_style_opa(o, 100, 0);
    }
    bind(o, action);
    lv_obj_update_layout(o);
    if (label && strncmp(label, "icon:", 5) == 0)
        icon(o, label + 5, (width - 28) / 2, (height - 28) / 2, 28, selected ? app.accent : fg);
    else {
        const lv_font_t *font = large_caption && height >= 64 ? &font_ui_20 : &font_ui_17;
        lv_obj_t *caption = text(o, label, 8, 0, width - 16, font, false);
        lv_obj_set_style_text_align(caption, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_align(caption, LV_ALIGN_CENTER, 0, 0);
    }
    return o;
}
lv_obj_t *button(lv_obj_t *parent, const char *label, int x, int y, int width, int height,
                 const char *action, bool selected) {
    return make_button(parent, label, x, y, width, height, action, selected, false);
}
lv_obj_t *button_fixed(lv_obj_t *parent, const char *label, int x, int y, int width, int height,
                       const char *action, bool selected) {
    return make_button(parent, label, x, y, width, height, action, selected, true);
}
lv_obj_t *button_with_icon(lv_obj_t *parent, const char *name, const char *caption, int x, int y,
                           int width, int height, const char *action, bool selected) {
    lv_obj_t *b = button(parent, "", x, y, width, height, action, selected);
    const lv_font_t *font = &font_ui_17;
    lv_point_t metrics;
    lv_txt_get_size(&metrics, caption, font, 0, 0, 1000, LV_TEXT_FLAG_NONE);
    width = lv_obj_get_width(b);
    height = lv_obj_get_height(b);
    int icon_size = !strcmp(app.route, "home") ? 20 : 28;
    int group_gap = icon_size + 10;
    int start = (width - metrics.x - group_gap) / 2;
    icon(b, name, start, (height - icon_size) / 2, icon_size, fg);
    lv_obj_t *label =
        text(b, caption, start + group_gap, (height - metrics.y) / 2, metrics.x + 2, font, false);
    (void)label;
    return b;
}
void row(lv_obj_t *parent, int y, const char *label, const char *value, const char *detail,
         const char *action) {
    /* List rows use a consistent touch-sized pitch, including scrolled lists.
     * Their callers own the spacing so adjacent rows cannot overlap. */
    int x = 0, w = 673, h = ui_section_root() && parent == app.content ? 96 : SETTINGS_ROW_HEIGHT;
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    if (action) {
        lv_obj_add_flag(o, LV_OBJ_FLAG_CLICKABLE);
        bind(o, action);
        if (*ui_control_reason(action)) {
            lv_obj_add_state(o, LV_STATE_DISABLED | LV_STATE_USER_1);
            lv_obj_set_style_opa(o, 100, 0);
        }
    }
    bool two = detail && *detail;
    int top = (h - (two ? 46 : 23)) / 2;
    lv_obj_t *title = text(o, label, 16, top, 430, &font_ui_20, false);
    lv_obj_set_height(title, 26);
    lv_label_set_long_mode(title, LV_LABEL_LONG_DOT);
    if (two)
        text(o, detail, 16, top + 28, 584, &font_ui_14, true);
    if (value && !strcmp(value, "›"))
        icon(o, "chevron-right", w - 34, (h - 28) / 2, 28, fg);
    else {
        lv_obj_t *v =
            text(o, value, 460, (h - 23) / 2, w - 460 - SETTINGS_CARD_GAP, &font_ui_18, true);
        lv_obj_set_style_text_align(v, LV_TEXT_ALIGN_RIGHT, 0);
    }
    lv_obj_set_style_bg_color(o, panel, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(o, 12, 0);
    lv_obj_set_style_border_width(o, 0, 0);
    lv_obj_set_style_bg_color(o, lv_color_mix(app.accent, panel, 55), LV_STATE_PRESSED);
}

bool ui_section_root(void) {
    const char *routes[] = {"move",  "heat",     "fans",    "filament",  "tune",    "calibration",
                            "files", "settings", "network", "timelapse", "storage", "system"};
    for (unsigned i = 0; i < sizeof(routes) / sizeof(routes[0]); i++)
        if (!strcmp(app.route, routes[i]))
            return true;
    return false;
}
void ui_header(const char *title) {
    if (ui_section_root()) {
        /* Sidebar and selected tab already identify these sections. */
        if (strcmp(app.route, "files"))
            return;
        text(app.content, title, 0, 23, 602, &font_heading_28, false);
    } else {
        button(app.content, "icon:arrow-left", 0, 19, 44, 44, "back", false);
        text(app.content, title, 64, 23, 602, &font_heading_28, false);
    }
}

void footer(const char *left, const char *la, const char *right, const char *ra) {
    if (left)
        button_fixed(app.content, left, 0, 402, 296, 64, la, false);
    if (right)
        button_fixed(app.content, right, 378, 402, 296, 64, ra, true);
}
lv_obj_t *scroll_area(int top, int bottom) {
    lv_obj_t *o = lv_obj_create(app.content);
    current_scroll = o;
    lv_obj_remove_style_all(o);
    lv_obj_set_pos(o, 0, top);
    lv_obj_set_size(o, 674, bottom - top);
    lv_obj_set_scroll_dir(o, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(o, LV_SCROLLBAR_MODE_AUTO);
    lv_obj_set_style_width(o, 3, LV_PART_SCROLLBAR);
    lv_obj_set_style_bg_color(o, muted, LV_PART_SCROLLBAR);
    lv_obj_set_style_bg_opa(o, 120, LV_PART_SCROLLBAR);
    return o;
}
void center_message(const char *title, const char *detail, const char *label, const char *action) {
    lv_obj_t *t = text(app.content, title, 35, 205, 604, &font_ui_24, false);
    lv_obj_set_style_text_align(t, LV_TEXT_ALIGN_CENTER, 0);
    t = text(app.content, detail, 35, 252, 604, &font_ui_18, true);
    lv_obj_set_style_text_align(t, LV_TEXT_ALIGN_CENTER, 0);
    if (label)
        button(app.content, label, 212, 344, 250, 60, action, true);
}
bool ui_locked(void) {
    return app.printing || app.paused || !app.connected ||
           strcmp(S("status.webhooks.state"), "ready") != 0;
}
bool ui_unavailable(const char *title, const char *verb) {
    if (!ui_locked())
        return false;
    char heading[200], detail[350];
    snprintf(heading, sizeof(heading), "%s unavailable during printing", title);
    snprintf(detail, sizeof(detail), "Finish or stop the print before %s.", verb);
    if (!app.connected) {
        snprintf(heading, sizeof(heading), "Printer disconnected");
        snprintf(detail, sizeof(detail), "Waiting for the connection to Moonraker.");
    } else if (strcmp(S("status.webhooks.state"), "ready")) {
        snprintf(heading, sizeof(heading), "Printer stopped");
        snprintf(detail, sizeof(detail), "Resolve the printer error before %s.", verb);
    }
    center_message(heading, detail, "Back to print", "route:home");
    return true;
}
void ui_tabs(bool settings, const char *selected) {
    const char *control[][3] = {
        {"move", "Move", "move"},  {"heat", "Thermals", "thermometer"},
        {"fans", "Fans", "fan"},   {"filament", "Filament", "cfs"},
        {"tune", "Tune", "gauge"}, {"calibration", "Calibrate", "activity"}};
    const char *options[][3] = {{"settings", "General", "monitor"},
                                {"network", "Network", "wifi"},
                                {"timelapse", "Time-lapse", "video"},
                                {"storage", "Storage", "hard-drive"},
                                {"system", "System", "settings"}};
    int count = settings ? 5 : 6;
    lv_obj_t *line = box(app.content, 0, 78, 674, 2, false);
    lv_obj_set_style_radius(line, 0, 0);
    lv_obj_set_style_bg_color(line, edge, 0);
    lv_obj_clear_flag(line, LV_OBJ_FLAG_CLICKABLE);
    int width = 674 / count;
    for (int i = 0; i < count; i++) {
        const char **item = settings ? options[i] : control[i];
        bool active = !strcmp(item[0], selected);
        char action[100];
        snprintf(action, sizeof(action), "route:%s", item[0]);
        lv_obj_t *tab = button(app.content, "", i * width, 16, width, 64, action, active);
        lv_obj_set_style_radius(tab, 0, 0);
        lv_obj_set_style_border_width(tab, 0, 0);
        lv_obj_set_style_bg_opa(tab, active ? 120 : 0, 0);
        icon(tab,
             !settings && !strcmp(item[0], "filament") && !B("status.k2_cfs.fitted") ? "disc-3"
                                                                                     : item[2],
             (width - 24) / 2, 5, 24, active ? app.accent : fg);
        lv_obj_t *label = text(tab, item[1], 2, 35, width - 4, &font_ui_17, false);
        lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_set_style_text_color(label, active ? app.accent : fg, 0);
        if (active) {
            lv_obj_t *underline = box(tab, 0, 61, width, 3, false);
            lv_obj_clear_flag(underline, LV_OBJ_FLAG_CLICKABLE);
            lv_obj_set_style_radius(underline, 0, 0);
            lv_obj_set_style_border_width(underline, 0, 0);
            lv_obj_set_style_bg_color(underline, app.accent, 0);
        }
    }
}
void close_modal(void) {
    app.filament_notice_id = 0;
    if (app.overlay) {
        lv_obj_del(app.overlay);
        app.overlay = NULL;
    }
    app.number_open = false;
    app.printer_fault_open = false;
    app.keyboard_visible = false;
    modal_kind[0] = 0;
}
void ui_close(void) {
    close_modal();
}
static void outside(lv_event_t *e) {
    if (lv_event_get_target(e) == app.overlay && strcmp(modal_kind, "fault") &&
        strcmp(modal_kind, "operation")) {
        if (!strcmp(app.confirm, "discard"))
            app.notice_slot[0] = 0;
        close_modal();
    }
}
void modal_frame(const char *title, int x, int y, int w, int h, bool critical) {
    close_modal();
    app.overlay = lv_obj_create(app.screen);
    lv_obj_remove_style_all(app.overlay);
    lv_obj_set_size(app.overlay, WIDTH, HEIGHT);
    lv_obj_set_style_bg_color(app.overlay, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(app.overlay, 175, 0);
    lv_obj_clear_flag(app.overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(app.overlay, outside, LV_EVENT_CLICKED, NULL);
    lv_obj_t *frame = box(app.overlay, x, y, w, h, false);
    app.body = frame;
    lv_obj_t *t = text(frame, title, 20, 18, w - 40, &font_heading_28, false);
    lv_obj_set_height(t, 42);
    lv_label_set_long_mode(t, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(t, LV_TEXT_ALIGN_CENTER, 0);
    snprintf(modal_kind, sizeof(modal_kind), "%s", critical ? "fault" : "dialog");
}
void modal_message(const char *message) {
    lv_obj_t *area = lv_obj_create(app.body);
    lv_obj_remove_style_all(area);
    lv_obj_set_pos(area, 24, 72);
    lv_obj_set_size(area, 512, 198);
    lv_obj_set_scroll_dir(area, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(area, LV_SCROLLBAR_MODE_AUTO);
    lv_obj_set_style_width(area, 3, LV_PART_SCROLLBAR);
    lv_obj_set_style_bg_color(area, muted, LV_PART_SCROLLBAR);
    lv_obj_set_style_bg_opa(area, 120, LV_PART_SCROLLBAR);
    lv_point_t size;
    lv_txt_get_size(&size, message, &font_ui_20, 0, 0, 500, LV_TEXT_FLAG_NONE);
    lv_obj_t *label =
        text(area, message, 6, size.y < 198 ? (198 - size.y) / 2 : 0, 500, &font_ui_20, false);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
}
void ui_notice(const char *title, const char *message) {
    modal_frame(title, 120, 60, 560, 360, true);
    modal_message(message);
    button(app.body, "OK", 160, 282, 240, 56, "close", true);
}
void ui_confirm(const char *message, const char *command) {
    snprintf(app.confirm, sizeof(app.confirm), "%s", command);
    snprintf(app.confirm_message, sizeof(app.confirm_message), "%s", message);
    cJSON *parsed = cJSON_Parse(command);
    const char *kind = jstr(parsed, "action", "");
    const char *title =
        !strcmp(kind, "slot_clear")
            ? (!strcmp(jstr(parsed, "slot", ""), "E") ? "Reset spool holder?" : "Reset CFS slot?")
        : !strcmp(kind, "motors")  ? "Unlock motors?"
        : !strcmp(kind, "restart") ? "Restart printer control?"
        : (!strcmp(kind, "delete_file") || !strcmp(kind, "delete_recording")) ? "Delete file?"
        : !strcmp(kind, "firmware_restart") ? "Restart controller firmware?"
                                            : "Confirm";
    modal_frame(title, 120, 60, 560, 360, false);
    modal_message(message);
    button(app.body, "Cancel", 24, 282, 230, 56, "close", false);
    const char *label =
        (!strcmp(kind, "delete_file") || !strcmp(kind, "delete_recording")) ? "OK" : "Continue";
    if (!strcmp(command, "discard"))
        label = "Discard changes";
    else if (!strcmp(kind, "slot_clear"))
        label = "Reset";
    else if (!strcmp(kind, "motors"))
        label = "Unlock motors";
    else if (!strcmp(kind, "cancel"))
        label = "Stop print";
    else if (!strcmp(kind, "restart"))
        label = "Restart Klipper";
    else if (!strcmp(kind, "firmware_restart"))
        label = "Firmware restart";
    else if (!strcmp(kind, "usb_format"))
        label = "Format USB";
    else if (!strcmp(kind, "calibrate"))
        label = "Start calibration";
    else if (!strcmp(kind, "save_config"))
        label = "Save and restart";
    button(app.body, label, 306, 282, 230, 56, "confirmed", true);
    cJSON_Delete(parsed);
}
void ui_send(cJSON *value) {
    char *raw = cJSON_PrintUnformatted(value);
    cJSON_Delete(value);
    ui_command(raw);
    free(raw);
}
/* A fixture never reaches the printer. Apply only local list edits and the
 * time-lapse switch so desktop previews can exercise these UI workflows. */
static void fixture_action(const char *command) {
    cJSON *request = cJSON_Parse(command);
    const char *action = jstr(request, "action", "");
    bool recording = !strcmp(action, "delete_recording");
    if ((recording || !strcmp(action, "delete_file")) && jbool(request, "confirm", false)) {
        cJSON *rows = J(recording ? "platform.recordings" : "files");
        const char *path = jstr(request, "path", "");
        for (int i = cJSON_GetArraySize(rows) - 1; i >= 0; i--)
            if (!strcmp(jstr(cJSON_GetArrayItem(rows, i), "path", ""), path))
                cJSON_DeleteItemFromArray(rows, i);
        app.dirty = true;
    } else if (!strcmp(action, "preference") &&
               (!strcmp(jstr(request, "key", ""), "cfs_insertion_read") ||
                !strcmp(jstr(request, "key", ""), "cfs_startup_read") ||
                !strcmp(jstr(request, "key", ""), "cfs_auto_refill")) &&
               cJSON_IsBool(cJSON_GetObjectItemCaseSensitive(request, "value"))) {
        const char *key = jstr(request, "key", "");
        cJSON *prefs = J("prefs");
        if (cJSON_IsObject(prefs)) {
            cJSON_DeleteItemFromObjectCaseSensitive(prefs, key);
            cJSON_AddBoolToObject(prefs, key, jbool(request, "value", false));
        }
        app.dirty = true;
    } else if (!strcmp(action, "timelapse") &&
               cJSON_IsBool(cJSON_GetObjectItemCaseSensitive(request, "enabled"))) {
        cJSON *settings = J("platform.timelapse");
        if (cJSON_IsObject(settings))
            cJSON_ReplaceItemInObjectCaseSensitive(
                settings, "enabled", cJSON_CreateBool(jbool(request, "enabled", false)));
        app.dirty = true;
    }
    cJSON_Delete(request);
}
void ui_command(const char *command) {
    if (ui_connection_mode() != CONNECTION_READY &&
        (ui_connection_mode() != CONNECTION_LOST || command[0] != '{' ||
         !recovery_action(command) || !app.service_link))
        return;
    if (*ui_control_reason(command))
        return;
    if (app.fixture) {
        if (*app.command_record) {
            FILE *file = fopen(app.command_record, "a");
            if (file) {
                fprintf(file, "%s\n", command);
                fclose(file);
            }
        }
        fixture_action(command);
        return;
    }
    if (!platform_send(command))
        ui_transport_lost();
}
static void number_draw(void) {
    modal_frame(app.number_title, 150, 22, 500, 436, false);
    app.number_open = true;
    lv_obj_t *window = box(app.body, 14, 56, 470, 73, false);
    lv_obj_set_style_bg_color(window, bg, 0);
    const char *unit =
        !strcmp(app.number_kind, "position")                                           ? "mm"
        : !strcmp(app.number_kind, "temperature") || !strcmp(app.number_kind, "draft") ? "°C"
                                                                                       : "%";
    lv_obj_t *hint = text(window, !strcmp(app.number_kind, "position") ? "Position" : "Target", 0,
                          8, 468, &font_ui_14, true);
    lv_obj_set_style_text_align(hint, LV_TEXT_ALIGN_CENTER, 0);
    char with_unit[60];
    snprintf(with_unit, sizeof(with_unit), "%s %s", app.number_text, unit);
    number_label = text(window, with_unit, 8, 32, 452, &font_ui_25, false);
    lv_obj_set_style_text_align(number_label, LV_TEXT_ALIGN_CENTER, 0);
    char limits[100];
    snprintf(limits, sizeof(limits), "%g – %g", app.number_min, app.number_max);
    lv_obj_t *t = text(app.body, limits, 20, 135, 460, &font_ui_14, true);
    lv_obj_set_style_text_align(t, LV_TEXT_ALIGN_CENTER, 0);
    const char *keys[] = {"7", "8", "9", "⌫",   "4", "5", "6", "Cancel",
                          "1", "2", "3", "Set", "0", ".", "±"};
    for (int i = 0; i < 15; i++) {
        char action[60];
        snprintf(action, sizeof(action), "number:%s", keys[i]);
        lv_obj_t *key_button = button(app.body,
                                      i == 3 ? "icon:delete"
                                      : i == 11 && !strcmp(app.number_kind, "position") ? "Move"
                                                                                        : keys[i],
                                      14 + (i % 4) * 119, 162 + (i / 4) * 66, 113,
                                      i == 11 ? 126 : 60, action, i == 11);
        if (i != 3) {
            lv_obj_t *caption = lv_obj_get_child(key_button, 0);
            lv_obj_set_style_text_font(caption,
                                       i == 7    ? &font_ui_18
                                       : i == 11 ? &font_ui_20
                                                 : &font_ui_24,
                                       0);
            lv_obj_align(caption, LV_ALIGN_CENTER, 0, 0);
        }
        if (i == 11)
            number_set = key_button;
        if (i == 14 && app.number_min >= 0) {
            lv_obj_add_state(key_button, LV_STATE_DISABLED);
            lv_obj_set_style_opa(key_button, 100, 0);
        }
    }
}
void ui_number(const char *title, const char *kind, const char *key, double value, double min,
               double max) {
    snprintf(app.number_title, sizeof(app.number_title), "%s", title);
    snprintf(app.number_kind, sizeof(app.number_kind), "%s", kind);
    snprintf(app.number_key, sizeof(app.number_key), "%s", key);
    snprintf(app.number_text, sizeof(app.number_text), "%g", value);
    app.number_min = min;
    app.number_max = max;
    app.number_replace = true;
    number_draw();
}
static void number_key(const char *key) {
    if (!strcmp(key, "Cancel")) {
        close_modal();
        return;
    }
    if (!strcmp(key, "Set")) {
        char *end = NULL;
        double value = strtod(app.number_text, &end);
        if (!*app.number_text || *end || !isfinite(value) || value < app.number_min ||
            value > app.number_max) {
            ui_notice("Value out of range", "Use a value within the configured limits.");
            return;
        }
        close_modal();
        if (!strcmp(app.number_kind, "draft")) {
            cJSON_ReplaceItemInObject(app.draft, "nozzle_temp", cJSON_CreateNumber(value));
            app.edit_dirty = true;
            app.dirty = true;
            return;
        }
        cJSON *r = cJSON_CreateObject();
        cJSON_AddStringToObject(r, "action", app.number_kind);
        cJSON_AddStringToObject(r,
                                !strcmp(app.number_kind, "temperature") ? "heater"
                                : !strcmp(app.number_kind, "position")  ? "axis"
                                                                        : "key",
                                app.number_key);
        cJSON_AddNumberToObject(r, "value", value);
        ui_send(r);
        return;
    }
    size_t n = strlen(app.number_text);
    if (!strcmp(key, "⌫")) {
        if (n)
            app.number_text[n - 1] = 0;
        app.number_replace = false;
    } else if (!strcmp(key, "±")) {
        if (app.number_min < 0) {
            if (app.number_text[0] == '-')
                memmove(app.number_text, app.number_text + 1, n);
            else if (n < sizeof(app.number_text) - 2) {
                memmove(app.number_text + 1, app.number_text, n + 1);
                app.number_text[0] = '-';
            }
        }
    } else if (n < 12) {
        if (app.number_replace) {
            app.number_text[0] = 0;
            n = 0;
            app.number_replace = false;
        }
        if (strcmp(key, ".") || !strchr(app.number_text, '.'))
            strncat(app.number_text, key, sizeof(app.number_text) - strlen(app.number_text) - 1);
    }
    char shown[60];
    snprintf(shown, sizeof(shown), "%s %s", app.number_text,
             !strcmp(app.number_kind, "position")                                           ? "mm"
             : !strcmp(app.number_kind, "temperature") || !strcmp(app.number_kind, "draft") ? "°C"
                                                                                            : "%");
    lv_label_set_text(number_label, shown);
    number_validate();
}
static void number_validate(void) {
    char *end = NULL;
    double value = strtod(app.number_text, &end);
    bool valid = *app.number_text && !*end && isfinite(value) && value >= app.number_min &&
                 value <= app.number_max;
    if (valid && !*ui_control_reason("number:Set")) {
        lv_obj_clear_state(number_set, LV_STATE_DISABLED);
        lv_obj_set_style_opa(number_set, 255, 0);
    } else {
        lv_obj_add_state(number_set, LV_STATE_DISABLED);
        lv_obj_set_style_opa(number_set, 100, 0);
    }
}

void ui_route(const char *route, bool back) {
    bool editing = !strcmp(app.route, "edit") || !strcmp(app.route, "materials") ||
                   !strcmp(app.route, "colors");
    bool saving = false;
    cJSON *operation;
    cJSON_ArrayForEach(operation, J("operations")) {
        saving |= !strcmp(jstr(operation, "action", ""), "slot_save") &&
                  !strcmp(jstr(operation, "slot", ""), app.selected_slot);
    }
    if (app.edit_dirty && editing && !saving && strcmp(route, "edit") &&
        strcmp(route, "materials") && strcmp(route, "colors")) {
        snprintf(app.pending_route, sizeof(app.pending_route), "%s", route);
        ui_confirm("Discard unsaved filament changes?", "discard");
        return;
    }
    if (!strcmp(route, "filament") && strcmp(app.route, "filament") && !editing)
        app.select_feeding_cfs = true;
    close_modal();
    if (!back && strcmp(route, app.route) && app.history_count < 80)
        snprintf(app.history[app.history_count++], 48, "%s", app.route);
    if (!strcmp(route, "timezone") && strcmp(app.route, "timezone")) {
        const char *current = *S("prefs.timezone") ? S("prefs.timezone") : S("platform.timezone");
        snprintf(app.timezone_draft, sizeof(app.timezone_draft), "%s", current);
    }
    snprintf(app.route, sizeof(app.route), "%s", route);
    if (!strcmp(route, "files")) {
        cJSON *r = cJSON_CreateObject();
        cJSON_AddStringToObject(r, "action", "files");
        cJSON_AddStringToObject(r, "source", "gcodes");
        ui_send(r);
    }
    const char *controls[] = {"move", "heat", "fans", "filament", "tune", "calibration"};
    const char *settings[] = {"settings", "network", "timelapse", "storage", "system"};
    for (int i = 0; i < 6; i++)
        if (!strcmp(route, controls[i]))
            snprintf(app.control_tab, sizeof(app.control_tab), "%s", route);
    for (int i = 0; i < 5; i++)
        if (!strcmp(route, settings[i]))
            snprintf(app.settings_tab, sizeof(app.settings_tab), "%s", route);
    app.dirty = true;
}
void ui_action(const char *action) {
    ConnectionMode mode = ui_connection_mode();
    if (mode == CONNECTION_STARTING || mode == CONNECTION_SERVICE_LOST)
        return;
    if (mode == CONNECTION_LOST && strcmp(action, "close") && !recovery_action(action))
        return;
    if (*ui_control_reason(action))
        return;
    if (!app.fixture) {
        if (!platform_send("{\"action\":\"activity\"}")) {
            ui_transport_lost();
            return;
        }
        if (B("platform.asleep"))
            return;
    }
    if (!strncmp(action, "route:", 6)) {
        ui_route(action + 6, false);
        return;
    }
    if (!strcmp(action, "back")) {
        if (app.history_count)
            ui_route(app.history[--app.history_count], true);
        else
            ui_route("home", true);
        return;
    }
    if (!strcmp(action, "close")) {
        if (!strcmp(app.confirm, "discard"))
            app.notice_slot[0] = 0;
        close_modal();
        return;
    }
    if (!strcmp(action, "confirmed")) {
        char command[sizeof(app.confirm)];
        snprintf(command, sizeof(command), "%s", app.confirm);
        close_modal();
        if (!strcmp(command, "discard")) {
            app.edit_dirty = false;
            ui_route(app.pending_route, false);
        } else
            ui_command(command);
        return;
    }
    if (!strncmp(action, "number:", 7)) {
        number_key(action + 7);
        return;
    }
    if (action[0] == '{') {
        ui_command(action);
        return;
    }
    views_action(action);
}
void ui_init(const char *assets, const char *socket) {
    memset(&app, 0, sizeof(app));
    snprintf(app.assets, sizeof(app.assets), "%s", assets);
    snprintf(app.socket, sizeof(app.socket), "%s", socket);
    snprintf(app.route, sizeof(app.route), "home");
    snprintf(app.control_tab, sizeof(app.control_tab), "move");
    snprintf(app.settings_tab, sizeof(app.settings_tab), "settings");
    app.selected_slot[0] = 0;
    snprintf(app.filter, sizeof(app.filter), "All");
    app.step = 10;
    app.zoom = 1;
    app.mapping = cJSON_CreateObject();
    app.object_selection = cJSON_CreateArray();
    app.accent = lv_color_hex(0x3d8bfd);
    bg = lv_color_hex(0x07090c);
    panel = lv_color_hex(0x151a22);
    edge = lv_color_hex(0x343e4b);
    fg = lv_color_hex(0xf0f3f7);
    muted = lv_color_hex(0xa4aebb);
    app.screen = lv_scr_act();
    lv_obj_remove_style_all(app.screen);
    lv_obj_set_style_bg_color(app.screen, bg, 0);
    lv_obj_set_style_bg_opa(app.screen, 255, 0);
    lv_obj_set_style_radius(app.screen, 0, 0);
    lv_obj_set_style_clip_corner(app.screen, false, 0);
    lv_obj_set_style_border_color(app.screen, edge, 0);
    lv_obj_set_style_border_width(app.screen, 0, 0);
    lv_obj_clear_flag(app.screen, LV_OBJ_FLAG_SCROLLABLE);
    app.content = lv_obj_create(app.screen);
    lv_obj_remove_style_all(app.content);
    lv_obj_set_pos(app.content, LEFT, 0);
    lv_obj_set_size(app.content, 678, 480);
    lv_obj_clear_flag(app.content, LV_OBJ_FLAG_SCROLLABLE);
    app.dirty = true;
}
/* Home retains value widgets between snapshots. Rebuild its static artwork
 * and controls only when one of their actual inputs changes. */
static void home_state(char *buffer, size_t size) {
    snprintf(buffer, size, "%s|%g|%d|%d|%g|%g|%g|%g|%s|%s", S("status.k2_cfs.active_slot"),
             N("status.hark_cfs_slots.revision"), B("status.k2_cfs.online"),
             B("status.filament_switch_sensor filament_sensor.filament_detected"),
             N("status.output_pin chamber_led.value"), N("status.fan.speed"),
             N("status.fan_generic auxiliary_fan.speed"), N("status.fan_generic exhaust_fan.speed"),
             S("platform.printer_image"), S("platform.thumbnail"));
}
static void move_state(char *buffer, size_t size) {
    bool enabled = false;
    cJSON *stepper;
    cJSON_ArrayForEach(stepper, J("status.stepper_enable.steppers")) enabled |=
        cJSON_IsTrue(stepper);
    snprintf(buffer, size, "%s|%d|%d|%g|%d", S("status.toolhead.homed_axes"), enabled,
             B("status.extruder.can_extrude") &&
                 N("status.extruder.temperature") >=
                     jnum(app.data, "settings.extruder.min_extrude_temp", 170),
             jnum(app.data, "settings.extruder.max_extrude_only_distance", 50),
             cJSON_IsObject(J("settings.extruder")));
}
void ui_data(cJSON *data) {
    if (!cJSON_IsObject(data)) {
        cJSON_Delete(data);
        return;
    }
    static uint64_t result_ids[32];
    static unsigned result_cursor;
    ConnectionMode previous_connection = ui_connection_mode();
    bool previous_link = app.service_link;
    char *previous_view = ui_view_signature();
    char *previous_policy = cJSON_PrintUnformatted(J("availability"));
    char *previous_operations = cJSON_PrintUnformatted(J("operations"));
    char *previous_operation = cJSON_PrintUnformatted(J("operation"));
    bool session_changed = strcmp(S("session"), jstr(data, "session", "")) != 0;
    char previous_home[1024];
    home_state(previous_home, sizeof(previous_home));
    char previous_move[256];
    move_state(previous_move, sizeof(previous_move));
    bool old_asleep = B("platform.asleep");
    bool files_busy = B("files_refresh.busy");
    bool wifi_busy = B("wifi.busy");
    bool old_print = app.printing, old_pause = app.paused;
    char previous_file[1024];
    snprintf(previous_file, sizeof(previous_file), "%s", S("status.print_stats.filename"));
    int old_heaters = cJSON_GetArraySize(J("heaters"));
    bool old_fitted = B("status.k2_cfs.fitted");
    cJSON_Delete(app.data);
    app.data = data;
    if (files_busy != B("files_refresh.busy") || wifi_busy != B("wifi.busy"))
        app.nav_dirty = true;
    if (app.file_waiting && !strcmp(S("file"), app.file) && B("file_read.busy"))
        app.file_waiting = false;
    if (app.filament_notice_id) {
        bool pending = false;
        cJSON *notice;
        cJSON_ArrayForEach(notice, J("notices")) pending |=
            (uint64_t)jnum(notice, "id", 0) == app.filament_notice_id;
        if (!pending) {
            close_modal();
            app.dirty = true;
        }
    }
    app.connected = B("connected");
    app.service_link = true;
    app.ever_service = true;
    ConnectionMode connection = ui_connection_mode();
    if (connection == CONNECTION_READY)
        app.ever_ready = true;
    if (connection != previous_connection || !previous_link) {
        /* The backend retains unacknowledged notices across reconnects. */
        app.last_notice = 0;
        app.rendered = false;
        close_modal();
        app.dirty = true;
    }
    char *new_view = ui_view_signature();
    if (strcmp(previous_view ? previous_view : "", new_view ? new_view : ""))
        app.dirty = true;
    free(previous_view);
    free(new_view);
    char *new_policy = cJSON_PrintUnformatted(J("availability"));
    char *new_operations = cJSON_PrintUnformatted(J("operations"));
    char *new_operation = cJSON_PrintUnformatted(J("operation"));
    bool policy_changed =
        strcmp(previous_policy ? previous_policy : "", new_policy ? new_policy : "") != 0;
    if (policy_changed) {
        sync_availability(app.screen);
        if (app.number_open)
            number_validate();
    }
    if (policy_changed ||
        strcmp(previous_operations ? previous_operations : "",
               new_operations ? new_operations : "") ||
        strcmp(previous_operation ? previous_operation : "", new_operation ? new_operation : ""))
        app.nav_dirty = true;
    if (policy_changed && !strcmp(app.route, "home"))
        app.dirty = true;
    if (policy_changed && !strcmp(app.route, "fans"))
        app.dirty = true;
    free(previous_policy);
    free(previous_operations);
    free(previous_operation);
    free(new_policy);
    free(new_operations);
    free(new_operation);
    char current_home[1024];
    home_state(current_home, sizeof(current_home));
    char current_move[256];
    move_state(current_move, sizeof(current_move));
    if (strcmp(previous_home, current_home) || old_asleep != B("platform.asleep") ||
        (!strcmp(app.route, "move") && strcmp(previous_move, current_move)))
        app.dirty = true;
    app.printing = !strcmp(S("status.print_stats.state"), "printing");
    app.paused = !strcmp(S("status.print_stats.state"), "paused");
    const char *accent = jstr(data, "prefs.accent", "#3d8bfd");
    lv_color_t previous = app.accent;
    if (strlen(accent) == 7)
        app.accent = lv_color_hex(strtoul(accent + 1, NULL, 16));
    app.revision++;
    if (!B("status.k2_cfs.fitted"))
        snprintf(app.selected_slot, sizeof(app.selected_slot), "E");
    if (!*app.selected_slot) {
        const char *active = S("status.k2_cfs.active_slot"), *selected = *active ? active : "E";
        if (!*active && B("status.k2_cfs.fitted")) {
            cJSON *record;
            cJSON_ArrayForEach(record, J("slots")) {
                const char *slot = jstr(record, "slot", "");
                if (*slot && strcmp(slot, "E") && jbool(record, "loaded", false)) {
                    selected = slot;
                    break;
                }
            }
        }
        snprintf(app.selected_slot, sizeof(app.selected_slot), "%s", selected);
    }
    if (old_print != app.printing || old_pause != app.paused ||
        old_heaters != cJSON_GetArraySize(J("heaters")) ||
        old_fitted != B("status.k2_cfs.fitted") || previous.full != app.accent.full)
        app.dirty = true;
    if (session_changed) {
        app.dirty = true;
        memset(result_ids, 0, sizeof(result_ids));
        result_cursor = 0;
        app.last_notice = 0;
        app.last_fault[0] = 0;
        memset(app.temperature_shown, 0, sizeof(app.temperature_shown));
        app.wifi_joining = false;
    }
    if ((!old_print && !old_pause && (app.printing || app.paused)) ||
        strcmp(previous_file, S("status.print_stats.filename"))) {
        cJSON_Delete(app.object_selection);
        app.object_selection = cJSON_CreateArray();
        app.zoom = 1;
        app.plate_x = app.plate_y = 0;
    }
    cJSON *results = J("results"), *result;
    int result_count = cJSON_IsArray(results) ? cJSON_GetArraySize(results) : 1;
    for (int i = 0; i < result_count; i++) {
        result = cJSON_IsArray(results) ? cJSON_GetArrayItem(results, i) : J("result");
        uint64_t id = (uint64_t)jnum(result, "id", 0);
        bool seen = !id;
        for (unsigned n = 0; n < 32; n++)
            seen |= result_ids[n] == id;
        if (seen)
            continue;
        result_ids[result_cursor++ % 32] = id;
        const char *result_action = jstr(result, "action", "");
        if (ui_connection_mode() != CONNECTION_READY)
            app.dirty = true;
        if (!strcmp(result_action, "file") && !strcmp(jstr(result, "path", ""), app.file)) {
            app.file_waiting = false;
            app.dirty = true;
        }
        if (!strcmp(result_action, "wifi_join")) {
            app.wifi_joining = false;
            app.dirty = true;
        }
        if (jbool(result, "success", false)) {
            const char *action = result_action;
            if (!strcmp(action, "slot_clear")) {
                cJSON_Delete(app.draft);
                app.draft = NULL;
                app.edit_dirty = false;
                if (!strcmp(app.route, "edit"))
                    ui_route("filament", true);
            }
            if (!strcmp(action, "slot_save")) {
                const char *slot = jstr(result, "slot", "");
                if (!*slot || !strcmp(slot, app.selected_slot)) {
                    app.edit_dirty = false;
                    if (!strcmp(app.route, "edit"))
                        ui_route("filament", true);
                }
            } else if (!strcmp(action, "delete_file")) {
                if (!strcmp(app.route, "file") || !strncmp(app.route, "mapping", 7))
                    ui_route("files", true);
            } else if (!strcmp(action, "start"))
                ui_route("home", true);
        }
    }
    if (!app.overlay)
        ui_update();
    if (app.rendered)
        views_overlay();
}

/* Explicit presentation rules: unlisted instant setters have no activity badge.
 * Page-level progress owns network tasks and the history view owns all details. */
typedef struct {
    const char *action, *caption;
} ActivitySpec;
static const ActivitySpec activities[] = {{"home", "Homing"},
                                          {"jog", "Moving"},
                                          {"position", "Moving"},
                                          {"extrude", "Moving\nfilament"},
                                          {"load", "Loading\nfilament"},
                                          {"unload", "Unloading\nfilament"},
                                          {"calibrate", "Calibrating"},
                                          {"calibration_result", "Saving\ncalibration"},
                                          {"save_config", "Saving\nsettings"},
                                          {"slot_save", "Saving\nfilament"},
                                          {"files", "Refreshing\nfiles"},
                                          {"file", "Reading\nfile"},
                                          {"recordings", "Reading\nvideos"},
                                          {"delete_file", "Deleting\nfile"},
                                          {"delete_recording", "Deleting\nvideo"},
                                          {"export", "Exporting\nfiles"},
                                          {"usb_eject", "Ejecting\nUSB"},
                                          {"usb_format", "Formatting\nUSB"},
                                          {"start", "Starting\nprint"},
                                          {"pause", "Pausing"},
                                          {"resume", "Resuming"},
                                          {"cancel", "Stopping\nprint"},
                                          {"plr", "Recovering\nprint"},
                                          {"restart", "Restarting"},
                                          {"firmware_restart", "Restarting"},
                                          {"wifi_scan", "Scanning\nWi-Fi"},
                                          {"wifi_join", "Connecting\nWi-Fi"}};
static const ActivitySpec *activity_spec(const char *action) {
    bool network = (!strcmp(app.route, "wifi") && !strcmp(action, "wifi_join")) ||
                   (!strcmp(app.route, "networks") && !strcmp(action, "wifi_scan"));
    bool print_home = !strcmp(app.route, "home") && (app.printing || app.paused);
    if (!strcmp(app.route, "history") || network ||
        (print_home &&
         (!strcmp(action, "start") || !strcmp(action, "pause") || !strcmp(action, "resume") ||
          !strcmp(action, "cancel") || !strcmp(action, "plr"))))
        return NULL;
    for (unsigned i = 0; i < sizeof(activities) / sizeof(activities[0]); i++)
        if (!strcmp(action, activities[i].action))
            return &activities[i];
    return NULL;
}
static int activity_priority(const ActivitySpec *spec) {
    bool files = !strcmp(app.route, "files") || !strcmp(app.route, "file") ||
                 !strncmp(app.route, "mapping", 7);
    bool storage = !strcmp(app.route, "recordings") || !strcmp(app.route, "storage") ||
                   !strcmp(app.route, "usb");
    const char *action = spec->action;
    if ((files &&
         (!strcmp(action, "files") || !strcmp(action, "file") || !strcmp(action, "delete_file"))) ||
        (storage && (!strcmp(action, "recordings") || !strcmp(action, "delete_recording") ||
                     !strcmp(action, "export") || !strncmp(action, "usb_", 4))))
        return 3;
    return !strcmp(action, "files") || !strcmp(action, "recordings") ? 1 : 2;
}
static const ActivitySpec *sidebar_activity(void) {
    const ActivitySpec *selected = B("files_refresh.busy") ? activity_spec("files") : NULL;
    if (B("wifi.busy")) {
        const char *message = S("wifi.message");
        const ActivitySpec *network = !strncmp(message, "Scanning", 8) ? activity_spec("wifi_scan")
                                      : !strncmp(message, "Connecting", 10)
                                          ? activity_spec("wifi_join")
                                          : NULL;
        if (network && (!selected || activity_priority(network) > activity_priority(selected)))
            selected = network;
    }
    cJSON *operations = J("operations"), *op;
    cJSON_ArrayForEach(op, operations) {
        const ActivitySpec *spec = activity_spec(jstr(op, "action", ""));
        if (spec && (!selected || activity_priority(spec) > activity_priority(selected)))
            selected = spec;
    }
    /* Older snapshots may only have the single-operation mirror. */
    if (!cJSON_GetArraySize(operations)) {
        const ActivitySpec *spec = activity_spec(jstr(J("operation"), "action", ""));
        if (spec && (!selected || activity_priority(spec) > activity_priority(selected)))
            selected = spec;
    }
    return selected;
}

static void navigation(void) {
    app.nav_dirty = false;
    if (app.nav)
        lv_obj_del(app.nav);
    app.nav = lv_obj_create(app.screen);
    lv_obj_remove_style_all(app.nav);
    lv_obj_set_pos(app.nav, 0, 0);
    lv_obj_set_size(app.nav, 84, 480);
    lv_obj_set_style_border_width(app.nav, 1, 0);
    lv_obj_set_style_border_side(app.nav, LV_BORDER_SIDE_RIGHT, 0);
    lv_obj_set_style_border_color(app.nav, edge, 0);
    const char *names[] = {"house", "printer-3d", "folder-open", "settings"};
    const char *routes[] = {"home", app.control_tab, "files", app.settings_tab};
    int current = (!strcmp(app.route, "home") || !strcmp(app.route, "objects")) ? 0
                  : (!strcmp(app.route, "files") || !strcmp(app.route, "file") ||
                     !strncmp(app.route, "mapping", 7))
                      ? 2
                  : (!strcmp(app.route, app.control_tab) || !strcmp(app.route, "edit") ||
                     !strcmp(app.route, "materials") || !strcmp(app.route, "colors"))
                      ? 1
                      : 3;
    for (int i = 0; i < 4; i++) {
        char action[70];
        snprintf(action, sizeof(action), "route:%s", routes[i]);
        lv_obj_t *b = button(app.nav, "", 8, 48 + i * 82, 67, 70, action, i == current);
        lv_obj_set_style_border_width(b, 0, 0);
        if (i != current)
            lv_obj_set_style_bg_opa(b, 0, 0);
        char nav_icon[80];
        snprintf(nav_icon, sizeof(nav_icon), "nav-%s", names[i]);
        icon(b, nav_icon, 5, 6, 56, i == current ? app.accent : fg);
    }
    const ActivitySpec *activity = sidebar_activity();
    if (!activity)
        return;
    lv_obj_t *control = button_fixed(app.nav, "", 4, 391, 76, 70, "route:history", false);
    lv_obj_set_style_bg_opa(control, 0, 0);
    lv_obj_set_style_border_width(control, 0, 0);
    lv_obj_t *spinner = lv_spinner_create(control, 1000, 60);
    lv_obj_set_pos(spinner, 26, 6);
    lv_obj_set_size(spinner, 24, 24);
    lv_obj_set_style_arc_width(spinner, 3, LV_PART_MAIN);
    lv_obj_set_style_arc_width(spinner, 3, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(spinner, edge, LV_PART_MAIN);
    lv_obj_set_style_arc_color(spinner, app.accent, LV_PART_INDICATOR);
    lv_obj_clear_flag(spinner, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_t *label = text(control, activity->caption, 2, 36, 72, &font_ui_12, false);
    lv_obj_set_height(label, 32);
    lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
}

static void connection_center(lv_obj_t *label) {
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
}
static void connection_screen(ConnectionMode mode) {
    bool starting = mode == CONNECTION_STARTING;
    bool service_lost = mode == CONNECTION_SERVICE_LOST;
    lv_obj_t *group = box(app.content, 0, 0, 720, 1, false);
    lv_obj_clear_flag(group, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_bg_opa(group, 0, 0);
    lv_obj_set_style_border_width(group, 0, 0);
    lv_obj_set_height(group, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(group, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(group, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(group, 20, 0);
    lv_obj_t *spinner = lv_spinner_create(group, 1000, 60);
    lv_obj_set_size(spinner, 64, 64);
    lv_obj_set_style_arc_width(spinner, 5, LV_PART_MAIN);
    lv_obj_set_style_arc_width(spinner, 5, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(spinner, edge, LV_PART_MAIN);
    lv_obj_set_style_arc_color(spinner, app.accent, LV_PART_INDICATOR);
    lv_obj_clear_flag(spinner, LV_OBJ_FLAG_CLICKABLE);
    connection_center(text(group,
                           starting       ? "Printer is starting up"
                           : service_lost ? "Cannot connect to touchscreen service"
                                          : "Printer connection lost",
                           0, 0, 720, &font_ui_30, false));
    const char *state = S("status.webhooks.state");
    bool fatal = !strcmp(state, "error") || !strcmp(state, "shutdown");
    const char *detail =
        service_lost ? "Waiting for the local touchscreen service to reconnect…"
        : starting   ? "Waiting for printer control to become ready…"
        : fatal && *S("status.webhooks.state_message") ? S("status.webhooks.state_message")
        : !app.connected ? "Waiting for the connection to printer control to return."
        : *S("error")    ? S("error")
                         : "Printer control is restarting. Waiting for reconnection…";
    const char *result_action = S("result.action");
    if (!starting && !service_lost && !B("result.success") && *S("result.error") &&
        (!strcmp(result_action, "restart") || !strcmp(result_action, "firmware_restart")))
        detail = S("result.error");
    lv_obj_t *message = text(group, detail, 0, 0, 680, &font_ui_20, true);
    connection_center(message);
    lv_obj_set_style_max_height(message, 100, 0);
    lv_label_set_long_mode(message, LV_LABEL_LONG_DOT);
    lv_obj_update_layout(group);
    lv_obj_align(group, LV_ALIGN_CENTER, 0, starting || service_lost ? 0 : -45);
    if (starting || service_lost)
        return;
    button_fixed(app.content, "Restart Klipper", 74, 352, 312, 70, "restart:klipper", false);
    button_fixed(app.content, "Firmware restart", 414, 352, 312, 70, "restart:firmware", false);
}

void ui_render(void) {
    save_scroll();
    current_scroll = NULL;
    snprintf(rendered_route, 48, "%s", app.route);
    app.dirty = false;
    rebuild = false;
    memset(app.current, 0, sizeof(app.current));
    memset(app.target, 0, sizeof(app.target));
    memset(app.temperature_arrow, 0, sizeof(app.temperature_arrow));
    memset(app.positions, 0, sizeof(app.positions));
    app.extrusion_temp = NULL;
    app.status_label = app.time_label = app.layer_label = app.progress = app.percent = NULL;
    lv_obj_clean(app.content);
    ConnectionMode connection = ui_connection_mode();
    if (connection != CONNECTION_READY) {
        if (app.nav) {
            lv_obj_del(app.nav);
            app.nav = NULL;
        }
        lv_obj_set_pos(app.content, 0, 0);
        lv_obj_set_size(app.content, 800, 480);
        connection_screen(connection);
        app.rendered = true;
        lv_obj_update_layout(app.screen);
        return;
    }
    lv_obj_set_pos(app.content, LEFT, 0);
    lv_obj_set_size(app.content, 678, 480);
    navigation();
    if (!strcmp(app.route, "home"))
        view_home();
    else
        views_render();
    restore_scroll();
    ui_update();
    app.rendered = true;
    views_overlay();
    lv_obj_update_layout(app.screen);
}
void ui_update(void) {
    if (ui_connection_mode() == CONNECTION_READY)
        views_refresh();
}
void ui_tick(void) {
    bool pressing = platform_pressed();
    if (app.nav_dirty && !pressing && !app.overlay && ui_connection_mode() == CONNECTION_READY) {
        navigation();
        app.nav_dirty = false;
    }
    if ((app.dirty || rebuild) && !pressing && !app.overlay)
        ui_render();
}

static void geometry_walk(lv_obj_t *object, cJSON *array) {
    const char *action = lv_obj_get_user_data(object);
    if (action) {
        lv_area_t area;
        lv_obj_get_coords(object, &area);
        cJSON *item = cJSON_CreateObject();
        cJSON_AddStringToObject(item, "action", action);
        cJSON_AddBoolToObject(item, "enabled", !lv_obj_has_state(object, LV_STATE_DISABLED));
        cJSON *rect = cJSON_AddArrayToObject(item, "rect");
        cJSON_AddItemToArray(rect, cJSON_CreateNumber(area.x1));
        cJSON_AddItemToArray(rect, cJSON_CreateNumber(area.y1));
        cJSON_AddItemToArray(rect, cJSON_CreateNumber(lv_obj_get_width(object)));
        cJSON_AddItemToArray(rect, cJSON_CreateNumber(lv_obj_get_height(object)));
        cJSON_AddItemToArray(array, item);
    }
    for (uint32_t i = 0; i < lv_obj_get_child_cnt(object); i++)
        geometry_walk(lv_obj_get_child(object, i), array);
}
void ui_geometry(const char *path) {
    cJSON *array = cJSON_CreateArray();
    geometry_walk(app.screen, array);
    char *json = cJSON_Print(array);
    FILE *file = fopen(path, "w");
    if (!file) {
        perror(path);
        exit(1);
    }
    fputs(json, file);
    fclose(file);
    free(json);
    cJSON_Delete(array);
}
