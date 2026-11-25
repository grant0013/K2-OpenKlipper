/* Snapshot inputs for each view. Local drafts use app.dirty; live coordinate
 * and temperature labels use views_refresh(). Unrelated telemetry must not
 * rebuild a page and reset its touch gestures or activity animation. */
#include "screen.h"
#include <string.h>

typedef struct {
    const char *routes;
    const char *inputs[12];
} ViewSpec;
static const ViewSpec views[] = {
    {"home", {"settings", "slots", "platform.print_preview_busy", "platform.starting_file"}},
    {"move",
     {"settings", "status.toolhead.homed_axes", "status.stepper_enable",
      "status.extruder.can_extrude"}},
    {"mesh", {"status.bed_mesh", "operations"}},
    {"heat", {"settings"}},
    {"fans",
     {"settings", "status.fan", "status.fan_generic auxiliary_fan",
      "status.fan_generic exhaust_fan"}},
    {"filament|edit|materials|colors|mapping|mapping_pick",
     {"slots", "materials", "metadata", "cfs_units", "status.k2_cfs",
      "status.filament_switch_sensor filament_sensor", "platform.file_thumbnail"}},
    {"files|file",
     {"files", "files_refresh", "metadata", "file", "file_read", "platform.file_thumbnail",
      "platform.file_thumbnails", "slots", "settings"}},
    {"settings|display|timers|refill|refill_sequence",
     {"prefs", "refill", "settings", "cfs_units", "status.k2_cfs.fitted"}},
    {"network|ethernet|wifi|networks", {"wifi", "platform.interfaces"}},
    {"timelapse|recordings|storage|usb",
     {"platform.timelapse", "platform.recordings", "platform.usb", "platform.usb_free",
      "platform.free"}},
    {"system|machine|software|language|help|restart|save",
     {"platform", "settings", "model", "status.toolhead.homed_axes",
      "status.k2_motor_bus.motors_ready"}},
    {"timezone", {"prefs.timezone", "platform.timezone", "timezone_presets"}},
    {"calibration", {"settings", "status.hark_calib"}},
    {"history|errors", {"events", "operations"}},
    {"tune",
     {"status.gcode_move.speed_factor", "status.gcode_move.extrude_factor",
      "status.gcode_move.homing_origin"}},
    {"objects", {"status.exclude_object", "metadata"}}};
static bool contains(const char *list, const char *route) {
    size_t length = strlen(route);
    for (const char *p = list; *p;) {
        const char *end = strchr(p, '|');
        size_t size = end ? (size_t)(end - p) : strlen(p);
        if (size == length && !strncmp(p, route, length))
            return true;
        if (!end)
            break;
        p = end + 1;
    }
    return false;
}
char *ui_view_signature(void) {
    cJSON *value = cJSON_CreateObject();
    const char *base[] = {
        "connected", "status.webhooks.state",         "status.print_stats.state",
        "error",     "status.webhooks.state_message", "platform.connection_error"};
    for (unsigned i = 0; i < sizeof(base) / sizeof(base[0]); i++)
        cJSON_AddItemToObject(value, base[i], cJSON_Duplicate(J(base[i]), true));
    bool found = false;
    for (unsigned i = 0; i < sizeof(views) / sizeof(views[0]); i++) {
        if (!contains(views[i].routes, app.route))
            continue;
        found = true;
        for (unsigned n = 0; n < 12 && views[i].inputs[n]; n++) {
            const char *key = views[i].inputs[n];
            cJSON_AddItemToObject(value, key, cJSON_Duplicate(J(key), true));
        }
        break;
    }
    /* A new, unregistered page remains correct; the registry is the efficient
     * contract, not a reason to silently stop refreshing an unfamiliar page. */
    if (!found)
        cJSON_AddItemToObject(value, "fallback", cJSON_Duplicate(app.data, true));
    char *signature = cJSON_PrintUnformatted(value);
    cJSON_Delete(value);
    return signature;
}
