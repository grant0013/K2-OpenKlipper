/* The illustration is static. Only four compact cached masks are tinted from
 * existing slot status; no image generation or printer queries happen here. */
#include "screen.h"
#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include "../assets/generated/printer_spool_layout.h"

static lv_obj_t *masks[4];
static char display_unit[16];

void printer_spools_reset(void) {
    memset(masks, 0, sizeof(masks));
}

static cJSON *unit_slot(const char *unit, int index) {
    cJSON *record;
    cJSON_ArrayForEach(record, J("slots")) {
        const char *slot = jstr(record, "slot", "");
        size_t length = strlen(slot);
        if (!length || slot[length - 1] != 'A' + index)
            continue;
        if ((length == 1 && !strcmp(unit, "1")) ||
            (length > 1 && strlen(unit) == length - 1 && !strncmp(slot, unit, length - 1)))
            return record;
    }
    return NULL;
}
lv_color_t printer_spool_colour(cJSON *slot) {
    if (!B("status.k2_cfs.online") || !jbool(slot, "loaded", false))
        return lv_color_hex(0x27313e);
    const char *hex = jstr(slot, "color", "");
    if (*hex == '#')
        hex++;
    if (strlen(hex) != 6)
        return lv_color_hex(0x76818d);
    for (int i = 0; i < 6; i++)
        if (!isxdigit((unsigned char)hex[i]))
            return lv_color_hex(0x76818d);
    unsigned value;
    if (sscanf(hex, "%x", &value) != 1)
        return lv_color_hex(0x76818d);
    return lv_color_hex(value);
}
void printer_spools(lv_obj_t *base, const char *unit) {
    snprintf(display_unit, sizeof(display_unit), "%s", unit);
    int zoom = lv_img_get_zoom(base);
    for (int i = 0; i < 4; i++) {
        char file[1400];
        snprintf(file, sizeof(file), "A:%s/%s", app.assets, spool_layers[i].file);
        lv_obj_t *mask = lv_img_create(lv_obj_get_parent(base));
        masks[i] = mask;
        lv_img_set_src(mask, file);
        lv_img_set_pivot(mask, 0, 0);
        lv_img_set_zoom(mask, zoom);
        lv_obj_set_pos(mask, lv_obj_get_x(base) + spool_layers[i].x * zoom / 256,
                       lv_obj_get_y(base) + spool_layers[i].y * zoom / 256);
        lv_obj_set_style_img_recolor(mask, printer_spool_colour(unit_slot(unit, i)), 0);
        lv_obj_set_style_img_recolor_opa(mask, LV_OPA_COVER, 0);
        lv_obj_clear_flag(mask, LV_OBJ_FLAG_CLICKABLE);
    }
}

void printer_spools_refresh(void) {
    for (int i = 0; i < 4; i++) {
        if (!masks[i])
            continue;
        lv_color_t colour = printer_spool_colour(unit_slot(display_unit, i));
        if (lv_obj_get_style_img_recolor(masks[i], 0).full != colour.full)
            lv_obj_set_style_img_recolor(masks[i], colour, 0);
    }
}
