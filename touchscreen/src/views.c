#define _POSIX_C_SOURCE 200809L
#include "screen.h"
#include <math.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
enum { HOME_MAIN = 470, HOME_SIDE_X = 488, HOME_SIDE_W = 190 };
static bool calibrate_before;
/* LVGL invalidates and reallocates even when the displayed text is unchanged. */
static void label_update(lv_obj_t *label, const char *value) {
    if (strcmp(lv_label_get_text(label), value))
        lv_label_set_text(label, value);
}

static bool has(const char *path) {
    return J(path) != NULL && !cJSON_IsNull(J(path));
}
static lv_color_t colour(const char *hex) {
    if (*hex == '#')
        hex++;
    return lv_color_hex(strtoul(hex, NULL, 16));
}
static void right(lv_obj_t *o) {
    lv_obj_set_style_text_align(o, LV_TEXT_ALIGN_RIGHT, 0);
}
static void centered(lv_obj_t *o) {
    lv_obj_set_style_text_align(o, LV_TEXT_ALIGN_CENTER, 0);
}
static void simple(const char *action) {
    cJSON *r = cJSON_CreateObject();
    cJSON_AddStringToObject(r, "action", action);
    ui_send(r);
}
static cJSON *request(const char *action) {
    cJSON *r = cJSON_CreateObject();
    cJSON_AddStringToObject(r, "action", action);
    return r;
}
static char *encode(cJSON *r) {
    char *s = cJSON_PrintUnformatted(r);
    cJSON_Delete(r);
    return s;
}
static void confirmed(const char *message, cJSON *r) {
    cJSON_AddBoolToObject(r, "confirm", true);
    char *s = encode(r);
    ui_confirm(message, s);
    free(s);
}
static void image(lv_obj_t *parent, const char *path, int x, int y) {
    if (!path || !*path)
        return;
    char source[1400];
    snprintf(source, sizeof(source), "A:%s", path);
    lv_obj_t *o = lv_img_create(parent);
    lv_img_set_src(o, source);
    lv_obj_set_pos(o, x, y);
}
/* Fit cached artwork without stretching; invalid files use the caller's fallback. */
static bool image_fit(lv_obj_t *parent, const char *path, int x, int y, int width, int height) {
    if (!path || !*path)
        return false;
    char source[1400];
    snprintf(source, sizeof(source), "A:%s", path);
    lv_img_header_t info;
    if (lv_img_decoder_get_info(source, &info) != LV_RES_OK || !info.w || !info.h)
        return false;
    int zoom = height * 256 / info.h;
    if (zoom > width * 256 / info.w)
        zoom = width * 256 / info.w;
    if (zoom < 1)
        return false;
    lv_obj_t *preview = lv_img_create(parent);
    lv_img_set_src(preview, source);
    lv_img_set_pivot(preview, 0, 0);
    lv_img_set_zoom(preview, zoom);
    lv_obj_set_pos(preview, x + (width - info.w * zoom / 256) / 2,
                   y + (height - info.h * zoom / 256) / 2);
    return true;
}
static void home_image(const char *path, int top, int height) {
    image_fit(app.content, path, 12, top + 12, HOME_MAIN - 24, height - 24);
}
cJSON *slot_record(const char *slot) {
    cJSON *rec;
    cJSON_ArrayForEach(rec, J("slots")) if (!strcmp(jstr(rec, "slot", ""), slot)) return rec;
    return NULL;
}
const char *slot_label(const char *slot) {
    if (!strcmp(slot, "E"))
        return "Spool holder";
    static char label[48];
    if (strlen(slot) > 1)
        snprintf(label, sizeof(label), "CFS %.*s · %c", (int)strlen(slot) - 1, slot,
                 slot[strlen(slot) - 1]);
    else
        snprintf(label, sizeof(label), "1%s", slot);
    return label;
}
static bool fitted(void) {
    return jbool(app.data, "status.k2_cfs.fitted", false);
}
static void spool_widget(lv_obj_t *parent, int x, int y, int size, const char *hex) {
    lv_color_t ring = colour(hex);
    if (ring.ch.red < 25 && ring.ch.green < 25 && ring.ch.blue < 25)
        ring = colour("2d3238");
    lv_obj_t *outer = box(parent, x, y, size, size, false);
    lv_obj_set_style_bg_opa(outer, 0, 0);
    lv_obj_clear_flag(outer, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_radius(outer, size / 2, 0);
    lv_obj_set_style_border_width(outer, 5, 0);
    lv_obj_set_style_border_color(outer, ring, 0);
    int inner_size = size / 2, inset = (size - inner_size) / 2 - 5;
    lv_obj_t *inner = box(outer, inset, inset, inner_size, inner_size, false);
    lv_obj_set_style_bg_opa(inner, 0, 0);
    lv_obj_clear_flag(inner, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_radius(inner, inner_size / 2, 0);
    lv_obj_set_style_border_width(inner, 2, 0);
    lv_obj_set_style_border_color(inner, ring, 0);
}
/* All CFS representations consume the same backend-owned transition state. */
static const char *filament_activity_label(void) {
    const char *action = S("filament_activity.action");
    return !strcmp(action, "load") ? "Loading…" : !strcmp(action, "unload") ? "Unloading…" : "";
}
static bool filament_slot_busy(const char *slot) {
    return *slot && *filament_activity_label() && !strcmp(slot, S("filament_activity.slot"));
}
static lv_obj_t *filament_spinner(lv_obj_t *parent, int x, int y, int size) {
    lv_obj_t *spinner = lv_spinner_create(parent, 1000, 60);
    lv_obj_set_pos(spinner, x, y);
    lv_obj_set_size(spinner, size, size);
    lv_obj_set_style_arc_width(spinner, 2, LV_PART_MAIN);
    lv_obj_set_style_arc_width(spinner, 2, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(spinner, colour("343e4b"), LV_PART_MAIN);
    lv_obj_set_style_arc_color(spinner, app.accent, LV_PART_INDICATOR);
    lv_obj_clear_flag(spinner, LV_OBJ_FLAG_CLICKABLE);
    return spinner;
}
/* The extruder sensor confirms the path; spool presence is a separate state.
 * An old active_slot after unloading must never imply filament in the nozzle. */
static bool feeding_slot(const char *slot) {
    if (filament_slot_busy(slot))
        return false;
    if (!B("status.filament_switch_sensor filament_sensor.filament_detected"))
        return false;
    const char *active = S("status.k2_cfs.active_slot");
    if (!strcmp(slot, "E"))
        return !*active || !fitted();
    return fitted() && B("status.k2_cfs.online") && !strcmp(slot, active);
}
static bool known_slot(cJSON *rec) {
    const char *slot = jstr(rec, "slot", "");
    const char *hex = jstr(rec, "color", "");
    if (*hex == '#')
        hex++;
    if (!jbool(rec, "loaded", false) || !*jstr(rec, "type", "") || strlen(hex) != 6)
        return false;
    for (int i = 0; i < 6; i++)
        if (!isxdigit((unsigned char)hex[i]))
            return false;
    if (!strcmp(slot, "E"))
        return B("status.filament_switch_sensor filament_sensor.filament_detected") &&
               !*S("status.k2_cfs.active_slot");
    return fitted() && B("status.k2_cfs.online");
}
static void slot_unit(const char *slot, char *unit, size_t size) {
    size_t n = strlen(slot);
    if (n <= 1)
        snprintf(unit, size, "1");
    else
        snprintf(unit, size, "%.*s", (int)n - 1, slot);
}
static void home_cfs(int top, int height) {
    lv_obj_t *tile = button(app.content, "", HOME_SIDE_X, top, HOME_SIDE_W, height,
                            "home_filament_strip", false);
    icon(tile, "cfs", 13, (height - 28) / 2, 28, colour("a4aebb"));
    if (!B("status.k2_cfs.online")) {
        text(tile, "Offline", 62, (height - 20) / 2, 116, &font_ui_18, true);
        return;
    }
    char unit[16] = "", groups[16][16] = {{0}};
    int units = 0;
    const char *active = S("status.k2_cfs.active_slot");
    const char *busy_slot = S("filament_activity.slot");
    bool busy = *filament_activity_label() && strcmp(busy_slot, "E");
    if (busy && *busy_slot)
        slot_unit(busy_slot, unit, sizeof(unit));
    else if (*active && feeding_slot(active))
        slot_unit(active, unit, sizeof(unit));
    cJSON *record;
    cJSON_ArrayForEach(record, J("slots")) {
        const char *slot = jstr(record, "slot", "");
        if (!*slot || !strcmp(slot, "E"))
            continue;
        char group[16];
        slot_unit(slot, group, sizeof(group));
        if (!*unit)
            snprintf(unit, sizeof(unit), "%s", group);
        bool seen = false;
        for (int i = 0; i < units; i++)
            seen |= !strcmp(groups[i], group);
        if (!seen && units < 16)
            snprintf(groups[units++], sizeof(groups[0]), "%s", group);
    }
    if (units > 1) {
        char label[48];
        snprintf(label, sizeof(label), "CFS %s · %d units", unit, units);
        text(tile, label, 62, height / 2 - 34, 118, &font_ui_12, true);
    }
    int drawn = 0, total = 0;
    cJSON_ArrayForEach(record, J("slots")) {
        const char *slot = jstr(record, "slot", "");
        if (!*slot || !strcmp(slot, "E"))
            continue;
        char group[16];
        slot_unit(slot, group, sizeof(group));
        if (strcmp(group, unit))
            continue;
        total++;
        if (drawn >= 4)
            continue;
        int x = 62 + drawn++ * 28, y = (height - 22) / 2;
        bool present = jbool(record, "loaded", false);
        lv_obj_t *dot = box(tile, x, y, 22, 22, false);
        lv_obj_clear_flag(dot, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_color(dot, colour(jstr(record, "color", "828b98")), 0);
        lv_obj_set_style_bg_opa(dot, present ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_color(dot, colour(feeding_slot(slot) ? "f0f3f7" : "596371"), 0);
        lv_obj_set_style_border_width(dot, feeding_slot(slot) ? 3 : 1, 0);
        if (filament_slot_busy(slot))
            filament_spinner(tile, x - 3, y - 3, 28);
        else if (feeding_slot(slot))
            icon(tile, "nozzle", x + 3, y + 24, 16, colour("f0f3f7"));
    }
    if (!total)
        text(tile, "No slots", 62, (height - 20) / 2, 116, &font_ui_18, true);
}
static void home_spool(int top, int height) {
    cJSON *record = slot_record("E");
    bool known = known_slot(record);
    lv_obj_t *tile =
        button(app.content, "", HOME_SIDE_X, top, HOME_SIDE_W, height, "route:filament", false);
    spool_widget(tile, 13, (height - 36) / 2, 36,
                 known ? jstr(record, "color", "828b98") : "596371");
    text(tile, "Spool holder", 62, height / 2 - 23, 118, &font_ui_16, false);
    if (filament_slot_busy("E"))
        filament_spinner(tile, 9, (height - 44) / 2, 44);
    lv_obj_t *details = text(tile, known ? jstr(record, "type", "") : "Set details", 62,
                             height / 2 + 3, 118, &font_ui_16, true);
    lv_label_set_long_mode(details, LV_LABEL_LONG_DOT);
    lv_obj_set_height(details, 23);
}
static void file_title(char *out, size_t size, const char *path) {
    const char *base = strrchr(path, '/');
    snprintf(out, size, "%s", base ? base + 1 : path);
    char *extension = strrchr(out, '.');
    if (extension && (!strcasecmp(extension, ".gcode") || !strcasecmp(extension, ".gco") ||
                      !strcasecmp(extension, ".g")))
        *extension = '\0';
}
static void preview_placeholder(lv_obj_t *parent, int x, int y, int width, int height, bool busy) {
    if (busy) {
        lv_obj_t *spinner = lv_spinner_create(parent, 1000, 60);
        lv_obj_set_pos(spinner, x + (width - 52) / 2, y + (height - 52) / 2);
        lv_obj_set_size(spinner, 52, 52);
        lv_obj_set_style_arc_width(spinner, 4, LV_PART_MAIN);
        lv_obj_set_style_arc_width(spinner, 4, LV_PART_INDICATOR);
        lv_obj_set_style_arc_color(spinner, colour("343e4b"), LV_PART_MAIN);
        lv_obj_set_style_arc_color(spinner, app.accent, LV_PART_INDICATOR);
        lv_obj_clear_flag(spinner, LV_OBJ_FLAG_CLICKABLE);
    } else
        icon(parent, "folder-open", x + (width - 96) / 2, y + (height - 96) / 2, 96,
             colour("a4aebb"));
}
void view_home(void) {
    bool starting = *S("platform.starting_file") != 0;
    bool printing =
        starting || app.printing || app.paused || !strcmp(S("status.print_stats.state"), "error");
    if (printing) {
        lv_obj_t *preview = box(app.content, 0, 19, HOME_MAIN, 260, false);
        lv_obj_set_style_bg_color(preview, colour("30343c"), 0);
        if (!image_fit(app.content, S("platform.thumbnail"), 12, 43, HOME_MAIN - 24, 196))
            preview_placeholder(app.content, 12, 43, HOME_MAIN - 24, 196,
                                B("platform.print_preview_busy"));
        app.time_label = text(app.content, "", 14, 247, 230, &font_ui_17, false);
        app.layer_label = text(app.content, "", 248, 249, 208, &font_ui_14, true);
        right(app.layer_label);
        lv_obj_t *status = button(app.content, "", 0, 289, HOME_MAIN, 80, "route:history", false);
        lv_obj_set_style_bg_opa(status, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(status, 0, 0);
        app.status_label = text(status, "", 0, 0, HOME_MAIN - 52, &font_ui_20, false);
        lv_label_set_long_mode(app.status_label, LV_LABEL_LONG_DOT);
        lv_obj_set_style_max_height(app.status_label, 56, 0);
        icon(status, "history", HOME_MAIN - 36, 26, 28, colour("a4aebb"));
        app.percent = text(app.content, "", 0, 416, 46, &font_ui_20, false);
        app.progress = lv_bar_create(app.content);
        lv_obj_set_pos(app.progress, 58, 426);
        lv_obj_set_size(app.progress, 224, 7);
        lv_obj_set_style_bg_color(app.progress, colour("29313d"), LV_PART_MAIN);
        lv_obj_set_style_bg_color(app.progress, app.accent, LV_PART_INDICATOR);
        lv_obj_set_style_bg_opa(app.progress, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(app.progress, LV_OPA_COVER, LV_PART_INDICATOR);
        lv_obj_set_style_radius(app.progress, 4, LV_PART_MAIN);
        lv_obj_set_style_radius(app.progress, 4, LV_PART_INDICATOR);
        button(app.content, app.paused ? "icon:play" : "icon:pause", 294, 391, 52, 70,
               starting     ? NULL
               : app.paused ? "{\"action\":\"resume\"}"
                            : "{\"action\":\"pause\"}",
               true);
        button(app.content, "icon:skip-object", 356, 391, 52, 70, starting ? NULL : "route:objects",
               false);
        button(app.content, "icon:square", 418, 391, 52, 70, starting ? NULL : "cancel_print",
               false);
    } else {
        const char *art = S("platform.printer_image");
        if (*art)
            home_image(art, 19, 362);
        else
            icon(app.content, "printer-3d", (HOME_MAIN - 44) / 2, 178, 44, colour("a4aebb"));
        const char *blocked = ui_control_reason("open_files");
        const char *caption = strstr(blocked, "Calibration") ? blocked : "Print a file";
        button_with_icon(app.content, "folder-open", caption, 0, 391, HOME_MAIN, 70, "open_files",
                         !*blocked);
    }
    int count = cJSON_GetArraySize(J("heaters"));
    if (count > 3)
        count = 3;
    int rows = count + 1;
    int height = rows ? (350 - (rows - 1) * 10) / rows : 0;
    for (int i = 0; i < count; i++) {
        cJSON *heater = cJSON_GetArrayItem(J("heaters"), i);
        char act[100];
        snprintf(act, sizeof(act), "temperature:%s", jstr(heater, "key", ""));
        lv_obj_t *tile = button(app.content, "", HOME_SIDE_X, 19 + i * (height + 10), HOME_SIDE_W,
                                height, act, false);
        const char *key = jstr(heater, "key", "");
        icon(tile,
             !strcmp(key, "extruder")     ? "nozzle"
             : !strcmp(key, "heater_bed") ? "bed"
                                          : "chamber",
             13, (height - 28) / 2, 28, colour("a4aebb"));
        app.current[i] = text(tile, "", 0, (height - 32) / 2, 54, &font_ui_24, false);
        app.target[i] = text(tile, "", 0, (height - 32) / 2, 54, &font_ui_24, true);
        icon(tile, "arrow-right", 0, (height - 16) / 2, 16, colour("a4aebb"));
        app.temperature_arrow[i] = lv_obj_get_child(tile, -1);
    }
    if (fitted())
        home_cfs(19 + count * (height + 10), height);
    else
        home_spool(19 + count * (height + 10), height);
    lv_obj_t *light =
        button(app.content, "", HOME_SIDE_X, 391, 90, 70, "{\"action\":\"light\"}", false);
    icon(light, B("status.output_pin chamber_led.value") ? "lightbulb" : "lightbulb-off", 0, 0, 28,
         colour("a4aebb"));
    lv_obj_align(lv_obj_get_child(light, -1), LV_ALIGN_CENTER, 0, 0);
    lv_obj_t *fans = button(app.content, "", HOME_SIDE_X + 100, 391, 90, 70, "home_fans", false);
    if (*S("platform.icons.home_fan"))
        image(fans, S("platform.icons.home_fan"), 0, 0);
    else
        icon(fans, "fan", 0, 0, 28, colour("a4aebb"));
    lv_obj_align(lv_obj_get_child(fans, -1), LV_ALIGN_CENTER, 0, 0);
}
void views_refresh(void) {
    char value[180];
    if (app.extrusion_temp) {
        snprintf(value, sizeof(value), "E %.0f°", N("status.extruder.temperature"));
        label_update(app.extrusion_temp, value);
    }
    for (int i = 0; i < 3; i++) {
        cJSON *heater = cJSON_GetArrayItem(J("heaters"), i);
        double target = jnum(heater, "target", 0);
        if (app.current[i]) {
            double measured = jnum(heater, "current", 0);
            if (!app.temperature_shown[i] || fabs(measured - app.shown_temperature[i]) >= .75) {
                app.shown_temperature[i] = (int)floor(measured + .5);
                app.temperature_shown[i] = true;
            }
            snprintf(value, sizeof(value), "%d°", app.shown_temperature[i]);
            label_update(app.current[i], value);
        }
        if (app.target[i]) {
            snprintf(value, sizeof(value), "%.0f°", target);
            label_update(app.target[i], value);
        }
        if (app.temperature_arrow[i]) {
            bool approaching = target > 0 && fabs(jnum(heater, "current", 0) - target) > 1.0;
            bool thermals = !strcmp(app.route, "heat");
            const lv_font_t *font = thermals ? &font_ui_30 : &font_ui_24;
            lv_point_t current_size, target_size;
            lv_txt_get_size(&current_size, lv_label_get_text(app.current[i]), font, 0, 0,
                            LV_COORD_MAX, LV_TEXT_FLAG_NONE);
            lv_txt_get_size(&target_size, lv_label_get_text(app.target[i]), font, 0, 0,
                            LV_COORD_MAX, LV_TEXT_FLAG_NONE);
            int width = current_size.x + (approaching ? 28 + target_size.x : 0);
            int x = thermals ? (lv_obj_get_width(lv_obj_get_parent(app.current[i])) - width) / 2
                             : 46 + (132 - width) / 2;
            lv_obj_set_width(app.current[i], current_size.x);
            lv_obj_set_x(app.current[i], x);
            if (approaching) {
                lv_obj_clear_flag(app.target[i], LV_OBJ_FLAG_HIDDEN);
                lv_obj_clear_flag(app.temperature_arrow[i], LV_OBJ_FLAG_HIDDEN);
                lv_obj_set_x(app.temperature_arrow[i], x + current_size.x + 6);
                lv_obj_set_width(app.target[i], target_size.x);
                lv_obj_set_x(app.target[i], x + current_size.x + 28);
            } else {
                lv_obj_add_flag(app.target[i], LV_OBJ_FLAG_HIDDEN);
                lv_obj_add_flag(app.temperature_arrow[i], LV_OBJ_FLAG_HIDDEN);
            }
        }
        if (app.positions[i]) {
            cJSON *p = cJSON_GetArrayItem(J("status.gcode_move.gcode_position"), i);
            snprintf(value, sizeof(value), "%.2f", cJSON_IsNumber(p) ? p->valuedouble : 0);
            label_update(app.positions[i],
                              strchr(S("status.toolhead.homed_axes"), "xyz"[i]) ? value : "—");
        }
    }
    if (app.status_label) {
        const char *state = *S("platform.starting_file") ? "Starting print…"
                            : app.paused                 ? "Paused"
                            : app.printing ? (*S("print_stage") ? S("print_stage") : "Printing")
                                           : "Stopped";
        lv_point_t size;
        lv_txt_get_size(&size, state, &font_ui_20, 0, 0, HOME_MAIN - 52, LV_TEXT_FLAG_NONE);
        lv_obj_set_height(app.status_label, size.y > 56 ? 56 : size.y);
        label_update(app.status_label, state);
        lv_obj_update_layout(app.status_label);
        lv_obj_align(app.status_label, LV_ALIGN_LEFT_MID, 0, 0);
    }
    if (app.time_label) {
        double estimated = N("platform.estimated_time"),
               elapsed = N("status.print_stats.print_duration");
        if (estimated > 0 && !*S("platform.starting_file")) {
            int minutes = (int)ceil(fmax(0, estimated - elapsed) / 60);
            if (minutes >= 60)
                snprintf(value, sizeof(value), "%d h %d min remaining", minutes / 60, minutes % 60);
            else
                snprintf(value, sizeof(value), "%d min remaining", minutes);
        } else
            value[0] = 0;
        label_update(app.time_label, value);
    }
    if (app.layer_label) {
        double total = N("status.print_stats.info.total_layer");
        double current = N("status.print_stats.info.current_layer");
        if (*S("platform.starting_file") || total <= 0)
            total = N("platform.layer_count");
        if (*S("platform.starting_file"))
            current = 0;
        if (total > 0 && current > 0)
            snprintf(value, sizeof(value), "Layer %.0f / %.0f", current, total);
        else if (total > 0)
            snprintf(value, sizeof(value), "%.0f layers", total);
        else
            value[0] = 0;
        label_update(app.layer_label, value);
    }
    if (app.progress) {
        double p = *S("platform.starting_file") ? 0 : N("status.virtual_sdcard.progress") * 100;
        lv_bar_set_value(app.progress, (int)p, LV_ANIM_OFF);
        snprintf(value, sizeof(value), "%.0f%%", p);
        label_update(app.percent, value);
    }
}
static void move_icon(lv_obj_t *control, const char *name, const char *label) {
    icon(control, name, 18, label ? 6 : 20, 36, colour("f0f3f7"));
    if (label)
        centered(text(control, label, 0, 48, 72, &font_ui_14, false));
}
static bool motors_locked(void) {
    cJSON *stepper;
    cJSON_ArrayForEach(stepper, J("status.stepper_enable.steppers")) {
        const char *name = stepper->string;
        if (name && !strncmp(name, "stepper_", 8) && strchr("xyz", name[8]) && name[8] &&
            cJSON_IsTrue(stepper))
            return true;
    }
    return false;
}
static bool axis_homed(char axis) {
    const char *homed = S("status.toolhead.homed_axes");
    return axis == 'z' ? strchr(homed, 'z') != NULL : strchr(homed, 'x') && strchr(homed, 'y');
}
static void move_view(void) {
    ui_tabs(false, "move");
    if (ui_unavailable("Movement", "moving the axes")) {
        if (has("settings.bed_mesh"))
            button_fixed(app.content, "Bed mesh", 212, 410, 250, 60, "route:mesh", false);
        return;
    }
    const char *icons[] = {"arrow-up", "arrow-left", "house", "arrow-right", "arrow-down"};
    int xs[] = {80, 0, 80, 160, 80}, ys[] = {108, 192, 192, 192, 276};
    const char *actions[] = {"jog:Y:1", "jog:X:-1", "{\"action\":\"home\",\"axis\":\"XY\"}",
                             "jog:X:1", "jog:Y:-1"};
    for (int i = 0; i < 5; i++) {
        bool enabled = i == 2 || axis_homed('x');
        lv_obj_t *control =
            button(app.content, "", xs[i], ys[i], 72, 76, enabled ? actions[i] : NULL, false);
        move_icon(control, icons[i], i == 2 ? "XY" : NULL);
    }
    for (int i = 0; i < 3; i++) {
        const char *names[] = {"arrow-up", "house", "arrow-down"};
        const char *actions[] = {"jog:Z:1", "{\"action\":\"home\",\"axis\":\"Z\"}", "jog:Z:-1"};
        bool enabled = i == 1 || axis_homed('z');
        lv_obj_t *control =
            button(app.content, "", 252, 108 + i * 84, 72, 76, enabled ? actions[i] : NULL, false);
        move_icon(control, names[i], i == 1 ? "Z" : NULL);
    }
    if (has("settings.extruder")) {
        bool hot = B("status.extruder.can_extrude") &&
                   N("status.extruder.temperature") >=
                       jnum(app.data, "settings.extruder.min_extrude_temp", 170);
        double max_distance = jnum(app.data, "settings.extruder.max_extrude_only_distance", 50);
        bool enabled = hot && app.step <= max_distance;
        lv_obj_t *retract =
            button(app.content, "", 344, 108, 72, 76, enabled ? "extrude:-1" : NULL, false);
        move_icon(retract, "arrow-up", NULL);
        lv_obj_t *nozzle = button(app.content, "", 344, 192, 72, 76, "temperature:extruder", false);
        icon(nozzle, "extruder", 18, 6, 36, colour("f0f3f7"));
        app.extrusion_temp = text(nozzle, "", 0, 48, 72, &font_ui_14, false);
        centered(app.extrusion_temp);
        lv_obj_t *extrude =
            button(app.content, "", 344, 276, 72, 76, enabled ? "extrude:1" : NULL, false);
        move_icon(extrude, "arrow-down", NULL);
    }
    centered(text(app.content, "mm", 0, 368, 416, &font_ui_14, true));
    double steps[] = {.1, 1, 10, 50, 100};
    for (int i = 0; i < 5; i++) {
        char caption[20], action[40];
        snprintf(caption, sizeof(caption), "%g", steps[i]);
        snprintf(action, sizeof(action), "step:%g", steps[i]);
        button(app.content, caption, i * 85, 398, 76, 64, action, app.step == steps[i]);
    }
    for (int i = 0; i < 3; i++) {
        char action[30], axis[2] = {"XYZ"[i], 0};
        snprintf(action, sizeof(action), "position:%c", "XYZ"[i]);
        lv_obj_t *field = button(app.content, "", 452, 108 + i * 84, 222, 76,
                                 axis_homed("xyz"[i]) ? action : NULL, false);
        text(field, axis, 12, 24, 24, &font_ui_20, true);
        app.positions[i] = text(field, "—", 40, 23, 146, &font_ui_24, false);
        centered(app.positions[i]);
        icon(field, "pencil", 194, 30, 16, colour("a4aebb"));
    }
    bool enabled = motors_locked();
    lv_obj_t *mesh = button(app.content, "", 452, 398, 105, 64,
                            has("settings.bed_mesh") ? "route:mesh" : NULL, false);
    icon(mesh, "bed-mesh", 34, 14, 36, colour("f0f3f7"));
    lv_obj_t *motor = button_fixed(app.content, "", 569, 398, 105, 64, "motor_toggle", false);
    icon(motor, enabled ? "motor-off" : "motor-on", 34, 14, 36, colour("f0f3f7"));
}
static void heat_view(void) {
    ui_tabs(false, "heat");
    int n = cJSON_GetArraySize(J("heaters"));
    if (!n) {
        center_message("Heaters unavailable", "Waiting for configured heater capabilities.", NULL,
                       NULL);
        return;
    }
    if (n > 3)
        n = 3;
    int width = (674 - (n - 1) * 12) / n;
    for (int i = 0; i < n; i++) {
        cJSON *heater = cJSON_GetArrayItem(J("heaters"), i);
        const char *key = jstr(heater, "key", "");
        char action[100];
        snprintf(action, sizeof(action), "temperature:%s", key);
        lv_obj_t *tile = button(app.content, "", i * (width + 12), 100, width, 280, action, false);
        const char *title = jstr(heater, "label", "");
        lv_point_t title_size;
        lv_txt_get_size(&title_size, title, &font_ui_20, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
        int start = (width - 28 - 10 - title_size.x) / 2;
        icon(tile,
             !strcmp(key, "extruder")     ? "nozzle"
             : !strcmp(key, "heater_bed") ? "bed"
                                          : "chamber",
             start, 49, 28, colour("a4aebb"));
        text(tile, title, start + 38, 52, title_size.x + 2, &font_ui_20, false);
        app.current[i] = text(tile, "", 0, 122, 100, &font_ui_30, false);
        app.target[i] = text(tile, "", 0, 122, 100, &font_ui_30, true);
        icon(tile, "arrow-right", 0, 132, 16, colour("a4aebb"));
        app.temperature_arrow[i] = lv_obj_get_child(tile, -1);
    }
    if (!app.printing && !app.paused)
        button_fixed(app.content, "Turn heaters off", 189, 402, 296, 64,
                     "{\"action\":\"heaters_off\"}", false);
}
static int fan_draft[3];
static uint64_t fan_draft_until[3];
static int fan_index(const char *name) {
    return !strcmp(name, "part") ? 0 : !strcmp(name, "side") ? 1 : 2;
}
typedef struct {
    char *name;
    lv_obj_t *output;
} FanControl;
static void slider_event(lv_event_t *e) {
    lv_obj_t *slider = lv_event_get_target(e);
    FanControl *control = lv_event_get_user_data(e);
    if (lv_event_get_code(e) == LV_EVENT_DELETE) {
        free(control->name);
        free(control);
        return;
    }
    if (lv_event_get_code(e) == LV_EVENT_VALUE_CHANGED) {
        lv_obj_t *label = control->output;
        if (label) {
            char value[20];
            snprintf(value, sizeof(value), "%d%%", lv_slider_get_value(slider));
            label_update(label, value);
        }
    }
    if (lv_event_get_code(e) == LV_EVENT_RELEASED) {
        int index = fan_index(control->name);
        fan_draft[index] = lv_slider_get_value(slider);
        fan_draft_until[index] = platform_millis() + 1500;
        cJSON *r = request("fan");
        cJSON_AddStringToObject(r, "fan", control->name);
        cJSON_AddNumberToObject(r, "value", lv_slider_get_value(slider));
        ui_send(r);
    }
}
static void fans_view(void) {
    ui_tabs(false, "fans");
    const char *labels[] = {"Part cooling", "Side fan", "Chamber exhaust"};
    const char *objects[] = {"fan", "fan_generic auxiliary_fan", "fan_generic exhaust_fan"};
    const char *ids[] = {"part", "side", "exhaust"};
    int rowno = 0;
    for (int i = 0; i < 3; i++) {
        cJSON *settings = J("settings");
        if (!cJSON_GetObjectItemCaseSensitive(settings, objects[i]))
            continue;
        int y = 116 + rowno++ * 124;
        text(app.content, labels[i], 0, y, 480, &font_ui_18, false);
        char path[150], value[30];
        snprintf(path, sizeof(path), "status.%s.speed", objects[i]);
        int speed = (int)round(N(path) * 100);
        if (fan_draft_until[i] > platform_millis())
            speed = fan_draft[i];
        snprintf(value, sizeof(value), "%d%%", speed);
        lv_obj_t *output = text(app.content, value, 550, y, 124, &font_ui_16, false);
        right(output);
        lv_obj_t *slider = lv_slider_create(app.content);
        lv_obj_set_pos(slider, 22, y + 36);
        lv_obj_set_size(slider, 629, 8);
        char availability_action[64];
        snprintf(availability_action, sizeof(availability_action), "fan:%s", ids[i]);
        if (*ui_control_reason(availability_action)) {
            lv_obj_add_state(slider, LV_STATE_DISABLED);
            lv_obj_set_style_opa(slider, 100, 0);
        }
        lv_slider_set_range(slider, 0, 100);
        lv_slider_set_value(slider, speed, LV_ANIM_OFF);
        lv_obj_set_style_bg_color(slider, colour("343e4b"), LV_PART_MAIN);
        lv_obj_set_style_bg_color(slider, app.accent, LV_PART_INDICATOR);
        lv_obj_set_style_bg_color(slider, colour("f0f3f7"), LV_PART_KNOB);
        lv_obj_set_style_bg_opa(slider, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_bg_opa(slider, LV_OPA_COVER, LV_PART_INDICATOR);
        lv_obj_set_style_bg_opa(slider, LV_OPA_COVER, LV_PART_KNOB);
        lv_obj_set_style_radius(slider, 4, LV_PART_MAIN);
        lv_obj_set_style_radius(slider, 4, LV_PART_INDICATOR);
        lv_obj_set_style_radius(slider, LV_RADIUS_CIRCLE, LV_PART_KNOB);
        lv_obj_set_style_pad_all(slider, 12, LV_PART_KNOB);
        lv_obj_set_ext_click_area(slider, 22);
        lv_obj_add_flag(slider, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_clear_flag(slider, LV_OBJ_FLAG_ADV_HITTEST);
        FanControl *control = calloc(1, sizeof(*control));
        control->name = strdup(ids[i]);
        control->output = output;
        lv_obj_add_event_cb(slider, slider_event, LV_EVENT_ALL, control);
    }
}
static void filament_view(void) {
    if (*app.notice_slot) {
        snprintf(app.selected_slot, sizeof(app.selected_slot), "%s", app.notice_slot);
        slot_unit(app.notice_slot, app.cfs_unit, sizeof(app.cfs_unit));
        app.select_feeding_cfs = false;
        app.notice_slot[0] = 0;
    }
    ui_tabs(false, "filament");
    if ((!app.connected || strcmp(S("status.webhooks.state"), "ready")) &&
        ui_unavailable("Filament controls", "loading, retracting or editing filament"))
        return;
    if (fitted() && !B("status.k2_cfs.online")) {
        center_message("CFS connection lost",
                       "Check the CFS cable and power before changing filament.", "Back to print",
                       "route:home");
        return;
    }
    if (!fitted()) {
        snprintf(app.selected_slot, sizeof(app.selected_slot), "E");
        cJSON *record = slot_record("E");
        lv_obj_t *tile = button(app.content, "", 0, 110, 673, 247, "edit_slot", false);
        const char *name = jstr(record, "name", "Spool holder");
        lv_point_t metrics;
        lv_txt_get_size(&metrics, name, &font_ui_24, 0, 0, 250, LV_TEXT_FLAG_NONE);
        int group_width = 62 + 28 + metrics.x;
        int start = (lv_obj_get_width(tile) - group_width) / 2;
        spool_widget(tile, start, 92, 62, jstr(record, "color", "828b98"));
        text(tile, name, start + 90, 93, metrics.x + 2, &font_ui_24, false);
        centered(text(tile, "Spool holder", start + 90, 135, metrics.x + 2, &font_ui_14, true));
        icon(tile, "pencil", 673 - 24 - 20, 247 - 24 - 20, 24, colour("a4aebb"));
        if (filament_slot_busy("E")) {
            filament_spinner(tile, (673 - 28) / 2, 181, 28);
            centered(text(tile, filament_activity_label(), 0, 218, 673, &font_ui_16, false));
        } else if (feeding_slot("E"))
            icon(tile, "nozzle", (673 - 24) / 2, 200, 24, colour("f0f3f7"));
        return;
    }
    int boxes = 0;
    char groups[16][16] = {{0}};
    cJSON *r;
    cJSON_ArrayForEach(r, J("slots")) {
        const char *slot = jstr(r, "slot", "");
        size_t n = strlen(slot);
        if (!n || !strcmp(slot, "E"))
            continue;
        char group[16];
        if (n == 1)
            snprintf(group, sizeof(group), "1");
        else
            snprintf(group, sizeof(group), "%.*s", (int)n - 1, slot);
        bool seen = false;
        for (int i = 0; i < boxes; i++)
            if (!strcmp(groups[i], group))
                seen = true;
        if (!seen && boxes < 16)
            snprintf(groups[boxes++], 16, "%s", group);
    }
    const int top = boxes > 1 ? 162 : 88;
    if (app.select_feeding_cfs) {
        const char *busy = S("filament_activity.slot");
        if (*filament_activity_label() && *busy && strcmp(busy, "E")) {
            slot_unit(busy, app.cfs_unit, sizeof(app.cfs_unit));
            snprintf(app.selected_slot, sizeof(app.selected_slot), "%s", busy);
        } else
            cJSON_ArrayForEach(r, J("slots")) {
                const char *slot = jstr(r, "slot", "");
                if (strcmp(slot, "E") && feeding_slot(slot)) {
                    size_t n = strlen(slot);
                    snprintf(app.cfs_unit, sizeof(app.cfs_unit), "%.*s", n > 1 ? (int)n - 1 : 1,
                             n > 1 ? slot : "1");
                    snprintf(app.selected_slot, sizeof(app.selected_slot), "%s", slot);
                    break;
                }
            }
        app.select_feeding_cfs = false;
    }
    bool selected_unit = false;
    for (int i = 0; i < boxes; i++)
        if (!strcmp(app.cfs_unit, groups[i]))
            selected_unit = true;
    if (!selected_unit && boxes)
        snprintf(app.cfs_unit, sizeof(app.cfs_unit), "%s", groups[0]);
    if (boxes > 1) {
        /* Unit navigation belongs to the CFS group, never the spool holder. */
        int tab_width = (524 - 8 * (boxes - 1)) / boxes;
        for (int i = 0; i < boxes; i++) {
            char label[32], action[32];
            snprintf(label, sizeof(label), "CFS %s", groups[i]);
            snprintf(action, sizeof(action), "unit:%s", groups[i]);
            lv_obj_t *tab = button(app.content, "", i * (tab_width + 8), 88, tab_width, 56, action,
                                   !strcmp(app.cfs_unit, groups[i]));
            centered(text(tab, label, 2, 5, tab_width - 4, &font_ui_16, false));
            char busy_unit[16];
            slot_unit(S("filament_activity.slot"), busy_unit, sizeof(busy_unit));
            if (*filament_activity_label() && *S("filament_activity.slot") &&
                strcmp(S("filament_activity.slot"), "E") && !strcmp(groups[i], busy_unit))
                filament_spinner(tab, tab_width - 22, 10, 14);
            int dot_index = 0;
            cJSON *record;
            cJSON_ArrayForEach(record, J("slots")) {
                const char *slot = jstr(record, "slot", "");
                size_t n = strlen(slot);
                if (!n || !strcmp(slot, "E"))
                    continue;
                char group[16];
                snprintf(group, sizeof(group), "%.*s", n > 1 ? (int)n - 1 : 1, n > 1 ? slot : "1");
                if (strcmp(group, groups[i]) || dot_index >= 4)
                    continue;
                lv_obj_t *dot =
                    box(tab, (tab_width - 66) / 2 + dot_index++ * 18, 33, 12, 12, false);
                lv_obj_clear_flag(dot, LV_OBJ_FLAG_CLICKABLE);
                lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
                lv_obj_set_style_bg_color(dot,
                                          colour(jbool(record, "loaded", false)
                                                     ? jstr(record, "color", "828b98")
                                                     : "343e4b"),
                                          0);
                lv_obj_set_style_border_width(dot, 0, 0);
            }
        }
    }

    cJSON *records[16];
    int count = 0;
    if (fitted())
        cJSON_ArrayForEach(r, J("slots")) {
            const char *slot = jstr(r, "slot", "");
            size_t n = strlen(slot);
            if (!n || !strcmp(slot, "E"))
                continue;
            if (boxes > 1 &&
                ((n == 1 && strcmp(app.cfs_unit, "1")) ||
                 (n > 1 && (strlen(app.cfs_unit) != n - 1 || strncmp(slot, app.cfs_unit, n - 1)))))
                continue;
            if (count < 15)
                records[count++] = r;
        }
    int cfs_count = count;
    records[count++] = slot_record("E");
    int width = cfs_count ? (524 - 10 * (cfs_count - 1)) / cfs_count : 150;
    int start = cfs_count ? 0 : (674 - width) / 2;
    for (int i = 0; i < count; i++) {
        r = records[i];
        const char *slot = i == cfs_count ? "E" : jstr(r, "slot", "");
        bool external = i == cfs_count;
        int x = external ? (fitted() ? 548 : (674 - 126) / 2) : start + i * (width + 10);
        int tile_width = external ? 126 : width;
        int tile_top = external ? 88 : top;
        int tile_height = external && boxes != 1 ? 292 : 328 - top;
        char action[50];
        snprintf(action, sizeof(action), "slot:%s", slot);
        lv_obj_t *tile = button(app.content, "", x, tile_top, tile_width, tile_height, action,
                                !strcmp(slot, app.selected_slot));
        int content_y = (tile_height - 166) / 2;
        char display_slot[64];
        snprintf(display_slot, sizeof(display_slot), "%s",
                 !strcmp(slot, "E") ? "Spool holder" : slot_label(slot));
        centered(text(tile, display_slot, 4, content_y + 12, tile_width - 8, &font_ui_17, false));
        bool present = !strcmp(slot, "E") ? feeding_slot("E") : jbool(r, "loaded", false);
        const char *hex = present ? jstr(r, "color", "828b98") : "343e4b";
        lv_color_t ring = colour(hex);
        if (ring.ch.red < 25 && ring.ch.green < 25 && ring.ch.blue < 25)
            ring = colour("2d3238");
        lv_obj_t *spool = box(tile, (tile_width - 58) / 2, content_y + 43, 58, 58, false);
        lv_obj_set_style_bg_opa(spool, 0, 0);
        lv_obj_clear_flag(spool, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_radius(spool, 29, 0);
        lv_obj_set_style_border_width(spool, 5, 0);
        lv_obj_set_style_border_color(spool, ring, 0);
        lv_obj_t *hub = box(spool, 9, 9, 28, 28, false);
        lv_obj_clear_flag(hub, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_radius(hub, 14, 0);
        lv_obj_set_style_bg_opa(hub, 0, 0);
        lv_obj_set_style_border_width(hub, 2, 0);
        lv_obj_set_style_border_color(hub, ring, 0);
        const char *kind = present ? jstr(r, "type", "") : "";
        centered(text(tile,
                      *kind                ? kind
                      : !strcmp(slot, "E") ? "Spool"
                      : present            ? "Unknown"
                                           : "Empty",
                      4, content_y + 109, tile_width - 8, &font_ui_16, false));
        if (filament_slot_busy(slot)) {
            lv_obj_t *spinner = filament_spinner(tile, 0, 0, 30);
            lv_obj_align_to(spinner, spool, LV_ALIGN_CENTER, 0, 0);
            centered(text(tile, filament_activity_label(), 2, content_y + 140, tile_width - 4,
                          &font_ui_14, false));
        } else if (feeding_slot(slot))
            icon(tile, "nozzle", (tile_width - 24) / 2, content_y + 134, 24, colour("f0f3f7"));
    }

    if (fitted()) {
        char temp[40], humidity[40];
        /* The legacy driver publishes one unit's readings only. */
        bool environment_known = boxes == 1 || !strcmp(app.cfs_unit, "1");
        if (environment_known) {
            snprintf(temp, sizeof(temp), "%.0f°C", N("status.k2_cfs.temperature"));
            snprintf(humidity, sizeof(humidity), "%.0f%% RH", N("status.k2_cfs.humidity"));
        } else {
            snprintf(temp, sizeof(temp), "—°C");
            snprintf(humidity, sizeof(humidity), "—%% RH");
        }
        lv_obj_t *environment = box(app.content, 0, 330, 1, 46, false);
        lv_obj_clear_flag(environment, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_bg_opa(environment, LV_OPA_50, 0);
        lv_obj_set_style_pad_left(environment, 24, 0);
        lv_obj_set_style_pad_right(environment, 24, 0);
        lv_obj_set_style_border_width(environment, 0, 0);
        lv_obj_set_width(environment, LV_SIZE_CONTENT);
        lv_obj_set_flex_flow(environment, LV_FLEX_FLOW_ROW);
        lv_obj_set_flex_align(environment, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                              LV_FLEX_ALIGN_CENTER);
        lv_obj_set_style_pad_column(environment, 10, 0);
        icon(environment, "thermometer", 0, 0, 24, colour("a4aebb"));
        lv_obj_t *temperature = text(environment, temp, 0, 0, 1, &font_ui_20, false);
        lv_obj_set_width(temperature, LV_SIZE_CONTENT);
        lv_obj_set_style_pad_right(temperature, 18, 0);
        icon(environment, "droplets", 0, 0, 24, colour("a4aebb"));
        lv_obj_t *relative_humidity = text(environment, humidity, 0, 0, 1, &font_ui_20, false);
        lv_obj_set_width(relative_humidity, LV_SIZE_CONTENT);
        lv_obj_update_layout(environment);
        int environment_width = boxes == 1 ? 674 : 524;
        lv_obj_set_pos(environment, (environment_width - lv_obj_get_width(environment)) / 2, 334);
    }
    button_fixed(app.content, "Edit", 44, 398, 110, 68, "edit_slot", false);
    cJSON *selected = slot_record(app.selected_slot);
    bool can_load = !strcmp(app.selected_slot, "E") || jbool(selected, "loaded", false);
    button_fixed(app.content, "Load", 166, 398, 190, 68, can_load ? "load" : NULL, false);
    button_fixed(app.content, "Unload", 368, 398, 190, 68, "unload", false);
    if (fitted())
        button_fixed(app.content, "icon:sliders-horizontal", 570, 398, 64, 68, "route:refill",
                     false);
}
static void edit_view(void) {
    if (ui_unavailable("Filament editing", "editing filament"))
        return;
    if (!app.draft)
        app.draft = cJSON_Duplicate(slot_record(app.selected_slot), true);
    if (!app.draft)
        app.draft = cJSON_CreateObject();
    const char *labels[] = {"Material", "Color", "Nozzle temp"};
    const char *actions[] = {"route:materials", "route:colors", "draft_temperature"};
    char temperature[50];
    snprintf(temperature, sizeof(temperature), "%.0f°C", jnum(app.draft, "nozzle_temp", 0));
    const char *values[] = {jstr(app.draft, "name", "Choose material"),
                            jstr(app.draft, "color", "Choose colour"), temperature};
    for (int i = 0; i < 3; i++) {
        lv_obj_t *field = button(app.content, "", 37, 104 + i * 90, 600, 76, actions[i], false);
        text(field, labels[i], 20, 26, 210, &font_ui_18, true);
        if (i == 1) {
            lv_obj_t *swatch = box(field, 506, 19, 36, 36, false);
            lv_obj_clear_flag(swatch, LV_OBJ_FLAG_CLICKABLE);
            lv_obj_set_style_radius(swatch, 18, 0);
            lv_obj_set_style_bg_color(swatch, colour(jstr(app.draft, "color", "828b98")), 0);
            lv_obj_set_style_border_color(swatch, colour("a4aebb"), 0);
        } else
            right(text(field, values[i], 230, 25, 320, &font_ui_20, false));
        icon(field, i == 2 ? "pencil" : "chevron-right", 560, 30, 16, colour("a4aebb"));
    }
    button_fixed(app.content, "Cancel", 0, 402, 210, 64, "cancel_edit", false);
    button_fixed(app.content, "Reset", 232, 402, 210, 64, "reset_spool", false);
    button_fixed(app.content, "Save", 464, 402, 210, 64, "save_slot", true);
}
static void materials_view(void) {
    const char *filters[] = {"All", "PLA", "PETG", "ABS", "TPU", "Other"};
    for (int i = 0; i < 6; i++) {
        char action[80];
        snprintf(action, sizeof(action), "filter:%s", filters[i]);
        button(app.content, filters[i], i * 113, 77, 105, 60, action,
               !strcmp(filters[i], app.filter));
    }
    lv_obj_t *list = scroll_area(143, 386);
    int y = 0;
    cJSON *m;
    cJSON_ArrayForEach(m, J("materials")) {
        const char *type = jstr(m, "type", "");
        if (strcmp(app.filter, "All") && strcmp(app.filter, type) &&
            !(!strcmp(app.filter, "Other") && strcmp(type, "PLA") && strcmp(type, "PETG") &&
              strcmp(type, "ABS") && strcmp(type, "TPU")))
            continue;
        char action[80];
        snprintf(action, sizeof(action), "material:%d", cJSON_GetArraySize(J("materials")));
        char *payload = encode(cJSON_Duplicate(m, true));
        size_t len = strlen(payload) + 16;
        char *a = malloc(len);
        snprintf(a, len, "material:%s", payload);
        row(list, y, jstr(m, "name", ""), "›", type, a);
        free(payload);
        free(a);
        y += SETTINGS_ROW_PITCH;
    }
    if (!y)
        text(list, "No matching materials", 20, 50, 600, &font_ui_20, true);
    footer(NULL, NULL, "Back", "route:edit");
}
static void colors_view(void) {
    const char *colors[] = {"20252d", "e9e9e5", "828b98", "e34c55", "e69039", "edce52",
                            "4f9f73", "28adbc", "3d8bfd", "5035ce", "d472a4", "ab7859"};
    for (int i = 0; i < 12; i++) {
        char action[50];
        snprintf(action, sizeof(action), "color:%s", colors[i]);
        lv_obj_t *b =
            button(app.content, "", 5 + (i % 6) * 112.5, 90 + (i / 6) * 82, 101, 72, action, false);
        lv_obj_set_style_bg_color(b, colour(colors[i]), 0);
    }
    text(app.content, "Hex colour", 0, 258, 673, &font_ui_14, true);
    button(app.content, jstr(app.draft, "color", ""), 0, 286, 673, 64, "color_hex", false);
    footer(NULL, NULL, "Done", "route:edit");
}
static void tune_view(void) {
    ui_tabs(false, "tune");
    if (!app.printing && !app.paused) {
        center_message("No print in progress", "Live adjustments become available during a print.",
                       "Choose a file", "open_files");
        return;
    }
    const char *labels[] = {"Print speed", "Flow ratio"}, *keys[] = {"speed", "flow"},
               *paths[] = {"status.gcode_move.speed_factor", "status.gcode_move.extrude_factor"};
    for (int i = 0; i < 2; i++) {
        int y = 110 + i * 108;
        double value = jnum(app.data, paths[i], 1) * 100;
        double increment = i == 0 ? 10 : 1;
        text(app.content, labels[i], 0, y + 22, 260, &font_ui_18, false);
        char action[120], caption[50];
        char maximum_path[80];
        snprintf(maximum_path, sizeof(maximum_path), "tune_limits.%s.maximum", keys[i]);
        double maximum = jnum(app.data, maximum_path, 200);
        snprintf(action, sizeof(action), "tune:%s:%g", keys[i], fmax(1, value - increment));
        button_fixed(app.content, "−", 310, y, 64, 64, action, false);
        snprintf(action, sizeof(action), "tune_input:%s", keys[i]);
        snprintf(caption, sizeof(caption), "%.0f%%", value);
        lv_obj_t *input = button_fixed(app.content, "", 386, y, 162, 64, action, false);
        lv_obj_t *value_text = text(input, caption, 0, 0, 162, &font_ui_24, false);
        centered(value_text);
        lv_obj_align(value_text, LV_ALIGN_CENTER, 0, 0);
        lv_obj_set_style_bg_opa(input, 0, 0);
        lv_obj_set_style_border_width(input, 0, 0);
        snprintf(action, sizeof(action), "tune:%s:%g", keys[i], fmin(maximum, value + increment));
        button_fixed(app.content, "+", 560, y, 64, 64, action, false);
    }
    text(app.content, "Z offset", 0, 356, 90, &font_ui_18, false);
    text(app.content, "Current print only", 100, 360, 196, &font_ui_12, true);
    button(app.content, "−0.01", 310, 334, 64, 64, "tune:z:-0.01", false);
    char z[80];
    snprintf(z, sizeof(z), "%.3f mm", N("status.gcode_move.homing_origin.2"));
    lv_obj_t *offset =
        button_fixed(app.content, "", 386, 334, 162, 64, "tune_input:z_offset", false);
    lv_obj_set_style_bg_opa(offset, 0, 0);
    lv_obj_set_style_border_width(offset, 0, 0);
    lv_obj_t *offset_text = text(offset, z, 0, 0, 162, &font_ui_24, false);
    centered(offset_text);
    lv_obj_align(offset_text, LV_ALIGN_CENTER, 0, 0);
    button(app.content, "+0.01", 560, 334, 64, 64, "tune:z:0.01", false);
}
static void calibration_view(void) {
    ui_tabs(false, "calibration");
    if (ui_unavailable("Calibration", "running calibration"))
        return;
    const char *labels[] = {"Home all",          "Z tilt",       "Full bed mesh",
                            "Input shaping",     "Belt tension", "Cutter calibration",
                            "Save configuration"};
    const char *keys[] = {"home", "tilt", "mesh", "shaper", "belt", "cutter", "save"};
    const char *caps[] = {NULL,
                          "settings.z_tilt",
                          "settings.bed_mesh",
                          "settings.resonance_tester",
                          "settings.k2_belt_tension",
                          "settings.k2_cfs",
                          NULL};
    int visible = 0;
    for (int i = 0; i < 7; i++) {
        if (caps[i] && !has(caps[i]))
            continue;
        char action[80];
        snprintf(action, sizeof(action), "calibrate:%s", keys[i]);
        button(app.content, labels[i], (visible % 2) * 342, 100 + (visible / 2) * 92, 330, 84,
               action, false);
        visible++;
    }
}
static lv_obj_t *list_action(lv_obj_t *list, const char *name, int x, int y, const char *action) {
    char label[64];
    snprintf(label, sizeof(label), "icon:%s", name);
    lv_obj_t *control = button_fixed(list, label, x, y, 64, 64, action, false);
    lv_obj_set_style_bg_opa(control, LV_OPA_TRANSP, LV_STATE_DEFAULT);
    lv_obj_set_style_border_width(control, 0, LV_STATE_DEFAULT);
    return control;
}
static void files_view(void) {
    button_fixed(app.content, "icon:refresh-cw", 600, 9, 64, 64, "refresh_files", false);
    const char *refresh = *S("files_refresh.error") ? "Refresh failed — tap refresh to retry" : "";
    lv_obj_t *status = text(app.content, refresh, 100, 32, 484, &font_ui_14, true);
    lv_obj_set_height(status, 22);
    lv_label_set_long_mode(status, LV_LABEL_LONG_DOT);
    lv_obj_t *list = scroll_area(83, 462);
    cJSON *f;
    int y = 0;
    cJSON_ArrayForEach(f, J("files")) {
        const char *path = jstr(f, "path", "");
        char *payload = request_json("file", "path", path);
        size_t n = strlen(payload) + 16;
        char *a = malloc(n);
        snprintf(a, n, "file_open:%s", payload);
        lv_obj_t *card = box(list, 0, y, 673, 78, false);
        lv_obj_clear_flag(card, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_border_width(card, 0, 0);
        lv_obj_t *b = button(list, "", 0, y, 586, 78, a, false);
        lv_obj_set_style_bg_opa(b, 0, 0);
        lv_obj_set_style_border_width(b, 0, 0);
        cJSON *thumb = cJSON_GetObjectItemCaseSensitive(J("platform.file_thumbnails"), path);
        const char *thumbnail = cJSON_IsString(thumb) ? thumb->valuestring : "";
        if (image_fit(b, thumbnail, 12, 9, 96, 60)) {
            lv_obj_t *image = lv_obj_get_child(b, -1);
            lv_obj_t *well = box(b, 12, 9, 96, 60, false);
            lv_obj_clear_flag(well, LV_OBJ_FLAG_CLICKABLE);
            lv_obj_set_style_bg_color(well, colour("30343c"), 0);
            lv_obj_set_style_border_width(well, 0, 0);
            lv_obj_move_to_index(well, lv_obj_get_index(image));
        } else
            icon(b, "folder-open", 48, 27, 24, colour("a4aebb"));
        const char *name = strrchr(path, '/');
        lv_obj_t *filename = text(b, name ? name + 1 : path, 120, 14, 450, &font_ui_18, false);
        lv_label_set_long_mode(filename, LV_LABEL_LONG_DOT);
        lv_obj_set_height(filename, 26);
        time_t stamp = (time_t)jnum(f, "modified", 0);
        struct tm tm;
        localtime_r(&stamp, &tm);
        char modified[80];
        strftime(modified, sizeof(modified), "%d %b %Y · %H:%M", &tm);
        text(b, modified, 120, 44, 450, &font_ui_13, true);
        snprintf(a, n, "file_delete:%s", payload);
        list_action(list, "trash-2", 600, y + 7, a);
        free(a);
        free(payload);
        y += 88;
    }
    if (!y && !B("files_refresh.busy")) {
        const char *empty = *S("files_refresh.error") ? S("files_refresh.error") : "No print files";
        text(list, empty, 14, 40, 620, &font_ui_20, true);
    }
}
static void file_view(void) {
    if (ui_unavailable("Starting a print", "starting another print"))
        return;
    const char *path = app.file_waiting ? app.file : S("file");
    if (!*path) {
        center_message("Select a file", "Choose a sliced G-code file.", "Files", "open_files");
        return;
    }
    if (path != app.file)
        snprintf(app.file, sizeof(app.file), "%s", path);
    lv_obj_t *art = box(app.content, 0, 19, 352, 361, false);
    lv_obj_set_style_bg_color(art, colour("30343c"), 0);
    lv_obj_set_style_border_width(art, 0, 0);
    const char *thumbnail = S("platform.file_thumbnail");
    bool reading = app.file_waiting || B("file_read.busy");
    if (reading || !image_fit(app.content, thumbnail, 12, 31, 328, 337))
        preview_placeholder(app.content, 12, 31, 328, 337, reading);
    lv_obj_t *details = box(app.content, 368, 19, 306, 361, false);
    lv_obj_set_style_border_width(details, 0, 0);
    char title[1024];
    file_title(title, sizeof(title), path);
    lv_obj_t *name = text(app.content, title, 384, 39, 274, &font_ui_24, false);
    lv_label_set_long_mode(name, LV_LABEL_LONG_DOT);
    lv_obj_set_height(name, 56);
    const char *labels[] = {"Estimated time", "Filament", "Layers"};
    char values[3][80];
    snprintf(values[0], sizeof(values[0]), "%.0f min", N("metadata.estimated_time") / 60);
    snprintf(values[1], sizeof(values[1]), "%.1f g", N("metadata.filament_weight_total"));
    snprintf(values[2], sizeof(values[2]), "%.0f", N("metadata.layer_count"));
    if (reading)
        for (int i = 0; i < 3; i++)
            snprintf(values[i], sizeof(values[i]), "—");
    for (int i = 0; i < 3; i++) {
        text(app.content, labels[i], 384, 125 + i * 50, 160, &font_ui_16, true);
        lv_obj_t *value = text(app.content, values[i], 548, 123 + i * 50, 110, &font_ui_20, false);
        right(value);
        lv_label_set_long_mode(value, LV_LABEL_LONG_DOT);
        lv_obj_set_height(value, 28);
    }
    if (B("calibrate_before_print") ||
        has("settings.gcode_macro START_PRINT.variable_screen_full_mesh") ||
        has("settings.gcode_macro start_print.variable_screen_full_mesh")) {
        lv_obj_t *toggle =
            button_fixed(app.content, "", 376, 300, 286, 64, "calibrate_before", false);
        lv_obj_set_style_bg_opa(toggle, 0, 0);
        lv_obj_set_style_border_width(toggle, 0, 0);
        lv_obj_t *check = box(toggle, 8, 16, 32, 32, calibrate_before);
        lv_obj_clear_flag(check, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_radius(check, 6, 0);
        if (calibrate_before)
            icon(check, "check", 4, 4, 24, colour("f0f3f7"));
        text(toggle, "Calibrate before printing", 52, 20, 224, &font_ui_17, false);
    }
    button_fixed(app.content, "Back", 0, 402, 352, 64, "back", false);
    lv_obj_t *start =
        button_fixed(app.content, "Start printing", 368, 402, 306, 64, "review_mapping", true);
    if (reading || (has("file_read") && !cJSON_GetArraySize(J("metadata")))) {
        lv_obj_add_state(start, LV_STATE_DISABLED);
        lv_obj_set_style_opa(start, 100, 0);
    }
}
static void colour_swatch(lv_obj_t *parent, int x, int y, const char *hex, int size) {
    lv_obj_t *swatch = box(parent, x, y, size, size, false);
    lv_obj_clear_flag(swatch, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_radius(swatch, size / 2, 0);
    lv_obj_set_style_bg_color(swatch, colour(hex && *hex ? hex : "343e4b"), 0);
    lv_obj_set_style_border_color(swatch, colour("a4aebb"), 0);
}
static void source_label(char *buffer, size_t size, const char *slot) {
    if (!slot || !*slot)
        snprintf(buffer, size, "Select filament");
    else if (!strcmp(slot, "E"))
        snprintf(buffer, size, "Spool holder");
    else {
        size_t length = strlen(slot);
        snprintf(buffer, size, "CFS %.*s · %c", length > 1 ? (int)length - 1 : 1,
                 length > 1 ? slot : "1", slot[length - 1]);
    }
}
/* Same material rule as backend/model.py: case/ASCII whitespace only. */
static bool material_matches(const char *required, const char *actual) {
    for (;;) {
        while (*required && strchr(" \t\r\n\f\v", *required))
            required++;
        while (*actual && strchr(" \t\r\n\f\v", *actual))
            actual++;
        if (!*required)
            return !*actual;
        if (toupper((unsigned char)*required++) != toupper((unsigned char)*actual))
            return false;
        if (*actual)
            actual++;
    }
}
static bool compatible_material(cJSON *filament, cJSON *record) {
    const char *required = jstr(filament, "type", "");
    const char *p = required;
    while (*p && strchr(" \t\r\n\f\v", *p))
        p++;
    return !*p || material_matches(required, jstr(record, "type", ""));
}
static cJSON *mapping_filament(void) {
    cJSON *filament;
    cJSON_ArrayForEach(filament, J("metadata.filaments")) {
        char key[20];
        snprintf(key, sizeof(key), "%d", (int)jnum(filament, "tool", 0));
        if (!strcmp(key, app.mapping_tool))
            return filament;
    }
    return NULL;
}
static bool mapping_ready(void) {
    cJSON *filament;
    cJSON_ArrayForEach(filament, J("metadata.filaments")) {
        char key[20];
        snprintf(key, sizeof(key), "%d", (int)jnum(filament, "tool", 0));
        const char *target = jstr(app.mapping, key, "");
        if (!known_slot(slot_record(target)) ||
            !compatible_material(filament, slot_record(target)) ||
            (!strcmp(target, "E") && cJSON_GetArraySize(J("metadata.filaments")) > 1))
            return false;
    }
    return cJSON_GetArraySize(J("metadata.filaments")) > 0 ||
           known_slot(slot_record(jstr(app.mapping, "0", "")));
}
static void mapping_view(void) {
    if (ui_unavailable("Filament selection", "starting another print"))
        return;
    lv_obj_t *list = scroll_area(19, 379);
    cJSON *filament;
    int y = 0;
    int count = cJSON_GetArraySize(J("metadata.filaments"));
    if (count > 3)
        lv_obj_set_scrollbar_mode(list, LV_SCROLLBAR_MODE_ON);
    for (int i = 0; i < (count ? count : 1); i++) {
        filament = cJSON_GetArrayItem(J("metadata.filaments"), i);
        int tool = (int)jnum(filament, "tool", 0);
        char key[20], caption[64], action[64];
        snprintf(key, sizeof(key), "%d", tool);
        snprintf(caption, sizeof(caption), "File filament %d", tool + 1);
        snprintf(action, sizeof(action), "mapping_pick:%d", tool);
        const char *target = jstr(app.mapping, key, "");
        cJSON *record = slot_record(target);
        bool known = known_slot(record);
        bool mismatch = known && !compatible_material(filament, record);
        lv_obj_t *card = box(list, 0, y, 674, 112, false);
        lv_obj_clear_flag(card, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_bg_opa(card, 0, 0);
        lv_obj_set_style_border_width(card, 0, 0);
        lv_obj_t *required = box(card, 0, 6, 286, 96, false);
        lv_obj_clear_flag(required, LV_OBJ_FLAG_CLICKABLE);
        text(required, caption, 16, 6, 258, &font_ui_16, true);
        colour_swatch(required, 16, 42, jstr(filament, "colour", jstr(filament, "color", "828b98")),
                      32);
        lv_obj_t *required_type = text(required, jstr(filament, "type", "Not specified"), 60, 44,
                                       214, &font_ui_20, false);
        lv_label_set_long_mode(required_type, LV_LABEL_LONG_DOT);
        lv_obj_set_height(required_type, 26);
        icon(card, "arrow-right", 300, 52, 24, colour("a4aebb"));
        lv_obj_t *pick = button_fixed(card, "", 348, 6, 320, 96, action, false);
        if (known) {
            char source[80];
            source_label(source, sizeof(source), target);
            colour_swatch(pick, 16, 42, jstr(record, "color", "828b98"), 32);
            lv_obj_t *selected_type = text(pick, jstr(record, "type", "Unknown material"), 60, 44,
                                           208, &font_ui_20, false);
            lv_label_set_long_mode(selected_type, LV_LABEL_LONG_DOT);
            lv_obj_set_height(selected_type, 26);
            text(pick, source, 16, 6, 252, &font_ui_16, true);
            if (mismatch) {
                lv_obj_set_style_border_color(pick, colour("e35d6a"), 0);
                lv_obj_t *warning =
                    text(pick, "Material mismatch", 60, 74, 230, &font_ui_12, false);
                lv_obj_set_style_text_color(warning, colour("e35d6a"), 0);
            }
        } else {
            text(pick, "Printer filament", 16, 6, 252, &font_ui_16, true);
            colour_swatch(pick, 16, 42, "343e4b", 32);
            text(pick, "Select filament", 60, 44, 208, &font_ui_20, true);
        }
        if (filament_slot_busy(target))
            filament_spinner(pick, 282, 46, 24);
        else
            icon(pick, "chevron-down", 284, 48, 20, colour("a4aebb"));
        y += 120;
    }
    if (!y)
        text(list, "No filament requirements found in this file.", 14, 40, 620, &font_ui_20, true);
    button_fixed(app.content, "Back", 0, 402, 286, 64, "back", false);
    button_fixed(app.content, "Start print", 348, 402, 320, 64,
                 mapping_ready() ? "start_print" : NULL, true);
}
static void mapping_pick(void) {
    cJSON *filament = mapping_filament();
    lv_obj_t *list = scroll_area(88, 368);
    int y = 0;
    cJSON *record;
    cJSON_ArrayForEach(record, J("slots")) {
        if (!known_slot(record))
            continue;
        const char *slot = jstr(record, "slot", "");
        if (!strcmp(slot, "E") && cJSON_GetArraySize(J("metadata.filaments")) > 1)
            continue;
        char action[64], source[80];
        snprintf(action, sizeof(action), "mapping_set:%s", slot);
        source_label(source, sizeof(source), slot);
        bool compatible = compatible_material(filament, record);
        bool selected = compatible && !strcmp(jstr(app.mapping, app.mapping_tool, ""), slot);
        lv_obj_t *item = button_fixed(list, "", 0, y, 673, 88, action, selected);
        colour_swatch(item, 16, 24, jstr(record, "color", "828b98"), 40);
        text(item, jstr(record, "type", "Unknown material"), 76, 17, 450, &font_ui_20, false);
        if (filament_slot_busy(slot)) {
            size_t length = strlen(source);
            snprintf(source + length, sizeof(source) - length, " · %s", filament_activity_label());
            filament_spinner(item, 622, 28, 28);
        }
        text(item, source, 76, 54, 450, &font_ui_14, true);
        if (!compatible) {
            lv_obj_add_state(item, LV_STATE_DISABLED);
            lv_obj_set_style_opa(item, 160, 0);
        }
        if (selected && !filament_slot_busy(slot))
            icon(item, "check", 624, 29, 28, app.accent);
        y += 96;
    }
    if (y > 280)
        lv_obj_set_scrollbar_mode(list, LV_SCROLLBAR_MODE_ON);
    if (!y)
        text(list, "No available filaments with known material and color.", 18, 50, 620,
             &font_ui_20, true);
}

static void settings_view(void) {
    ui_tabs(true, "settings");
    row(app.content, 100, "Display & appearance", "›", "Brightness and highlights",
        "route:display");
    row(app.content, 212, "Light & idle timers", "›", "Automatic switch-off", "route:timers");
    if (fitted())
        row(app.content, 324, "CFS preferences", "›", "Reading and automatic refill",
            "route:refill");
}
static void choices(int y, const char *title, const char *key, const int *values,
                    const char **labels, int count, int current) {
    text(app.content, title, 2, y, 673, &font_ui_20, false);
    int w = (674 - 10 * (count - 1)) / count;
    for (int i = 0; i < count; i++) {
        cJSON *r = request("preference");
        cJSON_AddStringToObject(r, "key", key);
        cJSON_AddNumberToObject(r, "value", values[i]);
        char *a = encode(r);
        button(app.content, labels[i], i * (w + 10), y + 32, w, 64, a, values[i] == current);
        free(a);
    }
}
static void display_view(void) {
    int brightness[] = {25, 50, 75, 100};
    const char *bl[] = {"25%", "50%", "75%", "100%"};
    choices(78, "Brightness", "brightness", brightness, bl, 4,
            (int)jnum(app.data, "prefs.brightness", 100));
    text(app.content, "Highlights", 2, 198, 673, &font_ui_20, false);
    const char *colors[] = {"#3d8bfd", "#d9434a", "#f39c12", "#27ae60", "#8e44ad", "#d9dfe8"};
    for (int i = 0; i < 6; i++) {
        cJSON *r = request("preference");
        cJSON_AddStringToObject(r, "key", "accent");
        cJSON_AddStringToObject(r, "value", colors[i]);
        char *a = encode(r);
        lv_obj_t *b = button_fixed(app.content, "", 6 + i * 112, 230, 101, 76, a,
                                   !strcmp(S("prefs.accent"), colors[i]));
        lv_obj_set_style_bg_color(b, colour(colors[i]), 0);
        lv_obj_set_style_radius(b, 11, 0);
        lv_obj_set_style_border_width(b, 2, 0);
        if (!strcmp(S("prefs.accent"), colors[i])) {
            lv_obj_set_style_outline_color(b, app.accent, 0);
            lv_obj_set_style_outline_width(b, 3, 0);
            lv_obj_set_style_outline_pad(b, 3, 0);
        }
        free(a);
    }
}
static void timers_view(void) {
    int light[] = {0, 300, 600, 1800}, idle[] = {900, 1800, 3600, 86400};
    int sleep[] = {120, 300, 600, 0};
    const char *sl[] = {"2 min", "5 min", "10 min", "Never"};
    const char *ll[] = {"Never", "5 min", "10 min", "30 min"},
               *il[] = {"15 min", "30 min", "1 hour", "24 hours"};
    choices(78, "Screen off", "sleep_after", sleep, sl, 4,
            (int)jnum(app.data, "prefs.sleep_after", 600));
    choices(198, "Light off", "light_off", light, ll, 4,
            (int)jnum(app.data, "prefs.light_off", 300));
    choices(318, "Idle shutdown", "idle", idle, il, 4, (int)jnum(app.data, "prefs.idle", 1800));
}

static void toggle_card(int y, int height, const char *title, const char *detail, bool enabled,
                        const char *action) {
    lv_obj_t *setting = button_fixed(app.content, "", 0, y, 674, height, action, false);
    lv_obj_set_style_border_width(setting, 0, 0);
    int top = (height - 46) / 2;
    text(setting, title, 16, top, 530, &font_ui_20, false);
    text(setting,
         detail && *detail ? detail
         : enabled         ? "On"
                           : "Off",
         16, top + 28, 530, &font_ui_14, true);
    lv_obj_t *track = box(setting, 582, (height - 40) / 2, 76, 40, false);
    lv_obj_clear_flag(track, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_radius(track, 20, 0);
    lv_obj_set_style_border_width(track, 0, 0);
    lv_obj_set_style_bg_color(track, enabled ? app.accent : colour("596371"), 0);
    lv_obj_t *thumb = box(track, enabled ? 39 : 5, 5, 30, 30, false);
    lv_obj_clear_flag(thumb, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_radius(thumb, 15, 0);
    lv_obj_set_style_border_width(thumb, 0, 0);
    lv_obj_set_style_bg_color(thumb, colour("f0f3f7"), 0);
}
static void refill_view(void) {
    if (ui_unavailable("CFS preferences", "changing CFS preferences"))
        return;
    if (!fitted()) {
        center_message("No CFS installed", "CFS preferences require a connected CFS.", NULL, NULL);
        return;
    }
    bool enabled = jbool(app.data, "prefs.cfs_auto_refill", true);
    cJSON *r = request("preference");
    cJSON_AddStringToObject(r, "key", "cfs_auto_refill");
    cJSON_AddBoolToObject(r, "value", !enabled);
    char *a = encode(r);
    toggle_card(90, SETTINGS_ROW_HEIGHT, "Auto refill", "", enabled, a);
    free(a);
    row(app.content, 90 + SETTINGS_ROW_PITCH, "Auto refill sequence", "›", "",
        "route:refill_sequence");
    centered(text(app.content, "Filament details are requested after a spool is inserted.", 20, 314,
                  634, &font_ui_18, true));
}
static void refill_sequence(void) {
    lv_obj_t *list = scroll_area(88, 462);
    cJSON *group;
    int y = 0;
    cJSON_ArrayForEach(group, J("refill")) {
        char line[300] = "";
        cJSON *slot;
        cJSON_ArrayForEach(slot, group) {
            if (*line)
                strncat(line, " → ", sizeof(line) - strlen(line) - 1);
            strncat(line, slot_label(slot->valuestring), sizeof(line) - strlen(line) - 1);
        }
        row(list, y, line, "", "Matching material and colour", NULL);
        y += SETTINGS_ROW_PITCH;
    }
    if (!y)
        text(list,
             "No matching loaded filaments. Refill becomes available when two or more loaded slots "
             "have the same material and colour.",
             20, 60, 620, &font_ui_20, true);
}
static void network_view(void) {
    ui_tabs(true, "network");
    const char *lan = S("platform.interfaces.ethernet.address");
    row(app.content, 100, "Ethernet", "›", *lan ? lan : "Cable disconnected", "route:ethernet");
    const char *wifi = S("wifi.ssid");
    row(app.content, 212, "Wi-Fi", "›", *wifi ? wifi : "Not connected", "route:wifi");
}
static void ethernet_view(void) {
    row(app.content, 90, "Connection",
        *S("platform.interfaces.ethernet.address") ? "Connected" : "Cable disconnected", "", NULL);
    if (*S("platform.interfaces.ethernet.address")) {
        row(app.content, 90 + SETTINGS_ROW_PITCH, "IP address",
            S("platform.interfaces.ethernet.address"), "", NULL);
        row(app.content, 90 + 2 * SETTINGS_ROW_PITCH, "Address assignment", "Automatic (DHCP)", "",
            NULL);
    }
}
static cJSON *active_operation(const char *action) {
    cJSON *op;
    cJSON_ArrayForEach(op, J("operations")) if (!strcmp(jstr(op, "action", ""), action)) return op;
    op = J("operation");
    return !strcmp(jstr(op, "action", ""), action) ? op : NULL;
}
static void network_progress(const char *title, const char *detail) {
    lv_obj_t *group = box(app.content, 0, 0, 674, 1, false);
    lv_obj_clear_flag(group, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_bg_opa(group, 0, 0);
    lv_obj_set_style_border_width(group, 0, 0);
    lv_obj_set_height(group, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(group, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(group, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(group, 20, 0);
    lv_obj_t *spinner = lv_spinner_create(group, 1000, 60);
    lv_obj_set_size(spinner, 56, 56);
    lv_obj_set_style_arc_width(spinner, 4, LV_PART_MAIN);
    lv_obj_set_style_arc_width(spinner, 4, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(spinner, colour("343e4b"), LV_PART_MAIN);
    lv_obj_set_style_arc_color(spinner, app.accent, LV_PART_INDICATOR);
    lv_obj_clear_flag(spinner, LV_OBJ_FLAG_CLICKABLE);
    centered(text(group, title, 0, 0, 640, &font_ui_24, false));
    if (detail && *detail)
        centered(text(group, detail, 0, 0, 640, &font_ui_18, true));
    lv_obj_update_layout(group);
    lv_obj_align(group, LV_ALIGN_CENTER, 0, 20);
}
static void wifi_view(void) {
    cJSON *join = active_operation("wifi_join");
    if (app.wifi_joining || join ||
        (B("wifi.busy") && !strncmp(S("wifi.message"), "Connecting", 10))) {
        char detail[180];
        const char *ssid = *app.wifi_ssid ? app.wifi_ssid : jstr(join, "ssid", "selected network");
        snprintf(detail, sizeof(detail), "Connecting to %s", ssid);
        network_progress("Connecting to Wi-Fi", detail);
        return;
    }
    row(app.content, 90, "Network", *S("wifi.ssid") ? S("wifi.ssid") : "Not connected", "", NULL);
    row(app.content, 90 + SETTINGS_ROW_PITCH, "IP address", S("wifi.ip_address"), "", NULL);
    row(app.content, 90 + 2 * SETTINGS_ROW_PITCH, "Signal", S("wifi.rssi"), "", NULL);
    footer(NULL, NULL, "Connect to Wi-Fi", "scan_wifi");
}
static void wifi_networks(void) {
    if (active_operation("wifi_scan") ||
        (B("wifi.busy") && !strncmp(S("wifi.message"), "Scanning", 8))) {
        network_progress("Scanning available networks", "Please wait…");
        return;
    }
    lv_obj_t *list = scroll_area(88, 388);
    cJSON *net;
    int y = 0;
    cJSON_ArrayForEach(net, J("wifi.networks")) {
        cJSON *ssid = cJSON_GetArrayItem(net, 0), *signal = cJSON_GetArrayItem(net, 1),
              *secure = cJSON_GetArrayItem(net, 2);
        if (!cJSON_IsString(ssid))
            continue;
        char detail[100];
        snprintf(detail, sizeof(detail), "%.0f dBm · %s", signal ? signal->valuedouble : 0,
                 cJSON_IsTrue(secure) ? "Secured" : "Open network");
        cJSON *r = cJSON_CreateObject();
        cJSON_AddStringToObject(r, "ssid", ssid->valuestring);
        cJSON_AddBoolToObject(r, "secured", cJSON_IsTrue(secure));
        char *json = encode(r);
        char *action = malloc(strlen(json) + 20);
        sprintf(action, "wifi_select:%s", json);
        row(list, y, ssid->valuestring, "›", detail, action);
        free(action);
        free(json);
        y += SETTINGS_ROW_PITCH;
    }
    if (!y)
        text(list, B("wifi.busy") ? "Scanning available networks…" : "No networks found", 20, 60,
             620, &font_ui_20, true);
    footer(NULL, NULL, "Scan again", "scan_wifi");
}
static void timelapse_view(void) {
    ui_tabs(true, "timelapse");
    if (!has("platform.timelapse")) {
        center_message("Time-lapse unavailable",
                       "The Moonraker time-lapse component is not installed.", NULL, NULL);
        return;
    }
    toggle_card(100, 96, "Time-lapse", NULL, B("platform.timelapse.enabled"), "toggle_timelapse");
    row(app.content, 212, "Recordings", "›", "Saved time-lapse videos", "recordings");
}
static void recordings_view(void) {
    lv_obj_t *list = scroll_area(88, 462);
    cJSON *record;
    int y = 0;
    cJSON_ArrayForEach(record, J("platform.recordings")) {
        const char *path = jstr(record, "path", "");
        cJSON *r = request("export");
        cJSON_AddStringToObject(r, "path", path);
        char *a = encode(r);
        lv_obj_t *item = box(list, 0, y, 673, 78, false);
        lv_obj_clear_flag(item, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_border_width(item, 0, 0);
        lv_obj_t *name = text(item, path, 16, 26, 482, &font_ui_18, false);
        lv_obj_set_height(name, 26);
        lv_label_set_long_mode(name, LV_LABEL_LONG_DOT);
        list_action(list, "save", 516, y + 7, B("platform.usb.mounted") ? a : NULL);
        free(a);
        r = request("delete_recording");
        cJSON_AddStringToObject(r, "path", path);
        char *payload = encode(r);
        size_t size = strlen(payload) + 16;
        a = malloc(size);
        snprintf(a, size, "file_delete:%s", payload);
        list_action(list, "trash-2", 600, y + 7, a);
        free(a);
        free(payload);
        y += 88;
    }
    if (!y)
        text(list, "No recordings available", 20, 60, 620, &font_ui_20, true);
}
static void storage_view(void) {
    ui_tabs(true, "storage");
    if (ui_unavailable("Storage changes", "changing or formatting storage"))
        return;
    row(app.content, 100, "Printer storage", S("platform.free"), "", NULL);
    row(app.content, 212, "USB drive",
        B("platform.usb.mounted") ? "›"
        : has("platform.usb")     ? "Ejected"
                                  : "Not connected",
        S("platform.usb.model"), B("platform.usb.mounted") ? "route:usb" : NULL);
}
static void usb_view(void) {
    if (ui_unavailable("USB storage changes", "ejecting or formatting USB"))
        return;
    if (!has("platform.usb") || cJSON_IsNull(J("platform.usb"))) {
        center_message("USB drive not connected", "Connect a USB drive.", NULL, NULL);
        return;
    }
    if (!B("platform.usb.mounted")) {
        center_message(B("platform.usb.ejected") ? "USB drive ejected" : "USB drive not mounted",
                       B("platform.usb.ejected")
                           ? "You can remove the USB drive."
                           : "Connect a mounted USB drive to use storage actions.",
                       NULL, NULL);
        return;
    }
    row(app.content, 90, "Drive", S("platform.usb.model"), "", NULL);
    row(app.content, 90 + SETTINGS_ROW_PITCH, "Available space", S("platform.usb_free"), "", NULL);
    footer("Eject USB", "{\"action\":\"usb_eject\"}", "Format USB…", "usb_format");
}
static void system_view(void) {
    ui_tabs(true, "system");
    const char *labels[] = {"Machine information", "Software version", "Time zone", "Help & logs",
                            "Restart"};
    const char *routes[] = {"machine", "software", "timezone", "help", "restart"};
    int width = (674 - SETTINGS_CARD_GAP) / 2;
    for (int i = 0; i < 5; i++) {
        char action[80];
        snprintf(action, sizeof(action), "route:%s", routes[i]);
        lv_obj_t *card =
            button(app.content, "", (i % 2) * (width + SETTINGS_CARD_GAP),
                   100 + (i / 2) * (100 + SETTINGS_CARD_GAP), width, 100, action, false);
        lv_obj_set_style_border_width(card, 0, 0);
        lv_obj_t *label = text(card, labels[i], 16, 38, 262, &font_ui_20, false);
        lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
        lv_obj_set_height(label, 28);
        icon(card, "chevron-right", width - 34, 36, 28, colour("a4aebb"));
    }
}
static void machine_view(void) {
    lv_obj_t *list = scroll_area(77, 462);
    int y = 0;
    char value[240];
#define INFO(label, value)                                                                         \
    do {                                                                                           \
        row(list, y, label, value, "", NULL);                                                      \
        y += SETTINGS_ROW_PITCH;                                                                   \
    } while (0)
    INFO("Model", S("model"));
    INFO("Language", "English");
    INFO("Serial number", *S("platform.serial") ? S("platform.serial") : "Unavailable");
    INFO("Klipper", S("status.webhooks.state"));
    if (has("status.k2_motor_bus"))
        INFO("Closed-loop motors", B("status.k2_motor_bus.motors_ready") ? "Ready" : "Not ready");
    INFO("Homed axes", *S("status.toolhead.homed_axes") ? S("status.toolhead.homed_axes") : "None");
    if (fitted()) {
        int slots = cJSON_GetArraySize(J("status.hark_cfs_slots.map_targets"));
        snprintf(value, sizeof(value), "%d configured slots", slots > 0 ? slots - 1 : 0);
        INFO("CFS driver", value);
    }
    const char *address =
        *S("wifi.ip_address") ? S("wifi.ip_address") : S("platform.interfaces.ethernet.address");
    INFO("Network address", address);
    if (has("settings.input_shaper")) {
        snprintf(value, sizeof(value), "%s %.1f Hz / %s %.1f Hz",
                 S("settings.input_shaper.shaper_type_x"), N("settings.input_shaper.shaper_freq_x"),
                 S("settings.input_shaper.shaper_type_y"),
                 N("settings.input_shaper.shaper_freq_y"));
        INFO("Input shaper X / Y", value);
    }
    if (has("settings.extruder.pid_kp")) {
        snprintf(value, sizeof(value), "%.2f / %.2f / %.2f", N("settings.extruder.pid_kp"),
                 N("settings.extruder.pid_ki"), N("settings.extruder.pid_kd"));
        INFO("Nozzle PID", value);
    }
    if (has("settings.printer.max_z_velocity")) {
        snprintf(value, sizeof(value), "%.1f mm/s · %.0f mm/s²",
                 N("settings.printer.max_z_velocity"), N("settings.printer.max_z_accel"));
        INFO("Z limits", value);
    }
    if (*S("platform.stock_firmware"))
        INFO("Stock base firmware", S("platform.stock_firmware"));
#undef INFO
}
static void timezone_view(void) {
    const char *current = *S("prefs.timezone") ? S("prefs.timezone") : S("platform.timezone");
    const char *name = *current ? current : "Not available", *detail = "";
    cJSON *zone;
    cJSON_ArrayForEach(zone, J("timezone_presets")) {
        if (!strcmp(jstr(zone, "id", ""), current)) {
            name = jstr(zone, "name", current);
            detail = jstr(zone, "label", "");
            break;
        }
    }
    char current_summary[240];
    snprintf(current_summary, sizeof(current_summary), *detail ? "%s · %s" : "%s", name, detail);
    text(app.content, "Current:", 0, 82, 70, &font_ui_14, true);
    lv_obj_t *summary = text(app.content, current_summary, 72, 78, 601, &font_ui_18, false);
    lv_obj_set_height(summary, 28);
    lv_label_set_long_mode(summary, LV_LABEL_LONG_DOT);
    lv_obj_t *list = scroll_area(114, 470);
    int y = 0, selected_y = -1;
    cJSON_ArrayForEach(zone, J("timezone_presets")) {
        const char *id = jstr(zone, "id", "");
        bool selected = !strcmp(id, app.timezone_draft);
        if (selected)
            selected_y = y;
        char action[160], description[200];
        snprintf(action, sizeof(action), "timezone_pick:%s", id);
        const char *cities = jstr(zone, "cities", "");
        snprintf(description, sizeof(description), *cities ? "%s · %s" : "%s",
                 jstr(zone, "label", ""), cities);
        row(list, y, jstr(zone, "name", jstr(zone, "label", "")),
            !selected && !strcmp(id, current) ? "Current" : "", description, action);
        lv_obj_t *item = lv_obj_get_child(list, -1);
        lv_obj_set_height(item, 72);
        if (selected) {
            lv_obj_set_style_bg_color(item, lv_color_mix(app.accent, colour("151a22"), 38), 0);
            lv_obj_set_style_bg_opa(item, LV_OPA_COVER, 0);
            lv_obj_set_style_radius(item, 8, 0);
            lv_obj_set_style_border_color(item, app.accent, 0);
            lv_obj_set_style_border_side(item, LV_BORDER_SIDE_FULL, 0);
            bool pending = strcmp(id, current) != 0;
            if (pending) {
                lv_obj_t *subtitle = lv_obj_get_child(item, 1);
                lv_obj_set_width(subtitle, 540);
                lv_obj_set_height(subtitle, 20);
                lv_label_set_long_mode(subtitle, LV_LABEL_LONG_DOT);
                lv_obj_t *apply = button(item, "Apply", 571, 14, 88, 60, "timezone_apply", true);
                /* A smaller visible button retains a 52px-high touch target. */
                lv_obj_set_size(apply, 88, 44);
                lv_obj_set_pos(apply, 571, 14);
                lv_obj_set_ext_click_area(apply, 4);
            }
        }
        y += 76;
    }
    if (!y)
        text(list, "Time zones unavailable", 12, 30, 640, &font_ui_20, true);
    else if (selected_y >= 0) {
        lv_obj_update_layout(list);
        lv_obj_scroll_to_y(list, selected_y > 76 ? selected_y - 76 : 0, LV_ANIM_OFF);
    }
}

static void history_view(bool errors) {
    lv_obj_t *list = scroll_area(88, 462);
    cJSON *events = J("events");
    int y = 0;
    if (!errors) {
        cJSON *operation;
        cJSON_ArrayForEach(operation, J("operations")) {
            row(list, y, jstr(operation, "label", "Operation"), "In progress",
                jstr(operation, "detail", ""), NULL);
            y += SETTINGS_ROW_PITCH;
        }
    }
    for (int i = cJSON_GetArraySize(events) - 1; i >= 0; i--) {
        cJSON *e = cJSON_GetArrayItem(events, i);
        if (errors && !jbool(e, "fault", false))
            continue;
        time_t stamp = (time_t)jnum(e, "time", 0);
        struct tm tm;
        localtime_r(&stamp, &tm);
        char title[260], when[30];
        strftime(when, sizeof(when), "%H:%M", &tm);
        snprintf(title, sizeof(title), "%s  %s", when, jstr(e, "title", ""));
        row(list, y, title, "", jstr(e, "detail", ""), NULL);
        y += SETTINGS_ROW_PITCH;
    }
    if (!y)
        text(list, errors ? "No errors recorded" : "No status messages yet", 20, 60, 620,
             &font_ui_20, true);
}
static void plate_event(lv_event_t *e) {
    lv_obj_t *object = lv_event_get_target(e);
    lv_area_t area;
    lv_obj_get_coords(object, &area);
    double x0 = N("settings.bed_mesh.mesh_min.0"), y0 = N("settings.bed_mesh.mesh_min.1"),
           x1 = N("settings.bed_mesh.mesh_max.0"), y1 = N("settings.bed_mesh.mesh_max.1");
    if (x1 <= x0 || y1 <= y0)
        return;
    double span = fmax(x1 - x0, y1 - y0) / app.zoom;
    double pixels = lv_obj_get_width(object) - 2;
    int code = lv_event_get_code(e);
    static lv_point_t origin;
    static double ox, oy;
    static bool drag;
    if (code == LV_EVENT_PRESSED) {
        lv_indev_get_point(lv_indev_get_act(), &origin);
        ox = app.plate_x;
        oy = app.plate_y;
        drag = false;
        return;
    }
    if (code == LV_EVENT_PRESSING) {
        lv_point_t p;
        lv_indev_get_point(lv_indev_get_act(), &p);
        if (abs(p.x - origin.x) + abs(p.y - origin.y) > 8)
            drag = true;
        if (drag) {
            double maximum = fmax(x1 - x0, y1 - y0) - span;
            app.plate_x = fmax(0, fmin(maximum, ox - (p.x - origin.x) * span / pixels));
            app.plate_y = fmax(0, fmin(maximum, oy + (p.y - origin.y) * span / pixels));
            lv_obj_invalidate(object);
        }
        return;
    }
    cJSON *part;
    cJSON_ArrayForEach(part, J("status.exclude_object.objects")) {
        cJSON *poly = cJSON_GetObjectItemCaseSensitive(part, "polygon");
        int n = cJSON_GetArraySize(poly);
        if (n < 3 || n > 1024)
            continue;
        lv_point_t *points = malloc(sizeof(lv_point_t) * n);
        if (!points)
            return;
        for (int i = 0; i < n; i++) {
            cJSON *p = cJSON_GetArrayItem(poly, i);
            points[i].x = area.x1 + (jnum(p, "0", x0) - x0 - app.plate_x) * pixels / span;
            points[i].y = area.y1 + pixels - (jnum(p, "1", y0) - y0 - app.plate_y) * pixels / span;
        }
        const char *name = jstr(part, "name", "");
        bool selected = false, excluded = false;
        cJSON *entry;
        cJSON_ArrayForEach(entry, app.object_selection) if (cJSON_IsString(entry) &&
                                                            !strcmp(entry->valuestring, name))
            selected = true;
        cJSON_ArrayForEach(
            entry, J("status.exclude_object.excluded_objects")) if (cJSON_IsString(entry) &&
                                                                    !strcmp(entry->valuestring,
                                                                            name)) excluded = true;
        if (code == LV_EVENT_DRAW_MAIN) {
            lv_draw_ctx_t *ctx = lv_event_get_draw_ctx(e);
            lv_draw_line_dsc_t line;
            lv_draw_line_dsc_init(&line);
            line.color = excluded ? colour("777777") : selected ? app.accent : colour("a4aebb");
            line.width = selected ? 3 : 2;
            for (int i = 0; i < n; i++)
                lv_draw_line(ctx, &line, &points[i], &points[(i + 1) % n]);
        } else if (code == LV_EVENT_RELEASED && !drag && !excluded) {
            lv_point_t p;
            lv_indev_get_point(lv_indev_get_act(), &p);
            bool inside = false;
            for (int i = 0, j = n - 1; i < n; j = i++) {
                if (((points[i].y > p.y) != (points[j].y > p.y)) &&
                    (p.x < (double)(points[j].x - points[i].x) * (p.y - points[i].y) /
                                   (points[j].y - points[i].y) +
                               points[i].x))
                    inside = !inside;
            }
            if (inside) {
                if (selected) {
                    int index = 0;
                    cJSON_ArrayForEach(entry, app.object_selection) {
                        if (cJSON_IsString(entry) && !strcmp(entry->valuestring, name)) {
                            cJSON_DeleteItemFromArray(app.object_selection, index);
                            break;
                        }
                        index++;
                    }
                } else
                    cJSON_AddItemToArray(app.object_selection, cJSON_CreateString(name));
                lv_obj_invalidate(object);
                app.dirty = true;
                free(points);
                return;
            }
        }
        free(points);
    }
}
static void objects_view(void) {
    if (!app.printing && !app.paused) {
        center_message("No print in progress", "Object exclusion becomes available during a print.",
                       "Back to print", "route:home");
        return;
    }
    if (!cJSON_GetArraySize(J("status.exclude_object.objects"))) {
        center_message("Object definitions unavailable",
                       "This G-code does not contain selectable object contours.", "Back to print",
                       "route:home");
        return;
    }
    lv_obj_t *plate = box(app.content, 138, 19, 352, 352, false);
    lv_obj_add_flag(plate, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(plate, plate_event, LV_EVENT_ALL, NULL);
    button(app.content, "icon:zoom-in", 514, 27, 64, 64, "zoom:in", false);
    button(app.content, "icon:zoom-out", 514, 103, 64, 64, "zoom:out", false);
    button(app.content, "icon:maximize", 514, 179, 64, 64, "zoom:fit", false);
    button_fixed(app.content, "Back", 0, 402, 296, 64, "back", false);
    bool selected = cJSON_GetArraySize(app.object_selection) > 0;
    button_fixed(app.content, "Skip selected", 378, 402, 296, 64, selected ? "exclude" : NULL,
                 selected);
}
void views_render(void) {
    const char *p = app.route;
    const char *title = "Settings";
    if (!strcmp(p, "move") || !strcmp(p, "heat") || !strcmp(p, "fans") || !strcmp(p, "filament") ||
        !strcmp(p, "tune") || !strcmp(p, "calibration"))
        title = "Printer controls";
    else if (!strcmp(p, "mesh"))
        title = "Bed mesh";
    else if (!strcmp(p, "files"))
        title = "Files";
    else if (!strcmp(p, "file"))
        title = "Print file";
    else if (!strcmp(p, "mapping_pick")) {
        static char mapping_title[128];
        const char *type = jstr(mapping_filament(), "type", "");
        snprintf(mapping_title, sizeof(mapping_title),
                 *type ? "Select filament: %s" : "Select filament", type);
        title = mapping_title;
    } else if (!strncmp(p, "mapping", 7))
        title = "Select filaments";
    else if (!strcmp(p, "edit"))
        title = "Filament settings";
    else if (!strcmp(p, "materials"))
        title = "Choose material";
    else if (!strcmp(p, "colors"))
        title = "Choose colour";
    else if (!strcmp(p, "refill"))
        title = "CFS preferences";
    else if (!strcmp(p, "refill_sequence"))
        title = "Auto refill sequence";
    else if (!strcmp(p, "display"))
        title = "Display & appearance";
    else if (!strcmp(p, "timers"))
        title = "Light & idle timers";
    else if (!strcmp(p, "wifi"))
        title = "Wi-Fi";
    else if (!strcmp(p, "networks"))
        title = "Choose Wi-Fi";
    else if (!strcmp(p, "ethernet"))
        title = "Ethernet";
    else if (!strcmp(p, "recordings"))
        title = "Time-lapse recordings";
    else if (!strcmp(p, "usb"))
        title = "USB drive";
    else if (!strcmp(p, "machine"))
        title = "Machine information";
    else if (!strcmp(p, "software"))
        title = "Software version";
    else if (!strcmp(p, "timezone"))
        title = "Time zone";
    else if (!strcmp(p, "restart"))
        title = "Restart";
    else if (!strcmp(p, "save"))
        title = "Save configuration";
    else if (!strcmp(p, "history"))
        title = "Status history";
    else if (!strcmp(p, "errors"))
        title = "Recent errors";
    else if (!strcmp(p, "help"))
        title = "Help & logs";
    else if (!strcmp(p, "objects"))
        title = "Print objects";
    else if (!strcmp(p, "language"))
        title = "Language";
    char edit_title[80];
    if (!strcmp(p, "edit")) {
        snprintf(edit_title, sizeof(edit_title), !strcmp(app.selected_slot, "E") ? "%s" : "Slot %s",
                 slot_label(app.selected_slot));
        title = edit_title;
    }
    if (strcmp(p, "objects") && strcmp(p, "file") && strcmp(p, "mapping"))
        ui_header(title);
    if (!strcmp(p, "move"))
        move_view();
    else if (!strcmp(p, "mesh"))
        mesh_view();
    else if (!strcmp(p, "heat"))
        heat_view();
    else if (!strcmp(p, "fans"))
        fans_view();
    else if (!strcmp(p, "filament"))
        filament_view();
    else if (!strcmp(p, "edit"))
        edit_view();
    else if (!strcmp(p, "materials"))
        materials_view();
    else if (!strcmp(p, "colors"))
        colors_view();
    else if (!strcmp(p, "tune"))
        tune_view();
    else if (!strcmp(p, "calibration"))
        calibration_view();
    else if (!strcmp(p, "files"))
        files_view();
    else if (!strcmp(p, "file"))
        file_view();
    else if (!strcmp(p, "mapping"))
        mapping_view();
    else if (!strcmp(p, "mapping_pick"))
        mapping_pick();
    else if (!strcmp(p, "settings"))
        settings_view();
    else if (!strcmp(p, "display"))
        display_view();
    else if (!strcmp(p, "timers"))
        timers_view();
    else if (!strcmp(p, "refill"))
        refill_view();
    else if (!strcmp(p, "refill_sequence"))
        refill_sequence();
    else if (!strcmp(p, "network"))
        network_view();
    else if (!strcmp(p, "wifi"))
        wifi_view();
    else if (!strcmp(p, "ethernet"))
        ethernet_view();
    else if (!strcmp(p, "networks"))
        wifi_networks();
    else if (!strcmp(p, "timelapse"))
        timelapse_view();
    else if (!strcmp(p, "recordings"))
        recordings_view();
    else if (!strcmp(p, "storage"))
        storage_view();
    else if (!strcmp(p, "usb"))
        usb_view();
    else if (!strcmp(p, "system"))
        system_view();
    else if (!strcmp(p, "machine"))
        machine_view();
    else if (!strcmp(p, "timezone"))
        timezone_view();
    else if (!strcmp(p, "objects"))
        objects_view();
    else if (!strcmp(p, "history"))
        history_view(false);
    else if (!strcmp(p, "errors"))
        history_view(true);
    else if (!strcmp(p, "restart")) {
        row(app.content, 90, "Restart Klipper", "›", "", "restart:klipper");
        row(app.content, 90 + SETTINGS_ROW_PITCH, "Firmware restart", "›", "", "restart:firmware");
    } else if (!strcmp(p, "software")) {
        row(app.content, 90, "OpenKlipper",
            *S("platform.version") ? S("platform.version") : "Installed custom fork", "", NULL);
        row(app.content, 90 + SETTINGS_ROW_PITCH, "Touchscreen", "Native LVGL", "", NULL);
        row(app.content, 90 + 2 * SETTINGS_ROW_PITCH, "Build",
            *S("platform.screen_version") ? S("platform.screen_version") : "Development", "", NULL);
    } else if (!strcmp(p, "language")) {
        row(app.content, 90, "Language", "English", "", NULL);
    } else if (!strcmp(p, "help")) {
        row(app.content, 90, "Recent errors", "›", "Messages and recovery details", "route:errors");
        row(app.content, 90 + SETTINGS_ROW_PITCH, "Status history", "›", "", "route:history");
        row(app.content, 90 + 2 * SETTINGS_ROW_PITCH, "Export logs to USB", "›", "", "export_logs");
    } else if (!strcmp(p, "save")) {
        if (!ui_unavailable("Saving configuration", "saving configuration and restarting"))
            center_message("Save configuration?",
                           "Saves pending calibration changes and restarts Klipper. Homing will be "
                           "required afterward.",
                           "Save and restart", "save_config");
    }
}
static double temp_value, temp_min, temp_max;
static char temp_key[40], temp_title[100];
static bool temp_draft_editor;
static void temperature_sheet(void) {
    modal_frame(temp_title, 120, 61, 560, 358, false);
    char value[40];
    snprintf(value, sizeof(value), temp_value ? "%g°C" : "Off", temp_value);
    button(app.body, "−5", 24, 92, 64, 80, "temp_delta:-5", false);
    lv_obj_t *window = button(app.body, "", 98, 92, 364, 80, "temp_input", false);
    lv_obj_set_style_bg_color(window, colour("07090c"), 0);
    centered(text(window, "Target", 0, 13, lv_obj_get_width(window), &font_ui_15, true));
    lv_point_t metrics;
    lv_txt_get_size(&metrics, value, &font_ui_25, 0, 0, 1000, LV_TEXT_FLAG_NONE);
    int x = (lv_obj_get_width(window) - metrics.x - 28) / 2;
    text(window, value, x, 36, metrics.x + 2, &font_ui_25, false);
    icon(window, "pencil", x + metrics.x + 12, 44, 16, colour("a4aebb"));
    button(app.body, "+5", 472, 92, 64, 80, "temp_delta:5", false);
    double nozzle[] = {0, 140, 220, 250}, bed[] = {0, 35, 60, 90}, chamber[] = {0, 35, 45, 50, 60},
           filament[] = {200, 220, 250, 280};
    double *presets = temp_draft_editor                 ? filament
                      : !strcmp(temp_key, "chamber")    ? chamber
                      : !strcmp(temp_key, "heater_bed") ? bed
                                                        : nozzle;
    int preset_count = presets == chamber ? 5 : 4;
    int count = 0;
    for (int i = 0; i < preset_count; i++)
        if (presets[i] >= temp_min && presets[i] <= temp_max)
            count++;
    int left = (560 - (count * 82 - 10)) / 2 - 1, index = 0;
    for (int i = 0; i < preset_count; i++) {
        if (presets[i] < temp_min || presets[i] > temp_max)
            continue;
        char a[60], label[40];
        snprintf(a, sizeof(a), "temp_draft:%g", presets[i]);
        snprintf(label, sizeof(label), presets[i] ? "%g°C" : "Off", presets[i]);
        button(app.body, label, left + index++ * 82, 186, 72, 60, a, temp_value == presets[i]);
    }
    button(app.body, "Cancel", 24, 282, 230, 56, "close", false);
    button(app.body, "Set temperature", 306, 282, 230, 56, "temp_set", true);
}
static lv_obj_t *keyboard_input;
static bool keyboard_symbols, keyboard_shift, keyboard_caps;
static uint64_t keyboard_shift_at;
static lv_obj_t *keyboard_key(const char *label, const char *action, int x, int y, int width,
                              int height, bool selected) {
    lv_obj_t *key = button_fixed(app.body, "", x, y, width, height, action, selected);
    if (!strncmp(label, "icon:", 5)) {
        icon(key, label + 5, 0, 0, 28, selected ? app.accent : colour("f0f3f7"));
        lv_obj_align(lv_obj_get_child(key, -1), LV_ALIGN_CENTER, 0, 0);
    } else {
        lv_obj_t *caption =
            text(key, label, 0, 0, width, strlen(label) == 1 ? &font_ui_24 : &font_ui_20, false);
        centered(caption);
        lv_obj_align(caption, LV_ALIGN_CENTER, 0, 0);
    }
    return key;
}
static void keyboard_draw(void) {
    modal_frame(app.keyboard_title, 0, 0, WIDTH, HEIGHT, false);
    lv_obj_set_style_radius(app.body, 0, 0);
    lv_obj_set_style_border_width(app.body, 0, 0);
    app.keyboard_visible = true;
    keyboard_input = lv_textarea_create(app.body);
    /* Single-line mode resets height to content size. Apply the final field
     * dimensions and padding afterward, keeping text clear of both borders. */
    lv_textarea_set_one_line(keyboard_input, true);
    lv_obj_set_style_text_font(keyboard_input, &font_ui_24, 0);
    lv_obj_set_style_pad_left(keyboard_input, 18, 0);
    lv_obj_set_style_pad_right(keyboard_input, !strcmp(app.keyboard_kind, "wifi") ? 92 : 18, 0);
    lv_obj_set_style_pad_top(keyboard_input, 20, 0);
    lv_obj_set_style_pad_bottom(keyboard_input, 21, 0);
    lv_obj_set_style_bg_color(keyboard_input, colour("10151c"), 0);
    lv_obj_set_style_bg_opa(keyboard_input, LV_OPA_COVER, 0);
    lv_obj_set_style_text_color(keyboard_input, colour("f0f3f7"), 0);
    lv_obj_set_style_border_color(keyboard_input, colour("596471"), 0);
    lv_obj_set_style_border_width(keyboard_input, 1, 0);
    lv_obj_set_style_radius(keyboard_input, 12, 0);
    lv_obj_set_pos(keyboard_input, 16, 70);
    lv_obj_set_size(keyboard_input, 768, 76);
    lv_textarea_set_text(keyboard_input, app.keyboard_value);
    lv_textarea_set_cursor_pos(keyboard_input, LV_TEXTAREA_CURSOR_LAST);
    lv_obj_add_state(keyboard_input, LV_STATE_FOCUSED);
    lv_obj_set_style_text_color(keyboard_input, colour("828b98"), LV_PART_TEXTAREA_PLACEHOLDER);
    lv_obj_set_style_bg_color(keyboard_input, colour("f0f3f7"), LV_PART_CURSOR);
    lv_obj_set_style_bg_opa(keyboard_input, LV_OPA_TRANSP, LV_PART_CURSOR);
    lv_obj_set_style_border_side(keyboard_input, LV_BORDER_SIDE_LEFT, LV_PART_CURSOR);
    lv_obj_set_style_border_width(keyboard_input, 2, LV_PART_CURSOR);
    lv_obj_set_style_border_color(keyboard_input, colour("f0f3f7"), LV_PART_CURSOR);
    lv_obj_set_style_width(keyboard_input, 2, LV_PART_CURSOR);
    lv_obj_set_style_anim_time(keyboard_input, 500, LV_PART_CURSOR);
    lv_textarea_set_cursor_pos(keyboard_input, LV_TEXTAREA_CURSOR_LAST);

    lv_textarea_set_password_mode(keyboard_input,
                                  !strcmp(app.keyboard_kind, "wifi") && !app.password_visible);
    if (!strcmp(app.keyboard_kind, "wifi")) {
        lv_textarea_set_placeholder_text(keyboard_input, "  Wi-Fi password");
        lv_textarea_set_max_length(keyboard_input, 63);
        lv_obj_t *eye = keyboard_key(app.password_visible ? "icon:eye" : "icon:eye-off", "key_eye",
                                     710, 76, 64, 64, false);
        lv_obj_set_style_bg_opa(eye, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(eye, 0, 0);
    }
    const char *letters[] = {"qwertyuiop", "asdfghjkl", "zxcvbnm"};
    const char *symbols[] = {"1234567890!@#$", "%^&*()-_=+[]{}", ";:~`|\\<>?/\"',."};
    for (int row = 0; row < 3; row++) {
        const char *characters = keyboard_symbols ? symbols[row] : letters[row];
        int count = strlen(characters), gap = keyboard_symbols ? 3 : 6;
        int width = (768 - (count - 1) * gap) / count;
        int start = (WIDTH - (count * width + (count - 1) * gap)) / 2;
        for (int column = 0; column < count; column++) {
            char caption[2] = {characters[column], 0}, action[20];
            if (!keyboard_symbols && keyboard_shift && caption[0] >= 'a' && caption[0] <= 'z')
                caption[0] -= 32;
            snprintf(action, sizeof(action), "key:%s", caption);
            keyboard_key(caption, action, start + column * (width + gap), 158 + row * 62, width, 56,
                         false);
        }
    }
    keyboard_key(keyboard_symbols ? "ABC" : "?123", "key_symbols", 16, 344, 112, 56, false);
    if (!keyboard_symbols) {
        lv_obj_t *shift =
            keyboard_key("icon:arrow-big-up", "key_shift", 136, 344, 112, 56, keyboard_shift);
        if (keyboard_caps) {
            lv_obj_t *line = box(shift, 42, 46, 28, 3, false);
            lv_obj_clear_flag(line, LV_OBJ_FLAG_CLICKABLE);
            lv_obj_set_style_border_width(line, 0, 0);
            lv_obj_set_style_bg_color(line, app.accent, 0);
        }
    }
    keyboard_key("Space", "key: ", keyboard_symbols ? 136 : 256, 344, keyboard_symbols ? 524 : 404,
                 56, false);
    keyboard_key("icon:delete", "key_back", 668, 344, 116, 56, false);
    keyboard_key("Cancel", "close", 16, 412, 378, 56, false);
    keyboard_key(!strcmp(app.keyboard_kind, "wifi")        ? "Connect"
                 : !strncmp(app.keyboard_kind, "mesh_", 5) ? "Save"
                                                           : "Set colour",
                 "key_done", 406, 412, 378, 56, true);
}
void ui_keyboard(const char *title, const char *kind, const char *value) {
    snprintf(app.keyboard_title, sizeof(app.keyboard_title), "%s", title);
    snprintf(app.keyboard_kind, sizeof(app.keyboard_kind), "%s", kind);
    snprintf(app.keyboard_value, sizeof(app.keyboard_value), "%s", value);
    keyboard_symbols = keyboard_shift = keyboard_caps = false;
    keyboard_shift_at = 0;
    app.password_visible = false;
    keyboard_draw();
}
bool views_control_key(const char *action, char *key, size_t size) {
    const char *a = !strcmp(action, "confirmed") ? app.confirm : action;
    if (mesh_control_key(a, key, size))
        return true;
    if (!strncmp(a, "file_open:", 10))
        a += 10;
    else if (!strncmp(a, "file_delete:", 12)) {
        cJSON *r = cJSON_Parse(a + 12);
        snprintf(key, size, "%s",
                 !strcmp(jstr(r, "action", ""), "delete_recording") ? "delete_recording"
                                                                    : "delete_file");
        if (!strcmp(key, "delete_file") && (app.printing || app.paused) &&
            !strcmp(jstr(r, "path", ""), S("status.print_stats.filename")))
            snprintf(key, size, "delete_file_current");
        cJSON_Delete(r);
        return true;
    }
    if (*a == '{') {
        cJSON *r = cJSON_Parse(a);
        if (!r)
            return false;
        const char *name = jstr(r, "action", "");
        const char *arg = !strcmp(name, "temperature") ? jstr(r, "heater", "")
                          : !strcmp(name, "fan")       ? jstr(r, "fan", "")
                          : (!strcmp(name, "jog") || !strcmp(name, "position"))
                              ? jstr(r, "axis", "")
                          : !strcmp(name, "preference") ? jstr(r, "key", "")
                                                        : "";
        snprintf(key, size, *arg ? "%s:%s" : "%s", name, arg);
        if (!strcmp(name, "delete_file") && (app.printing || app.paused) &&
            !strcmp(jstr(r, "path", ""), S("status.print_stats.filename")))
            snprintf(key, size, "delete_file_current");
        cJSON_Delete(r);
        return *key != 0;
    }
    if (!strncmp(a, "step:", 5) || !strcmp(a, "open_files")) {
        snprintf(key, size, "%s", !strcmp(a, "open_files") ? "home_files" : "move_step");
        return true;
    }
    if (!strncmp(a, "jog:", 4) || !strncmp(a, "position:", 9)) {
        bool jog = !strncmp(a, "jog:", 4);
        snprintf(key, size, "%s:%c", jog ? "jog" : "position", a[jog ? 4 : 9]);
        return true;
    }
    if (!strncmp(a, "temperature:", 12) || !strncmp(a, "fan:", 4)) {
        snprintf(key, size, "%s", a);
        return true;
    }
    if (!strncmp(a, "plr:", 4)) {
        snprintf(key, size, "plr");
        return true;
    }
    if (!strcmp(a, "key_done") && !strcmp(app.keyboard_kind, "wifi")) {
        snprintf(key, size, "wifi_join");
        return true;
    }
    if (!strcmp(a, "route:materials") || !strcmp(a, "route:colors") || !strcmp(a, "color_hex") ||
        !strncmp(a, "material:", 9) || !strncmp(a, "color:", 6) ||
        (!strcmp(a, "key_done") && !strcmp(app.keyboard_kind, "color"))) {
        snprintf(key, size, "slot_save");
        return true;
    }
    if (!strcmp(a, "temp_set") || !strcmp(a, "temp_input") || !strncmp(a, "temp_delta:", 11) ||
        !strncmp(a, "temp_draft:", 11)) {
        if (temp_draft_editor)
            snprintf(key, size, "slot_save");
        else
            snprintf(key, size, "temperature:%s", temp_key);
        return true;
    }
    if (!strcmp(a, "number:Set")) {
        if (!strcmp(app.number_kind, "temperature")) {
            snprintf(key, size, "temperature:%s", app.number_key);
            return true;
        }
        if (!strcmp(app.number_kind, "position"))
            snprintf(key, size, "position:%s", app.number_key);
        else
            snprintf(key, size, "%s", !strcmp(app.number_kind, "draft") ? "slot_save" : "tune");
        return true;
    }
    if (!strncmp(a, "restart:", 8)) {
        snprintf(key, size, "%s", !strcmp(a + 8, "firmware") ? "firmware_restart" : "restart");
        return true;
    }
    if (!strcmp(a, "calibrate:save"))
        return false;
    const char *pairs[][2] = {{"jog:", "jog"},         {"position:", "position"},
                              {"extrude:", "extrude"}, {"calibrate:", "calibrate"},
                              {"tune:", "tune"},       {"tune_input:", "tune"}};
    for (unsigned i = 0; i < sizeof(pairs) / sizeof(pairs[0]); i++)
        if (!strncmp(a, pairs[i][0], strlen(pairs[i][0]))) {
            snprintf(key, size, "%s", pairs[i][1]);
            return true;
        }
    const char *names[][2] = {{"usb_format", "usb_format"},
                              {"load", "load"},
                              {"unload", "unload"},
                              {"edit_slot", "slot_save"},
                              {"reset_spool", "slot_clear"},
                              {"save_slot", "slot_save"},
                              {"draft_temperature", "slot_save"},
                              {"motor_toggle", "motors"},
                              {"start_print", "start"},
                              {"delete_file", "delete_file"},
                              {"cancel_print", "cancel"},
                              {"save_config", "save_config"},
                              {"exclude", "exclude"},
                              {"scan_wifi", "wifi_scan"},
                              {"timezone_apply", "timezone"},
                              {"refresh_files", "files"},
                              {"recordings", "recordings"},
                              {"timelapse_on", "timelapse"},
                              {"timelapse_off", "timelapse"},
                              {"toggle_timelapse", "timelapse"}};
    for (unsigned i = 0; i < sizeof(names) / sizeof(names[0]); i++)
        if (!strcmp(a, names[i][0])) {
            snprintf(key, size, "%s", names[i][1]);
            return true;
        }
    return false;
}
void views_action(const char *a) {
    if (mesh_action(a))
        return;
    if (!strncmp(a, "timezone_pick:", 14)) {
        cJSON *zone;
        cJSON_ArrayForEach(zone, J("timezone_presets")) {
            if (!strcmp(jstr(zone, "id", ""), a + 14)) {
                snprintf(app.timezone_draft, sizeof(app.timezone_draft), "%s", a + 14);
                app.dirty = true;
                break;
            }
        }
    } else if (!strcmp(a, "timezone_apply")) {
        const char *current = *S("prefs.timezone") ? S("prefs.timezone") : S("platform.timezone");
        if (*app.timezone_draft && strcmp(app.timezone_draft, current)) {
            cJSON *r = request("timezone");
            cJSON_AddStringToObject(r, "name", app.timezone_draft);
            ui_send(r);
        }
    } else if (!strncmp(a, "extrude:", 8)) {
        cJSON *r = request("extrude");
        cJSON_AddNumberToObject(r, "value", app.step * strtod(a + 8, NULL));
        ui_send(r);
    } else if (!strncmp(a, "step:", 5)) {
        app.step = strtod(a + 5, NULL);
        app.dirty = true;
    } else if (!strncmp(a, "jog:", 4)) {
        char axis = a[4];
        double sign = strtod(a + 6, NULL);
        cJSON *r = request("jog");
        char key[2] = {axis, 0};
        cJSON_AddStringToObject(r, "axis", key);
        cJSON_AddNumberToObject(r, "value", app.step * sign);
        ui_send(r);
    } else if (!strncmp(a, "position:", 9)) {
        char axis = a[9], path[80], title[50], key[2] = {axis, 0};
        snprintf(path, sizeof(path), "axes.%c", axis + 32);
        cJSON *limits = J(path);
        if (!cJSON_IsObject(limits)) {
            ui_notice("Position unavailable", "Configured axis limits could not be read.");
            return;
        }
        snprintf(title, sizeof(title), "%c position", axis);
        int index = axis == 'X' ? 0 : axis == 'Y' ? 1 : 2;
        cJSON *p = cJSON_GetArrayItem(J("status.gcode_move.gcode_position"), index);
        ui_number(title, "position", key, cJSON_IsNumber(p) ? p->valuedouble : 0,
                  jnum(limits, "minimum", 0), jnum(limits, "maximum", 0));
    } else if (!strncmp(a, "temperature:", 12)) {
        temp_draft_editor = false;
        const char *key = a + 12;
        cJSON *h;
        cJSON_ArrayForEach(h, J("heaters")) if (!strcmp(jstr(h, "key", ""), key)) {
            cJSON *limits = cJSON_GetObjectItemCaseSensitive(h, "limits");
            if (!cJSON_IsObject(limits)) {
                ui_notice("Temperature control unavailable",
                          "Configured limits could not be read.");
                return;
            }
            snprintf(temp_title, sizeof(temp_title), "%s temperature", jstr(h, "label", "Heater"));
            snprintf(temp_key, sizeof(temp_key), "%s", key);
            temp_min = jnum(limits, "minimum", 0);
            temp_max = jnum(limits, "maximum", 0);
            temp_value = jnum(h, "target", 0);
            temperature_sheet();
            break;
        }
    } else if (!strncmp(a, "temp_delta:", 11)) {
        temp_value = fmax(temp_min, fmin(temp_max, temp_value + strtod(a + 11, NULL)));
        temperature_sheet();
    } else if (!strncmp(a, "temp_draft:", 11)) {
        temp_value = strtod(a + 11, NULL);
        temperature_sheet();
    } else if (!strcmp(a, "temp_input"))
        ui_number(temp_title, temp_draft_editor ? "draft" : "temperature", temp_key, temp_value,
                  temp_min, temp_max);
    else if (!strcmp(a, "temp_set")) {
        close_modal();
        if (temp_draft_editor) {
            cJSON_DeleteItemFromObject(app.draft, "nozzle_temp");
            cJSON_AddNumberToObject(app.draft, "nozzle_temp", temp_value);
            app.edit_dirty = true;
            app.dirty = true;
            return;
        }
        cJSON *r = request("temperature");
        cJSON_AddStringToObject(r, "heater", temp_key);
        cJSON_AddNumberToObject(r, "value", temp_value);
        ui_send(r);
    } else if (!strncmp(a, "slot:", 5)) {
        snprintf(app.selected_slot, sizeof(app.selected_slot), "%s", a + 5);
        app.dirty = true;
    } else if (!strcmp(a, "reset_spool")) {
        cJSON *clear = request("slot_clear");
        cJSON_AddStringToObject(clear, "slot", app.selected_slot);
        confirmed("Resets saved filament details. Filament is not unloaded.", clear);
    } else if (!strcmp(a, "edit_slot")) {
        cJSON_Delete(app.draft);
        app.draft = cJSON_Duplicate(slot_record(app.selected_slot), true);
        if (!app.draft)
            app.draft = cJSON_CreateObject();
        app.edit_dirty = false;
        ui_route("edit", false);
    } else if (!strcmp(a, "cancel_edit")) {
        if (app.edit_dirty) {
            snprintf(app.pending_route, sizeof(app.pending_route), "filament");
            ui_confirm("Discard unsaved filament changes?", "discard");
        } else
            ui_route("filament", false);
    } else if (!strcmp(a, "save_slot")) {
        cJSON *r = request("slot_save");
        cJSON_AddStringToObject(r, "slot", app.selected_slot);
        cJSON_AddItemToObject(r, "record", cJSON_Duplicate(app.draft, true));
        ui_send(r);
    } else if (!strncmp(a, "filter:", 7)) {
        snprintf(app.filter, sizeof(app.filter), "%s", a + 7);
        app.dirty = true;
    } else if (!strncmp(a, "material:", 9)) {
        cJSON *material = cJSON_Parse(a + 9);
        if (material) {
            const char *fields[] = {"name", "type", "nozzle_temp"};
            for (int i = 0; i < 3; i++) {
                cJSON *v = cJSON_GetObjectItemCaseSensitive(material, fields[i]);
                if (v) {
                    cJSON_DeleteItemFromObject(app.draft, fields[i]);
                    cJSON_AddItemToObject(app.draft, fields[i], cJSON_Duplicate(v, true));
                }
            }
            cJSON_Delete(material);
            app.edit_dirty = true;
            ui_route("edit", false);
        }
    } else if (!strncmp(a, "color:", 6)) {
        cJSON_DeleteItemFromObject(app.draft, "color");
        cJSON_AddStringToObject(app.draft, "color", a + 6);
        app.edit_dirty = true;
        ui_route("edit", false);
    } else if (!strcmp(a, "color_hex"))
        ui_keyboard("Hex colour", "color", jstr(app.draft, "color", ""));
    else if (!strcmp(a, "draft_temperature")) {
        cJSON *settings = J("settings.extruder");
        double min = fmax(0, jnum(settings, "min_extrude_temp", 0)),
               max = jnum(settings, "max_temp", 0);
        if (max < min) {
            ui_notice("Temperature unavailable", "Configured limits could not be read.");
            return;
        }
        temp_draft_editor = true;
        snprintf(temp_title, sizeof(temp_title), "Nozzle temperature");
        snprintf(temp_key, sizeof(temp_key), "extruder");
        temp_value = jnum(app.draft, "nozzle_temp", min);
        temp_min = min;
        temp_max = max;
        temperature_sheet();
    } else if (!strcmp(a, "load")) {
        if (!strcmp(app.selected_slot, "E") &&
            !B("status.filament_switch_sensor filament_sensor.filament_detected") &&
            !*S("status.k2_cfs.active_slot")) {
            cJSON *r = request("load");
            cJSON_AddStringToObject(r, "slot", "E");
            cJSON_AddBoolToObject(r, "inserted", true);
            confirmed("Connect the spool-holder tube and insert external filament toward the "
                      "extruder before continuing.",
                      r);
        } else {
            cJSON *r = request("load");
            cJSON_AddStringToObject(r, "slot", app.selected_slot);
            ui_send(r);
        }
    } else if (!strcmp(a, "home_filament_strip")) {
        ui_route("filament", false);
    } else if (!strcmp(a, "home_history")) {
        ui_route("history", false);
    } else if (!strcmp(a, "home_fans")) {
        ui_route("fans", false);
    } else if (!strcmp(a, "unload")) {
        simple("unload");
    } else if (!strcmp(a, "cancel_print"))
        confirmed("Stop this print? It cannot be resumed afterward.", request("cancel"));
    else if (!strncmp(a, "tune:", 5)) {
        const char *p = strchr(a + 5, ':');
        if (!p)
            return;
        char key[20];
        snprintf(key, sizeof(key), "%.*s", (int)(p - a - 5), a + 5);
        cJSON *r = request("tune");
        cJSON_AddStringToObject(r, "key", key);
        cJSON_AddNumberToObject(r, "value", strtod(p + 1, NULL));
        ui_send(r);
    } else if (!strncmp(a, "tune_input:", 11)) {
        const char *key = a + 11;
        if (!strcmp(key, "z_offset")) {
            double current = N("status.gcode_move.homing_origin.2");
            ui_number("Z offset", "tune", key, current, current - .1, current + .1);
        } else {
            char max_path[80];
            snprintf(max_path, sizeof(max_path), "tune_limits.%s.maximum", key);
            ui_number(!strcmp(key, "speed") ? "Print speed" : "Flow ratio", "tune", key,
                      jnum(app.data,
                           !strcmp(key, "speed") ? "status.gcode_move.speed_factor"
                                                 : "status.gcode_move.extrude_factor",
                           1) *
                          100,
                      1, jnum(app.data, max_path, 200));
        }
    } else if (!strncmp(a, "calibrate:", 10)) {
        if (!strcmp(a + 10, "save")) {
            ui_route("save", false);
            return;
        }
        cJSON *r = request("calibrate");
        cJSON_AddStringToObject(r, "kind", a + 10);
        confirmed("Clear the bed and stay beside the printer. Start calibration?", r);
    } else if (!strcmp(a, "save_config"))
        confirmed("Save pending calibration changes and restart Klipper?", request("save_config"));
    else if (!strncmp(a, "restart:", 8)) {
        bool firmware = !strcmp(a + 8, "firmware");
        confirmed(firmware
                      ? "Klipper and the connected controllers will restart. Any current print "
                        "will fail and cannot be resumed."
                      : "Any current print will fail and cannot be resumed.",
                  request(firmware ? "firmware_restart" : "restart"));
    } else if (!strcmp(a, "open_files") || !strncmp(a, "files:", 6) ||
               !strcmp(a, "refresh_files")) {
        ui_route("files", false);
    } else if (!strncmp(a, "file_open:", 10)) {
        cJSON *request = cJSON_Parse(a + 10);
        const char *selected = jstr(request, "path", "");
        snprintf(app.file, sizeof(app.file), "%s", selected);
        app.file_waiting = true;
        cJSON_DeleteItemFromObject(app.data, "file");
        cJSON_AddStringToObject(app.data, "file", selected);
        cJSON_DeleteItemFromObject(app.data, "metadata");
        cJSON_AddObjectToObject(app.data, "metadata");
        cJSON_DeleteItemFromObject(J("platform"), "file_thumbnail");
        cJSON_DeleteItemFromObject(app.data, "file_read");
        cJSON *read = cJSON_AddObjectToObject(app.data, "file_read");
        cJSON_AddBoolToObject(read, "busy", true);
        cJSON_Delete(request);
        ui_command(a + 10);
        ui_route("file", false);
    } else if (!strncmp(a, "file_delete:", 12)) {
        cJSON *r = cJSON_Parse(a + 12);
        if (r) {
            if (!strcmp(jstr(r, "action", ""), "file"))
                cJSON_SetValuestring(cJSON_GetObjectItemCaseSensitive(r, "action"), "delete_file");
            char message[1200];
            snprintf(message, sizeof(message), "Delete %s? This cannot be undone.",
                     jstr(r, "path", ""));
            confirmed(message, r);
        }
    } else if (!strcmp(a, "calibrate_before")) {
        calibrate_before = !calibrate_before;
        app.dirty = true;
    } else if (!strcmp(a, "review_mapping")) {
        if (strcmp(app.mapping_file, S("file"))) {
            cJSON_Delete(app.mapping);
            app.mapping = cJSON_Duplicate(J("metadata.suggested_mapping"), true);
            if (!app.mapping)
                app.mapping = cJSON_CreateObject();
            snprintf(app.mapping_file, sizeof(app.mapping_file), "%s", S("file"));
        }
        ui_route("mapping", false);
    } else if (!strncmp(a, "mapping_pick:", 13)) {
        snprintf(app.mapping_tool, sizeof(app.mapping_tool), "%s", a + 13);
        ui_route("mapping_pick", false);
    } else if (!strncmp(a, "mapping_set:", 12)) {
        if (!known_slot(slot_record(a + 12)) ||
            !compatible_material(mapping_filament(), slot_record(a + 12)))
            return;
        cJSON_DeleteItemFromObject(app.mapping, app.mapping_tool);
        cJSON_AddStringToObject(app.mapping, app.mapping_tool, a + 12);
        ui_route("mapping", true);
    } else if (!strcmp(a, "motor_toggle")) {
        bool enabled = motors_locked();
        cJSON *r = request("motors");
        cJSON_AddBoolToObject(r, "enable", !enabled);
        if (enabled)
            confirmed("The bed may move under its own weight.", r);
        else
            ui_send(r);
    } else if (!strcmp(a, "start_print")) {
        if (!mapping_ready())
            return;
        cJSON *r = request("start");
        cJSON_AddStringToObject(r, "path", app.file);
        cJSON_AddItemToObject(r, "mapping", cJSON_Duplicate(app.mapping, true));
        cJSON_AddBoolToObject(r, "calibrate", calibrate_before);
        ui_send(r);
    } else if (!strcmp(a, "scan_wifi")) {
        simple("wifi_scan");
        ui_route("networks", false);
    } else if (!strncmp(a, "wifi_select:", 12)) {
        cJSON *r = cJSON_Parse(a + 12);
        if (!r)
            return;
        snprintf(app.keyboard_title, sizeof(app.keyboard_title), "%s", jstr(r, "ssid", ""));
        if (jbool(r, "secured", false)) {
            char ssid[100];
            snprintf(ssid, sizeof(ssid), "%s", app.keyboard_title);
            ui_keyboard(ssid, "wifi", "");
        } else {
            cJSON *request_value = request("wifi_join");
            cJSON_AddStringToObject(request_value, "ssid", jstr(r, "ssid", ""));
            cJSON_AddStringToObject(request_value, "password", "");
            confirmed("Join this open Wi-Fi network?", request_value);
        }
        cJSON_Delete(r);
    } else if (!strncmp(a, "key:", 4)) {
        size_t limit = !strcmp(app.keyboard_kind, "wifi") ? 63 : sizeof(app.keyboard_value) - 2;
        if (strlen(app.keyboard_value) + strlen(a + 4) <= limit)
            strcat(app.keyboard_value, a + 4);
        lv_textarea_set_text(keyboard_input, app.keyboard_value);
        lv_textarea_set_cursor_pos(keyboard_input, LV_TEXTAREA_CURSOR_LAST);
        if (!keyboard_symbols && keyboard_shift && !keyboard_caps && isalpha((unsigned char)a[4])) {
            keyboard_shift = false;
            keyboard_draw();
        }
    } else if (!strcmp(a, "key_back")) {
        size_t n = strlen(app.keyboard_value);
        if (n)
            app.keyboard_value[n - 1] = 0;
        lv_textarea_set_text(keyboard_input, app.keyboard_value);
    } else if (!strcmp(a, "key_symbols")) {
        keyboard_symbols = !keyboard_symbols;
        keyboard_draw();
    } else if (!strcmp(a, "key_shift")) {
        uint64_t now = platform_millis();
        if (keyboard_caps)
            keyboard_caps = keyboard_shift = false;
        else if (keyboard_shift && now - keyboard_shift_at <= 400)
            keyboard_caps = true;
        else
            keyboard_shift = !keyboard_shift;
        keyboard_shift_at = now;
        keyboard_draw();
    } else if (!strcmp(a, "key_eye")) {
        app.password_visible = !app.password_visible;
        keyboard_draw();
    } else if (!strcmp(a, "key_done")) {
        close_modal();
        if (!strcmp(app.keyboard_kind, "wifi")) {
            app.wifi_joining = true;
            snprintf(app.wifi_ssid, sizeof(app.wifi_ssid), "%s", app.keyboard_title);
            cJSON *r = request("wifi_join");
            cJSON_AddStringToObject(r, "ssid", app.keyboard_title);
            cJSON_AddStringToObject(r, "password", app.keyboard_value);
            ui_send(r);
            ui_route("wifi", false);
        } else if (!strcmp(app.keyboard_kind, "color")) {
            const char *hex = app.keyboard_value;
            if (*hex == '#')
                hex++;
            char *end = NULL;
            if (strlen(hex) != 6) {
                ui_notice("Invalid colour", "Use six hexadecimal digits.");
                return;
            }
            strtoul(hex, &end, 16);
            if (*end) {
                ui_notice("Invalid colour", "Use six hexadecimal digits.");
                return;
            }
            cJSON_DeleteItemFromObject(app.draft, "color");
            cJSON_AddStringToObject(app.draft, "color", hex);
            app.edit_dirty = true;
            ui_route("edit", false);
        }
    } else if (!strcmp(a, "toggle_timelapse") || !strcmp(a, "timelapse_on") ||
               !strcmp(a, "timelapse_off")) {
        cJSON *r = request("timelapse");
        cJSON_AddBoolToObject(r, "enabled",
                              !strcmp(a, "toggle_timelapse") ? !B("platform.timelapse.enabled")
                                                             : !strcmp(a, "timelapse_on"));
        ui_send(r);
    } else if (!strcmp(a, "recordings")) {
        simple("recordings");
        ui_route("recordings", false);
    } else if (!strcmp(a, "export_logs")) {
        cJSON *r = request("export");
        cJSON_AddStringToObject(r, "kind", "logs");
        ui_send(r);
    } else if (!strcmp(a, "usb_format"))
        confirmed("Erase every file on the connected USB drive?", request("usb_format"));
    else if (!strncmp(a, "unit:", 5)) {
        snprintf(app.cfs_unit, sizeof(app.cfs_unit), "%s", a + 5);
        cJSON *record;
        cJSON_ArrayForEach(record, J("slots")) {
            const char *slot = jstr(record, "slot", "");
            size_t n = strlen(slot);
            char group[16];
            if (!n || !strcmp(slot, "E"))
                continue;
            snprintf(group, sizeof(group), "%.*s", n > 1 ? (int)n - 1 : 1, n > 1 ? slot : "1");
            if (!strcmp(group, app.cfs_unit)) {
                snprintf(app.selected_slot, sizeof(app.selected_slot), "%s", slot);
                break;
            }
        }
        app.dirty = true;
    } else if (!strncmp(a, "zoom:", 5)) {
        if (!strcmp(a + 5, "fit")) {
            app.zoom = 1;
            app.plate_x = app.plate_y = 0;
        } else if (!strcmp(a + 5, "in"))
            app.zoom = fmin(8, app.zoom * 2);
        else
            app.zoom = fmax(1, app.zoom / 2);
        app.dirty = true;
    } else if (!strcmp(a, "exclude")) {
        if (!cJSON_GetArraySize(app.object_selection))
            return;
        cJSON *r = request("exclude");
        cJSON_AddItemToObject(r, "names", cJSON_Duplicate(app.object_selection, true));
        confirmed("Skip the selected objects for the rest of this print?", r);
    } else if (!strncmp(a, "filament_notice:", 16)) {
        cJSON *payload = cJSON_Parse(a + 16);
        const char *slot = jstr(payload, "slot", "");
        cJSON *r = request("ack");
        cJSON_AddNumberToObject(r, "id", jnum(payload, "id", 0));
        ui_send(r);
        close_modal();
        if (slot_record(slot)) {
            snprintf(app.notice_slot, sizeof(app.notice_slot), "%s", slot);
            ui_route("filament", false);
            app.dirty = true;
        }
        cJSON_Delete(payload);
    } else if (!strncmp(a, "notice_ack:", 11)) {
        cJSON *r = request("ack");
        cJSON_AddNumberToObject(r, "id", strtod(a + 11, NULL));
        ui_send(r);
        close_modal();

    } else if (!strcmp(a, "error_ack")) {
        snprintf(app.last_fault, sizeof(app.last_fault), "%s", S("error"));
        close_modal();
    } else if (!strncmp(a, "plr:", 4)) {
        cJSON *r = request("plr");
        cJSON_AddBoolToObject(r, "resume", !strcmp(a + 4, "resume"));
        confirmed("Only resume if the part is attached and the bed is clear.", r);
    }
}
/* Long fault text remains available in place, without a duplicate Details page. */

void views_overlay(void) {
    if (ui_connection_mode() != CONNECTION_READY)
        return;
    const char *error = S("error");
    if (!*error && app.connected && !strcmp(S("status.webhooks.state"), "ready")) {
        app.last_fault[0] = 0;
        if (app.printer_fault_open) {
            close_modal();
            app.dirty = true;
        }
    }
    cJSON *notices = J("notices");
    cJSON *notice = cJSON_GetArrayItem(notices, 0);
    if (notice) {
        uint64_t id = (uint64_t)jnum(notice, "id", 0);
        if (id > app.last_notice) {
            app.last_notice = id;
            const char *title = jstr(notice, "title", "Notice"),
                       *detail = jstr(notice, "detail", "");
            if (jbool(notice, "timed",
                      false)) { /* Timed notices are non-modal and do not steal touch. */
                lv_obj_t *toast = box(app.screen, 210, 408, 540, 60, false);
                text(toast, title, 20, 18, 500, &font_ui_18, false);
                lv_obj_del_delayed(toast, 3000);
                cJSON *ack = request("ack");
                cJSON_AddNumberToObject(ack, "id", id);
                ui_send(ack);
            } else {
                modal_frame(title, 120, 60, 560, 360, true);
                modal_message(detail);
                char action[180];
                const char *slot = jstr(notice, "filament_slot", "");
                if (*slot) {
                    app.filament_notice_id = id;
                    cJSON *payload = cJSON_CreateObject();
                    cJSON_AddNumberToObject(payload, "id", id);
                    cJSON_AddStringToObject(payload, "slot", slot);
                    char *encoded = encode(payload);
                    snprintf(action, sizeof(action), "filament_notice:%s", encoded);
                    free(encoded);
                } else
                    snprintf(action, sizeof(action), "notice_ack:%llu", (unsigned long long)id);
                button(app.body, "OK", 160, 282, 240, 60, action, true);
                return;
            }
        }
    }
    if (*error && strcmp(error, app.last_fault) && app.connected) {
        snprintf(app.last_fault, sizeof(app.last_fault), "%s", error);
        modal_frame("Printer stopped", 120, 60, 560, 360, true);
        app.printer_fault_open = true;
        modal_message(error);
        button(app.body, "OK", 160, 282, 240, 60, "error_ack", true);
        return;
    }
    if (!app.overlay && !cJSON_IsObject(J("operation")) && B("status.hark_plr.available")) {
        modal_frame("Interrupted print", 120, 60, 560, 360, true);
        modal_message(
            "An interrupted print can be recovered. Check the bed and part before continuing.");
        button(app.body, "Discard", 24, 282, 230, 56, "plr:discard", false);
        button(app.body, "Resume", 306, 282, 230, 56, "plr:resume", true);
    }
}
