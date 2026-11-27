#define _POSIX_C_SOURCE 200809L
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
static char preview_profile[128];
static cJSON *preview_mesh;
static lv_img_dsc_t *front_text;
static int profile_scroll;
static void remember_profile_scroll(lv_event_t *event) {
    profile_scroll = lv_obj_get_scroll_y(lv_event_get_target(event));
}
/* Holding a name reveals it without turning the release into a selection. */
static void name_event(lv_event_t *event) {
    lv_obj_t *target = lv_event_get_target(event);
    lv_obj_t *label = lv_event_get_user_data(event);
    lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_LONG_PRESSED)
        lv_label_set_long_mode(label, LV_LABEL_LONG_SCROLL_CIRCULAR);
    else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST)
        lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
    else if (code == LV_EVENT_SHORT_CLICKED) {
        const char *action = lv_obj_get_user_data(target);
        if (action)
            ui_action(action);
    } else if (code == LV_EVENT_DELETE)
        free(lv_obj_get_user_data(target));
}
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
    return (lv_point_t){(x * cos(yaw) + y * sin(yaw)) * w * .62,
                        (x * sin(yaw) - y * cos(yaw)) * sin(pitch) * h * .9 -
                            z * cos(pitch) * h * .24};
}
static void draw_axes(lv_draw_ctx_t *ctx, const lv_area_t *area) {
    const lv_point_t origin = {(area->x1 + area->x2) / 2, (area->y1 + area->y2) / 2};
    const unsigned colors[] = {0xe28877, 0x66bcaa, 0x78adf0};
    for (int axis = 0; axis < 3; axis++) {
        lv_point_t v = project_vector(axis == 0, axis == 1, axis == 2, 100, 100);
        double length = hypot(v.x, v.y);
        if (length < 1)
            continue;
        double dx = v.x / length, dy = v.y / length;
        double arm = fmin(24, (fmin(area->x2 - area->x1 + 1, area->y2 - area->y1 + 1) - 40) / 2.);
        double spacing = 6;
        lv_point_t end = {origin.x + dx * arm, origin.y + dy * arm};
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
        lv_area_t bounds = {end.x + dx * spacing - 9, end.y + dy * spacing - 8,
                            end.x + dx * spacing + 9, end.y + dy * spacing + 10};
        char name[2] = {"XYZ"[axis], 0};
        lv_draw_label(ctx, &label, &bounds, name, NULL);
    }
}
/* Position the readable Front label parallel to the physical minimum-Y edge. */
static void position_front(lv_obj_t *plot, const cJSON *data) {
    if (!lv_obj_get_child_cnt(plot))
        return;
    if (!data)
        data = J("status.bed_mesh");
    cJSON *matrix = jget(data, "probed_matrix");
    if (!cJSON_GetArraySize(matrix))
        matrix = jget(data, "mesh_matrix");
    double low, high;
    int rows, cols;
    if (!matrix_limits(matrix, &low, &high, &rows, &cols))
        return;
    double width = jnum(data, "mesh_max.0", 1) - jnum(data, "mesh_min.0", 0);
    double depth = jnum(data, "mesh_max.1", 1) - jnum(data, "mesh_min.1", 0);
    double scale = fmax(width, depth);
    if (!isfinite(scale) || width <= 0 || depth <= 0)
        width = depth = scale = 1;
    double w = lv_obj_get_width(plot), h = lv_obj_get_height(plot);
    double extent = (fabs(sin(yaw)) + fabs(cos(yaw))) * sin(pitch) * .9 + fabs(cos(pitch)) * .24;
    double fitted_h = h * fmin(1, .82 / extent);
    double span = high > low ? high - low : 1;
    cJSON *row = cJSON_GetArrayItem(matrix, 0);
    double z0 =
        high == low ? 0 : (cJSON_GetArrayItem(row, 0)->valuedouble - (low / 2 + high / 2)) / span;
    double z1 =
        high == low
            ? 0
            : (cJSON_GetArrayItem(row, cols - 1)->valuedouble - (low / 2 + high / 2)) / span;
    lv_point_t left = project_vector(-.5 * width / scale, -.5 * depth / scale, z0, w, fitted_h);
    lv_point_t right = project_vector(.5 * width / scale, -.5 * depth / scale, z1, w, fitted_h);
    double angle = atan2(right.y - left.y, right.x - left.x);
    /* Offset along the edge normal so the text sits just outside the surface. */
    double cx = w / 2 + (left.x + right.x) / 2. - sin(angle) * 16;
    double cy = h * .52 + (left.y + right.y) / 2. + cos(angle) * 16;
    lv_obj_t *label = lv_obj_get_child(plot, 0);
    lv_obj_set_pos(label, cx - 26, cy - 10);
    lv_img_set_angle(label, (int)(angle * 1800 / 3.141592653589793));
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
            /* Keep the printer front visible and the bed upright. */
            yaw = fmin(1.1, fmax(-1.1, yaw + (point.x - last_touch.x) * .01));
            pitch = fmin(1.3, fmax(.2, pitch + (point.y - last_touch.y) * .01));
            position_front(plot, lv_event_get_user_data(e));
            lv_obj_invalidate(lv_obj_get_parent(plot));
        }
        last_touch = point;
        return;
    }
    if (code != LV_EVENT_DRAW_MAIN)
        return;
    const cJSON *data = lv_event_get_user_data(e);
    if (!data)
        data = J("status.bed_mesh");
    cJSON *values_matrix = jget(data, "probed_matrix");
    if (!cJSON_GetArraySize(values_matrix))
        values_matrix = jget(data, "mesh_matrix");
    double low, high;
    int rows, cols;
    if (!matrix_limits(values_matrix, &low, &high, &rows, &cols))
        return;
    lv_area_t area;
    lv_obj_get_coords(plot, &area);
    double w = lv_obj_get_width(plot), h = lv_obj_get_height(plot);
    double width = jnum(data, "mesh_max.0", 1) - jnum(data, "mesh_min.0", 0);
    double depth = jnum(data, "mesh_max.1", 1) - jnum(data, "mesh_min.1", 0);
    double scale = fmax(width, depth), span = high > low ? high - low : 1;
    if (!isfinite(scale) || !isfinite(width) || !isfinite(depth) || width <= 0 || depth <= 0)
        width = depth = scale = 1;
    /* Leave room for the edge marker even at the steepest allowed angle. */
    double projected_height =
        (fabs(sin(yaw)) + fabs(cos(yaw))) * sin(pitch) * .9 + fabs(cos(pitch)) * .24;
    double fitted_h = h * fmin(1, .82 / projected_height);
    lv_point_t points[MESH_LIMIT][MESH_LIMIT];
    double values[MESH_LIMIT][MESH_LIMIT];
    Cell cells[(MESH_LIMIT - 1) * (MESH_LIMIT - 1)];
    int count = 0;
    for (int r = 0; r < rows; r++) {
        for (int c = 0; c < cols; c++) {
            double value = cJSON_GetArrayItem(cJSON_GetArrayItem(values_matrix, r), c)->valuedouble;
            double x = (c / (double)(cols - 1) - .5) * width / scale;
            double y = (r / (double)(rows - 1) - .5) * depth / scale;
            double z = high == low ? 0 : (value - (low / 2 + high / 2)) / span;
            lv_point_t projected = project_vector(x, y, z, w, fitted_h);
            points[r][c].x = area.x1 + w / 2 + projected.x;
            points[r][c].y = area.y1 + h * .52 + projected.y;
            values[r][c] = high == low ? .5 : (value - low) / span;
            if (r < rows - 1 && c < cols - 1)
                cells[count++] = (Cell){r, c, x * sin(yaw) - y * cos(yaw)};
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
}
/* This mesh viewer owns its plot and orientation marker. */
static lv_obj_t *create_surface(lv_obj_t *parent, const cJSON *mesh, int x, int y, int w, int h) {
    lv_obj_t *plot = box(parent, x, y, w, h, false);
    lv_obj_set_style_bg_opa(plot, 0, 0);
    lv_obj_set_style_border_width(plot, 0, 0);
    lv_obj_add_flag(plot, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(plot, draw_mesh, LV_EVENT_ALL, (void *)mesh);
    if (!front_text) {
        lv_obj_t *label = text(plot, "Front", 0, 0, 52, &font_ui_15, false);
        lv_obj_set_height(label, 20);
        center(label);
        lv_obj_update_layout(label);
        front_text = lv_snapshot_take(label, LV_IMG_CF_TRUE_COLOR_ALPHA);
        lv_obj_del(label);
    }
    if (front_text) {
        lv_obj_t *marker = lv_img_create(plot);
        lv_img_set_src(marker, front_text);
        lv_img_set_pivot(marker, 26, 10);
        lv_obj_clear_flag(marker, LV_OBJ_FLAG_CLICKABLE);
    }
    lv_obj_update_layout(plot);
    position_front(plot, mesh);
    return plot;
}
static void key_event(lv_event_t *event) {
    if (lv_event_get_code(event) != LV_EVENT_DRAW_MAIN)
        return;
    lv_area_t area;
    lv_obj_get_coords(lv_event_get_target(event), &area);
    draw_axes(lv_event_get_draw_ctx(event), &area);
}
static void create_axes(lv_obj_t *parent, int x, int y, int w, int h) {
    lv_obj_t *key = box(parent, x, y, w, h, false);
    lv_obj_set_style_bg_opa(key, 0, 0);
    lv_obj_set_style_border_width(key, 0, 0);
    lv_obj_add_event_cb(key, key_event, LV_EVENT_DRAW_MAIN, NULL);
}
static void create_height_legend(lv_obj_t *parent, double low, double high, int x, int y,
                                 int width) {
    for (int i = 0; i < 32; i++) {
        int left = width * i / 32, right = width * (i + 1) / 32;
        lv_obj_t *part = box(parent, x + left, y, right - left, 7, false);
        lv_obj_set_style_bg_color(part, height_color(i / 31.), 0);
        lv_obj_set_style_border_width(part, 0, 0);
        lv_obj_set_style_radius(part, 0, 0);
    }
    char value[40];
    snprintf(value, sizeof(value), "%.3f mm", low);
    text(parent, value, x, y + 18, width / 2, &font_ui_12, true);
    snprintf(value, sizeof(value), "%.3f mm", high);
    lv_obj_t *v = text(parent, value, x + width / 2, y + 18, width / 2, &font_ui_12, true);
    lv_obj_set_style_text_align(v, LV_TEXT_ALIGN_RIGHT, 0);
}
static void reset_view(lv_obj_t *parent, int x, int y) {
    lv_obj_t *control = button(parent, "icon:maximize", x, y, 52, 52, "mesh_reset", false);
    lv_obj_set_pos(control, x, y);
    lv_obj_set_size(control, 52, 52);
    for (unsigned i = 0; i < lv_obj_get_child_cnt(control); i++)
        lv_obj_align(lv_obj_get_child(control, i), LV_ALIGN_CENTER, 0, 0);
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
    double low, high;
    int rows, cols;
    bool loaded_valid = limits(&low, &high, &rows, &cols);
    cJSON_Delete(preview_mesh);
    preview_mesh = NULL;
    cJSON *saved =
        *preview_profile
            ? cJSON_GetObjectItemCaseSensitive(J("status.bed_mesh.profiles"), preview_profile)
            : NULL;
    if (saved) {
        preview_mesh = cJSON_CreateObject();
        cJSON_AddItemToObject(preview_mesh, "probed_matrix",
                              cJSON_Duplicate(jget(saved, "points"), true));
        cJSON *params = jget(saved, "mesh_params");
        double xmin = jnum(params, "min_x", 0), ymin = jnum(params, "min_y", 0);
        double xmax = jnum(params, "max_x", 1), ymax = jnum(params, "max_y", 1);
        double minimum[] = {xmin, ymin}, maximum[] = {xmax, ymax};
        cJSON_AddItemToObject(preview_mesh, "mesh_min", cJSON_CreateDoubleArray(minimum, 2));
        cJSON_AddItemToObject(preview_mesh, "mesh_max", cJSON_CreateDoubleArray(maximum, 2));
    } else
        preview_profile[0] = 0;
    bool valid = preview_mesh
                     ? matrix_limits(jget(preview_mesh, "probed_matrix"), &low, &high, &rows, &cols)
                     : loaded_valid;
    if (!valid && !cJSON_GetArraySize(J("status.bed_mesh.profiles"))) {
        center(text(app.content, "No bed mesh available", 0, 162, 674, &font_ui_30, false));
        bool blocked = *ui_control_reason("calibrate:mesh");
        center(text(app.content,
                    blocked ? "Probe the bed when the printer is idle."
                            : "Probe the bed to create your first mesh.",
                    72, 218, 530, &font_ui_20, true));
        if (!blocked && J("settings.bed_mesh"))
            button_fixed(app.content, "Probe", 212, 296, 250, 64, "calibrate:mesh", false);
        return;
    }
    lv_obj_t *measure = button(app.content, "Probe", 480, 15, 194, 56,
                               J("settings.bed_mesh") ? "calibrate:mesh" : NULL, false);
    lv_obj_set_pos(measure, 480, 15);
    lv_obj_set_height(measure, 56);
    lv_obj_t *surface = card(0, 88, 402, 378);
    if (valid) {
        create_surface(surface, preview_mesh, 6, 8, 390, 270);
        create_axes(surface, 0, 294, 80, 80);
        center(text(surface, *preview_profile ? "Preview · drag to rotate" : "Drag to rotate", 6,
                    292, 390, &font_ui_15, true));
        create_height_legend(surface, low, high, 112, 322, 196);
        reset_view(surface, 332, 310);
    } else
        center(text(surface, "No mesh loaded", 12, 163, 378, &font_ui_24, true));
    lv_obj_t *info = card(414, 88, 260, 150);
    const char *profile_name =
        *S("status.bed_mesh.profile_name") ? S("status.bed_mesh.profile_name") : "Unsaved mesh";
    const char *shown = *preview_profile ? preview_profile : S("status.bed_mesh.profile_name");
    bool shown_loaded = !strcmp(shown, S("status.bed_mesh.profile_name"));
    lv_obj_t *name =
        text(info, valid ? (*preview_profile ? preview_profile : profile_name) : "No mesh loaded",
             16, 16, shown_loaded ? 180 : 128, &font_ui_20, false);
    lv_obj_set_height(name, lv_font_get_line_height(&font_ui_20));
    lv_label_set_long_mode(name, LV_LABEL_LONG_DOT);
    lv_obj_add_flag(name, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(name, name_event, LV_EVENT_ALL, name);
    int shown_index = 0;
    cJSON *shown_item;
    cJSON_ArrayForEach(shown_item, J("status.bed_mesh.profiles")) {
        if (shown_item->string && !strcmp(shown_item->string, shown)) {
            char action[48];
            snprintf(action, sizeof(action), "mesh:rename:%d", shown_index);
            lv_obj_t *edit =
                button_fixed(info, "", shown_loaded ? 200 : 148, 2, 52, 52, action, false);
            lv_obj_set_pos(edit, shown_loaded ? 200 : 148, 2);
            lv_obj_set_size(edit, 52, 52);
            lv_obj_set_style_bg_opa(edit, 0, 0);
            lv_obj_set_style_border_width(edit, 0, 0);
            icon(edit, "pencil", 14, 14, 24, rgb(0xf0f3f7));
            if (!shown_loaded) {
                snprintf(action, sizeof(action), "mesh:load:%d", shown_index);
                lv_obj_t *load = button_fixed(info, "", 200, 2, 52, 52, action, false);
                lv_obj_set_pos(load, 200, 2);
                lv_obj_set_size(load, 52, 52);
                lv_obj_set_style_bg_opa(load, 0, 0);
                lv_obj_set_style_border_width(load, 0, 0);
                icon(load, "circle-arrow-up", 12, 12, 28, rgb(0xf0f3f7));
            }
            break;
        }
        shown_index++;
    }
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
    lv_obj_t *list = lv_obj_create(profiles);
    lv_obj_remove_style_all(list);
    lv_obj_set_pos(list, 12, 8);
    lv_obj_set_size(list, 236, 200);
    lv_obj_set_scroll_dir(list, LV_DIR_VER);
    lv_obj_clear_flag(list, LV_OBJ_FLAG_SCROLL_MOMENTUM | LV_OBJ_FLAG_SCROLL_ELASTIC);
    lv_obj_set_style_pad_bottom(list, 0, 0);
    lv_obj_set_style_width(list, 3, LV_PART_SCROLLBAR);
    lv_obj_add_event_cb(list, remember_profile_scroll, LV_EVENT_SCROLL, NULL);
    cJSON *profile;
    int index = 0;
    cJSON_ArrayForEach(profile, J("status.bed_mesh.profiles")) {
        if (!profile->string)
            continue;
        bool active = !strcmp(profile->string, S("status.bed_mesh.profile_name"));
        bool selected = !strcmp(profile->string, shown);
        lv_obj_t *row = box(list, 0, index * 68, 232, 64, selected);
        lv_obj_clear_flag(row, LV_OBJ_FLAG_CLICKABLE);
        char action[48];
        snprintf(action, sizeof(action), "mesh:preview:%d", index);
        lv_obj_t *choose = box(row, 0, 0, 128, 64, false);
        lv_obj_add_flag(choose, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_user_data(choose, strdup(action));
        lv_obj_set_style_bg_opa(choose, 0, 0);
        lv_obj_set_style_border_width(choose, 0, 0);
        lv_obj_t *label = text(choose, profile->string, 8, 21, 112, &font_ui_17, false);
        lv_obj_clear_flag(label, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_height(label, lv_font_get_line_height(&font_ui_17));
        lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
        lv_obj_add_event_cb(choose, name_event, LV_EVENT_ALL, label);
        snprintf(action, sizeof(action), "mesh:load:%d", index);
        lv_obj_t *load = button_fixed(row, "", 128, 0, 52, 64, active ? NULL : action, false);
        lv_obj_set_style_bg_opa(load, 0, 0);
        lv_obj_set_style_border_width(load, 0, 0);
        icon(load, active ? "check" : "circle-arrow-up", 12, 18, 28,
             active ? rgb(0x66bcaa) : rgb(0xf0f3f7));
        snprintf(action, sizeof(action), "mesh:delete:%d", index);
        lv_obj_t *remove = button_fixed(row, "icon:trash-2", 180, 0, 52, 64, action, false);
        lv_obj_set_style_bg_opa(remove, 0, 0);
        lv_obj_set_style_border_width(remove, 0, 0);
        index++;
    }
    int offset = profile_scroll;
    lv_obj_update_layout(list);
    lv_obj_scroll_to_y(list, offset, LV_ANIM_OFF);
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
    if (!strncmp(a, "mesh:preview:", 13)) {
        char *end;
        long index = strtol(a + 13, &end, 10);
        cJSON *profile = !*end && index >= 0 && index <= 100000
                             ? cJSON_GetArrayItem(J("status.bed_mesh.profiles"), (int)index)
                             : NULL;
        if (profile && profile->string) {
            snprintf(preview_profile, sizeof(preview_profile), "%s", profile->string);
            app.dirty = true;
        }
        return true;
    }
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
            preview_profile[0] = 0;
            cJSON *r = cJSON_CreateObject();
            cJSON_AddStringToObject(r, "action", "mesh_profile");
            cJSON_AddStringToObject(r, "operation", "load");
            cJSON_AddStringToObject(r, "name", selected_profile);
            cJSON_AddBoolToObject(r, "confirm", true);
            ui_send(r);
        }
        return true;
    }
    if (!strcmp(a, "key_done") && !strcmp(app.keyboard_kind, "mesh_rename")) {
        close_modal();
        if (!*app.keyboard_value) {
            ui_notice("Profile name required", "Enter a name for this mesh profile.");
            return true;
        }
        confirm_profile("rename", selected_profile, app.keyboard_value);
        return true;
    }
    return false;
}
bool mesh_control_key(const char *a, char *key, size_t size) {
    if (!strncmp(a, "mesh:preview:", 13))
        return false;
    if (!strncmp(a, "mesh:", 5) ||
        (!strcmp(a, "key_done") && !strcmp(app.keyboard_kind, "mesh_rename"))) {
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
    } else if (!strcmp(op, "rename") && profile) {
        const char *target = jstr(request, "new_name", "");
        if (!*target || !strcmp(target, "default") || cJSON_HasObjectItem(profiles, target))
            return;
        cJSON *saved = cJSON_Duplicate(profile, true);
        cJSON_AddItemToObject(profiles, target, saved);
        if (!strcmp(op, "rename")) {
            if (!strcmp(jstr(mesh, "profile_name", ""), name))
                fixture_field(mesh, "profile_name", cJSON_CreateString(target));
            if (!strcmp(preview_profile, name))
                snprintf(preview_profile, sizeof(preview_profile), "%s", target);
            cJSON_DeleteItemFromObjectCaseSensitive(profiles, name);
        }
    }
    app.dirty = true;
}
