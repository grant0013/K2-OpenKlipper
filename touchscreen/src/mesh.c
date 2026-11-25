/* Bed mesh inspection and profile controls.
 * Layout reference: HelixScreen's visualization / current mesh / profiles cards.
 * This is an independent LVGL 8 implementation, not copied HelixScreen code. */
#include "screen.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MESH_LIMIT 64
static double yaw = .6, pitch = .65;
static lv_point_t last_touch;
static char selected_profile[128];
static lv_color_t rgb(unsigned value) {
    return lv_color_hex(value);
}
static void center(lv_obj_t *label) {
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
}
static cJSON *matrix(void) {
    cJSON *value = J("status.bed_mesh.probed_matrix");
    return cJSON_GetArraySize(value) ? value : J("status.bed_mesh.mesh_matrix");
}
static bool matrix_limits(cJSON *value, double *low, double *high, int *rows, int *cols) {
    *rows = cJSON_GetArraySize(value);
    *cols = cJSON_GetArraySize(cJSON_GetArrayItem(value, 0));
    if (*rows < 2 || *rows > MESH_LIMIT || *cols < 2 || *cols > MESH_LIMIT)
        return false;
    *low = INFINITY;
    *high = -INFINITY;
    for (int r = 0; r < *rows; r++) {
        cJSON *row = cJSON_GetArrayItem(value, r);
        if (cJSON_GetArraySize(row) != *cols)
            return false;
        for (int c = 0; c < *cols; c++) {
            cJSON *v = cJSON_GetArrayItem(row, c);
            if (!cJSON_IsNumber(v) || !isfinite(v->valuedouble))
                return false;
            *low = fmin(*low, v->valuedouble);
            *high = fmax(*high, v->valuedouble);
        }
    }
    return isfinite(*high - *low);
}
static bool limits(double *low, double *high, int *rows, int *cols) {
    return matrix_limits(matrix(), low, high, rows, cols);
}
static lv_color_t height_color(double t) {
    t = fmin(1, fmax(0, t));
    return t < .5 ? lv_color_mix(rgb(0x66bcaa), rgb(0x559ddd), (uint8_t)(t * 510))
                  : lv_color_mix(rgb(0xe28877), rgb(0x66bcaa), (uint8_t)((t - .5) * 510));
}
typedef struct {
    int r, c;
    double depth;
} Cell;
static int cell_depth(const void *a, const void *b) {
    double delta = ((const Cell *)a)->depth - ((const Cell *)b)->depth;
    return delta < 0 ? -1 : delta > 0;
}
/* Rotation and height variation can make a projected quad concave or folded.
 * LVGL 8's convex-polygon rasterizer may loop forever for those shapes. Keep
 * ordinary quads seamless, but triangulate every non-strictly-convex cell. */
static int64_t turn(lv_point_t a, lv_point_t b, lv_point_t c) {
    return (int64_t)(b.x - a.x) * (c.y - a.y) - (int64_t)(b.y - a.y) * (c.x - a.x);
}
static void draw_cell(lv_draw_ctx_t *ctx, const lv_draw_rect_dsc_t *face,
                      const lv_point_t corners[4]) {
    int sign = 0;
    bool convex = true;
    for (int i = 0; i < 4; i++) {
        int64_t cross = turn(corners[i], corners[(i + 1) % 4], corners[(i + 2) % 4]);
        int current = cross > 0 ? 1 : cross < 0 ? -1 : 0;
        if (!current || (sign && sign != current))
            convex = false;
        sign = current;
    }
    if (convex) {
        lv_draw_polygon(ctx, face, corners, 4);
        return;
    }
    const lv_point_t triangles[2][3] = {{corners[0], corners[1], corners[2]},
                                        {corners[0], corners[2], corners[3]}};
    for (int i = 0; i < 2; i++)
        if (turn(triangles[i][0], triangles[i][1], triangles[i][2]))
            lv_draw_polygon(ctx, face, triangles[i], 3);
}
/* Both surface vertices and the orientation key use this camera basis. */
static lv_point_t project_vector(double x, double y, double z, double w, double h) {
    return (lv_point_t){(x * cos(yaw) - y * sin(yaw)) * w * .62,
                        (x * sin(yaw) + y * cos(yaw)) * sin(pitch) * h * .9 -
                            z * cos(pitch) * h * .24};
}
static void draw_axes(lv_draw_ctx_t *ctx, const lv_area_t *area) {
    const lv_point_t origin = {area->x1 + 48, area->y2 - 50};
    const unsigned colors[] = {0xe28877, 0x66bcaa, 0x78adf0};
    for (int axis = 0; axis < 3; axis++) {
        lv_point_t v = project_vector(axis == 0, axis == 1, axis == 2, 100, 100);
        double length = hypot(v.x, v.y);
        if (length < 1)
            continue;
        double dx = v.x / length, dy = v.y / length;
        lv_point_t end = {origin.x + dx * 28, origin.y + dy * 28};
        lv_point_t head1 = {end.x - dx * 6 + dy * 3, end.y - dy * 6 - dx * 3};
        lv_point_t head2 = {end.x - dx * 6 - dy * 3, end.y - dy * 6 + dx * 3};
        lv_draw_line_dsc_t line;
        lv_draw_line_dsc_init(&line);
        line.color = rgb(colors[axis]);
        line.width = 2;
        lv_draw_line(ctx, &line, &origin, &end);
        lv_draw_line(ctx, &line, &end, &head1);
        lv_draw_line(ctx, &line, &end, &head2);
        lv_draw_label_dsc_t label;
        lv_draw_label_dsc_init(&label);
        label.font = &font_ui_14;
        label.color = line.color;
        label.align = LV_TEXT_ALIGN_CENTER;
        lv_area_t bounds = {end.x + dx * 10 - 9, end.y + dy * 10 - 8, end.x + dx * 10 + 9,
                            end.y + dy * 10 + 10};
        char name[2] = {"XYZ"[axis], 0};
        lv_draw_label(ctx, &label, &bounds, name, NULL);
    }
}
static void draw_mesh(lv_event_t *e) {
    lv_obj_t *plot = lv_event_get_target(e);
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_PRESSED || code == LV_EVENT_PRESSING) {
        lv_indev_t *input = lv_indev_get_act();
        if (!input)
            return;
        lv_point_t point;
        lv_indev_get_point(input, &point);
        if (code == LV_EVENT_PRESSING) {
            yaw += (point.x - last_touch.x) * .01;
            pitch = fmin(1.3, fmax(.2, pitch + (point.y - last_touch.y) * .01));
            lv_obj_invalidate(plot);
        }
        last_touch = point;
        return;
    }
    if (code != LV_EVENT_DRAW_MAIN)
        return;
    double low, high;
    int rows, cols;
    if (!limits(&low, &high, &rows, &cols))
        return;
    lv_area_t area;
    lv_obj_get_coords(plot, &area);
    double w = lv_obj_get_width(plot), h = lv_obj_get_height(plot);
    double width = jnum(app.data, "status.bed_mesh.mesh_max.0", 1) -
                   jnum(app.data, "status.bed_mesh.mesh_min.0", 0);
    double depth = jnum(app.data, "status.bed_mesh.mesh_max.1", 1) -
                   jnum(app.data, "status.bed_mesh.mesh_min.1", 0);
    double scale = fmax(width, depth), span = high > low ? high - low : 1;
    if (!isfinite(scale) || !isfinite(width) || !isfinite(depth) || width <= 0 || depth <= 0)
        width = depth = scale = 1;
    lv_point_t points[MESH_LIMIT][MESH_LIMIT];
    double values[MESH_LIMIT][MESH_LIMIT];
    Cell cells[(MESH_LIMIT - 1) * (MESH_LIMIT - 1)];
    int count = 0;
    for (int r = 0; r < rows; r++) {
        for (int c = 0; c < cols; c++) {
            double value = cJSON_GetArrayItem(cJSON_GetArrayItem(matrix(), r), c)->valuedouble;
            double x = (c / (double)(cols - 1) - .5) * width / scale;
            double y = (r / (double)(rows - 1) - .5) * depth / scale;
            double z = high == low ? 0 : (value - (low / 2 + high / 2)) / span;
            lv_point_t projected = project_vector(x, y, z, w, h);
            points[r][c].x = area.x1 + w / 2 + projected.x;
            points[r][c].y = area.y1 + h * .52 + projected.y;
            values[r][c] = high == low ? .5 : (value - low) / span;
            if (r < rows - 1 && c < cols - 1)
                cells[count++] = (Cell){r, c, x * sin(yaw) + y * cos(yaw)};
        }
    }
    qsort(cells, count, sizeof(Cell), cell_depth);
    lv_draw_ctx_t *ctx = lv_event_get_draw_ctx(e);
    lv_draw_rect_dsc_t face;
    lv_draw_rect_dsc_init(&face);
    face.bg_opa = LV_OPA_COVER;
    lv_draw_line_dsc_t line;
    lv_draw_line_dsc_init(&line);
    line.color = rgb(0x0d252c);
    line.opa = 130;
    line.width = 1;
    for (int i = 0; i < count; i++) {
        int r = cells[i].r, c = cells[i].c;
        lv_point_t corners[] = {points[r][c], points[r][c + 1], points[r + 1][c + 1],
                                points[r + 1][c]};
        face.bg_color = height_color(
            (values[r][c] + values[r][c + 1] + values[r + 1][c + 1] + values[r + 1][c]) / 4);
        draw_cell(ctx, &face, corners);
        for (int n = 0; n < 4; n++)
            lv_draw_line(ctx, &line, &corners[n], &corners[(n + 1) % 4]);
    }
    draw_axes(ctx, &area);
}
static bool measuring(void) {
    cJSON *op;
    cJSON_ArrayForEach(op, J("operations")) if (!strcmp(jstr(op, "action", ""), "calibrate") &&
                                                !strcmp(jstr(op, "kind", ""), "mesh")) return true;
    return false;
}
static lv_obj_t *card(int x, int y, int w, int h) {
    lv_obj_t *o = box(app.content, x, y, w, h, false);
    lv_obj_set_style_bg_color(o, rgb(0x10171e), 0);
    lv_obj_set_style_border_color(o, rgb(0x293440), 0);
    return o;
}
static void stat(lv_obj_t *parent, const char *name, const char *value, int y) {
    text(parent, name, 16, y, 92, &font_ui_17, true);
    lv_obj_t *v = text(parent, value, 108, y, 134, &font_ui_17, false);
    lv_obj_set_style_text_align(v, LV_TEXT_ALIGN_RIGHT, 0);
}
void mesh_view(void) {
    if (measuring()) {
        lv_obj_t *spinner = lv_spinner_create(app.content, 1000, 60);
        lv_obj_set_size(spinner, 58, 58);
        lv_obj_set_pos(spinner, 308, 179);
        lv_obj_set_style_arc_width(spinner, 5, LV_PART_MAIN);
        lv_obj_set_style_arc_width(spinner, 5, LV_PART_INDICATOR);
        lv_obj_set_style_arc_color(spinner, app.accent, LV_PART_INDICATOR);
        center(text(app.content, "Meshing in progress…", 0, 265, 674, &font_ui_30, false));
        return;
    }
    lv_obj_t *measure = button(app.content, "Probe", 480, 15, 194, 56,
                               J("settings.bed_mesh") ? "calibrate:mesh" : NULL, false);
    lv_obj_set_pos(measure, 480, 15);
    lv_obj_set_height(measure, 56);
    double low, high;
    int rows, cols;
    bool valid = limits(&low, &high, &rows, &cols);
    lv_obj_t *surface = card(0, 88, 402, 378);
    if (valid) {
        lv_obj_t *plot = box(surface, 6, 8, 390, 300, false);
        lv_obj_set_style_bg_opa(plot, 0, 0);
        lv_obj_set_style_border_width(plot, 0, 0);
        lv_obj_add_flag(plot, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_event_cb(plot, draw_mesh, LV_EVENT_ALL, NULL);
        for (int i = 0; i < 32; i++) {
            lv_obj_t *bar = box(surface, 20 + i * 9, 322, 9, 7, false);
            lv_obj_set_style_bg_color(bar, height_color(i / 31.), 0);
            lv_obj_set_style_border_width(bar, 0, 0);
            lv_obj_set_style_radius(bar, 0, 0);
        }
        char value[48];
        snprintf(value, sizeof(value), "%.3f mm", low);
        text(surface, value, 20, 341, 140, &font_ui_14, true);
        snprintf(value, sizeof(value), "%.3f mm", high);
        lv_obj_t *max = text(surface, value, 166, 341, 142, &font_ui_14, true);
        lv_obj_set_style_text_align(max, LV_TEXT_ALIGN_RIGHT, 0);
        lv_obj_t *reset = button(surface, "icon:maximize", 332, 310, 52, 52, "mesh_reset", false);
        lv_obj_set_pos(reset, 332, 310);
        lv_obj_set_height(reset, 52);
    } else
        center(text(surface, "No mesh loaded", 12, 163, 378, &font_ui_24, true));
    lv_obj_t *info = card(414, 88, 260, 150);
    const char *profile_name =
        *S("status.bed_mesh.profile_name") ? S("status.bed_mesh.profile_name") : "Unsaved mesh";
    lv_obj_t *name =
        text(info, valid ? profile_name : "No mesh loaded", 16, 16, 228, &font_ui_20, false);
    lv_label_set_long_mode(name, LV_LABEL_LONG_DOT);
    if (valid) {
        char value[64];
        snprintf(value, sizeof(value), "%d × %d", rows, cols);
        stat(info, "Grid", value, 53);
        snprintf(value, sizeof(value), "%.3f mm", high - low);
        stat(info, "Z range", value, 84);
        snprintf(value, sizeof(value), "%.3f / %.3f", low, high);
        stat(info, "Min / max", value, 115);
    } else {
        text(info,
             cJSON_GetArraySize(J("status.bed_mesh.profiles"))
                 ? "Load a saved profile below or probe the bed."
                 : "Probe the bed to create your first mesh.",
             16, 67, 228, &font_ui_17, true);
    }
    lv_obj_t *profiles = card(414, 250, 260, 216);
    text(profiles, "Profiles", 16, 17, 170, &font_ui_20, false);
    lv_obj_t *save =
        button(profiles, "icon:save", 192, 4, 52, 52, valid ? "mesh:save" : NULL, false);
    lv_obj_set_pos(save, 192, 4);
    lv_obj_set_height(save, 52);
    lv_obj_set_style_bg_opa(save, 0, 0);
    lv_obj_set_style_border_width(save, 0, 0);
    lv_obj_t *list = lv_obj_create(profiles);
    lv_obj_remove_style_all(list);
    lv_obj_set_pos(list, 12, 64);
    lv_obj_set_size(list, 236, 144);
    lv_obj_set_scroll_dir(list, LV_DIR_VER);
    lv_obj_set_style_pad_bottom(list, 4, 0);
    lv_obj_set_style_width(list, 3, LV_PART_SCROLLBAR);
    cJSON *profile;
    int index = 0;
    cJSON_ArrayForEach(profile, J("status.bed_mesh.profiles")) {
        if (!profile->string)
            continue;
        bool active = !strcmp(profile->string, S("status.bed_mesh.profile_name"));
        lv_obj_t *row = box(list, 0, index * 72, 232, 64, active);
        lv_obj_clear_flag(row, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_t *label = text(row, profile->string, 8, 10, 112, &font_ui_17, false);
        lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
        double min, max;
        int nr, nc;
        if (matrix_limits(cJSON_GetObjectItemCaseSensitive(profile, "points"), &min, &max, &nr,
                          &nc)) {
            char range[48];
            snprintf(range, sizeof(range), "Z range %.3f mm", max - min);
            text(row, range, 8, 38, 112, &font_ui_13, true);
        }
        char action[48];
        snprintf(action, sizeof(action), "mesh:%s:%d", active ? "rename" : "load", index);
        lv_obj_t *use = button(row, "", 124, 0, 52, 64, action, false);
        lv_obj_set_style_bg_opa(use, 0, 0);
        lv_obj_set_style_border_width(use, 0, 0);
        icon(use, active ? "pencil" : "circle-arrow-up", active ? 14 : 12, active ? 20 : 18,
             active ? 24 : 28, rgb(0xf0f3f7));
        snprintf(action, sizeof(action), "mesh:delete:%d", index);
        lv_obj_t *remove = button(row, "icon:trash-2", 180, 0, 52, 64, action, false);
        lv_obj_set_style_bg_opa(remove, 0, 0);
        lv_obj_set_style_border_width(remove, 0, 0);
        index++;
    }
    if (!index)
        text(list, "No saved profiles", 4, 24, 228, &font_ui_17, true);
}
static void confirm_profile(const char *operation, const char *name, const char *new_name) {
    cJSON *r = cJSON_CreateObject();
    cJSON_AddStringToObject(r, "action", "mesh_profile");
    cJSON_AddStringToObject(r, "operation", operation);
    cJSON_AddStringToObject(r, "name", name);
    if (new_name)
        cJSON_AddStringToObject(r, "new_name", new_name);
    cJSON_AddBoolToObject(r, "confirm", true);
    char *command = cJSON_PrintUnformatted(r);
    cJSON_Delete(r);
    char message[300];
    if (!strcmp(operation, "delete"))
        snprintf(message, sizeof(message), "Remove '%s' from the saved profiles.", name);
    else if (!strcmp(operation, "rename"))
        snprintf(message, sizeof(message), "Rename '%s' to '%s'.", name, new_name ? new_name : "");
    else
        snprintf(message, sizeof(message), "Save the current mesh as '%s'.", name);
    ui_confirm(message, command);
    free(command);
}
bool mesh_action(const char *a) {
    if (!strcmp(a, "mesh_reset")) {
        yaw = .6;
        pitch = .65;
        app.dirty = true;
        return true;
    }
    const char *operation = NULL, *index_text = NULL;
    if (!strncmp(a, "mesh:load:", 10)) {
        operation = "load";
        index_text = a + 10;
    } else if (!strncmp(a, "mesh:rename:", 12)) {
        operation = "rename";
        index_text = a + 12;
    } else if (!strncmp(a, "mesh:delete:", 12)) {
        operation = "delete";
        index_text = a + 12;
    }
    if (operation) {
        char *end;
        long index = strtol(index_text, &end, 10);
        if (*end || index < 0 || index > 100000)
            return true;
        cJSON *profile = cJSON_GetArrayItem(J("status.bed_mesh.profiles"), (int)index);
        if (!profile || !profile->string)
            return true;
        snprintf(selected_profile, sizeof(selected_profile), "%s", profile->string);
        if (!strcmp(operation, "rename"))
            ui_keyboard("Rename mesh profile", "mesh_rename", selected_profile);
        else if (!strcmp(operation, "delete"))
            confirm_profile("delete", selected_profile, NULL);
        else {
            cJSON *r = cJSON_CreateObject();
            cJSON_AddStringToObject(r, "action", "mesh_profile");
            cJSON_AddStringToObject(r, "operation", "load");
            cJSON_AddStringToObject(r, "name", selected_profile);
            cJSON_AddBoolToObject(r, "confirm", true);
            ui_send(r);
        }
        return true;
    }
    if (!strcmp(a, "mesh:save")) {
        ui_keyboard("Save mesh profile", "mesh_save", "");
        return true;
    }
    if (!strcmp(a, "key_done") && !strncmp(app.keyboard_kind, "mesh_", 5)) {
        close_modal();
        if (!*app.keyboard_value) {
            ui_notice("Profile name required", "Enter a name for this mesh profile.");
            return true;
        }
        bool rename = !strcmp(app.keyboard_kind, "mesh_rename");
        confirm_profile(rename ? "rename" : "save", rename ? selected_profile : app.keyboard_value,
                        rename ? app.keyboard_value : NULL);
        return true;
    }
    return false;
}
bool mesh_control_key(const char *a, char *key, size_t size) {
    if (!strncmp(a, "mesh:", 5) ||
        (!strcmp(a, "key_done") && !strncmp(app.keyboard_kind, "mesh_", 5))) {
        snprintf(key, size, "%s", "mesh_profile");
        return true;
    }
    return false;
}

/* Desktop-only metadata changes make profile controls reviewable without IPC.
 * Real printer state is always supplied by the backend subscription. */
static void fixture_field(cJSON *object, const char *key, cJSON *value) {
    cJSON_DeleteItemFromObjectCaseSensitive(object, key);
    cJSON_AddItemToObject(object, key, value);
}
void mesh_fixture(const cJSON *request) {
    if (!app.fixture || !jbool(request, "confirm", false))
        return;
    cJSON *mesh = J("status.bed_mesh"), *profiles = J("status.bed_mesh.profiles");
    if (!cJSON_IsObject(mesh) || !cJSON_IsObject(profiles))
        return;
    const char *op = jstr(request, "operation", ""), *name = jstr(request, "name", "");
    cJSON *profile = cJSON_GetObjectItemCaseSensitive(profiles, name);
    if (!strcmp(op, "delete")) {
        cJSON_DeleteItemFromObjectCaseSensitive(profiles, name);
    } else if (!strcmp(op, "load") && profile) {
        cJSON *points = cJSON_GetObjectItemCaseSensitive(profile, "points");
        fixture_field(mesh, "probed_matrix", cJSON_Duplicate(points, true));
        fixture_field(mesh, "mesh_matrix", cJSON_Duplicate(points, true));
        fixture_field(mesh, "profile_name", cJSON_CreateString(name));
    } else if (!strcmp(op, "save") || (!strcmp(op, "rename") && profile)) {
        const char *target = !strcmp(op, "rename") ? jstr(request, "new_name", "") : name;
        if (!*target || !strcmp(target, "default") || cJSON_HasObjectItem(profiles, target))
            return;
        cJSON *saved = profile ? cJSON_Duplicate(profile, true) : cJSON_CreateObject();
        fixture_field(saved, "points", cJSON_Duplicate(matrix(), true));
        cJSON_AddItemToObject(profiles, target, saved);
        if (!strcmp(op, "rename")) {
            fixture_field(mesh, "profile_name", cJSON_CreateString(target));
            cJSON_DeleteItemFromObjectCaseSensitive(profiles, name);
        }
    }
    app.dirty = true;
}
