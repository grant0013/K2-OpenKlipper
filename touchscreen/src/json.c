#include "screen.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

static cJSON *resource_read(const char *path) {
    int descriptor = open(path, O_RDONLY | O_NONBLOCK);
    if (descriptor < 0)
        return NULL;
    struct stat info;
    if (fstat(descriptor, &info) || !S_ISREG(info.st_mode) || info.st_size <= 0 ||
        info.st_size > 2 * 1024 * 1024) {
        close(descriptor);
        return NULL;
    }
    FILE *file = fdopen(descriptor, "rb");
    if (!file) {
        close(descriptor);
        return NULL;
    }
    size_t size = (size_t)info.st_size;
    char *text = malloc(size + 1);
    if (!text) {
        fclose(file);
        return NULL;
    }
    size_t count = fread(text, 1, size, file);
    fclose(file);
    text[count] = 0;
    cJSON *value = count == size ? cJSON_Parse(text) : NULL;
    free(text);
    return value;
}

static void resource_set(cJSON *target, const char *key, cJSON *value) {
    if (!cJSON_ReplaceItemInObjectCaseSensitive(target, key, value))
        cJSON_AddItemToObject(target, key, value);
}

void ui_resources_load(cJSON *update) {
    const cJSON *descriptor = json_update_value(app.data, update, "resources");
    const char *path = jstr(descriptor, "path", "");
    if (!*path)
        return; /* Self-contained preview fixtures carry their own settings. */
    bool patch = jbool(update, "_screen_patch", false);
    if (patch && cJSON_GetArraySize(J("settings")) && json_equal(J("resources"), descriptor))
        return;
    cJSON *target = patch ? jget(update, "set") : update;
    if (!cJSON_IsObject(target))
        return;
    cJSON *data = resource_read(path);
    if (!cJSON_IsObject(data) || !cJSON_IsObject(jget(data, "settings"))) {
        /* Never present ready controls using an unconfirmed configuration. */
        resource_set(target, "settings", cJSON_CreateObject());
        cJSON_Delete(data);
        return;
    }
    const char *keys[] = {"settings", "zones", "timezone_presets", "tune_limits"};
    for (unsigned i = 0; i < sizeof(keys) / sizeof(keys[0]); i++) {
        cJSON *item = cJSON_DetachItemFromObjectCaseSensitive(data, keys[i]);
        if (item)
            resource_set(target, keys[i], item);
    }
    cJSON_Delete(data);
}

bool ui_catalogue_load(void) {
    const char *path = S("catalogue.path"), *revision = S("catalogue.revision");
    if (!*path || !*revision)
        return false;
    if (!strcmp(app.catalogue_revision, revision) && cJSON_IsArray(J("materials")))
        return false;
    cJSON *data = resource_read(path);
    bool valid = cJSON_IsArray(data);
    if (!valid) {
        cJSON_Delete(data);
        data = cJSON_CreateArray();
    }
    resource_set(app.data, "materials", data);
    if (valid)
        snprintf(app.catalogue_revision, sizeof(app.catalogue_revision), "%s", revision);
    return true;
}

/* Patches replace top-level sections or individual status objects. Null is a value, not a deletion;
 * explicit removals keep reconnects and cleared data unambiguous. */
const cJSON *json_update_value(const cJSON *previous, const cJSON *update, const char *path) {
    if (!jbool(update, "_screen_patch", false))
        return jget(update, path);
    size_t length = strcspn(path, ".");
    const cJSON *item;
    cJSON_ArrayForEach(
        item, jget(update, "remove")) if (cJSON_IsString(item) &&
                                          strlen(item->valuestring) == length &&
                                          !strncmp(path, item->valuestring, length)) return NULL;
    const cJSON *changes = jget(update, "set");
    cJSON_ArrayForEach(item, changes) if (item->string && strlen(item->string) == length &&
                                          !strncmp(path, item->string, length)) return jget(changes,
                                                                                            path);
    if (!strncmp(path, "status.", 7)) {
        const char *object_path = path + 7;
        size_t object_length = strcspn(object_path, ".");
        cJSON_ArrayForEach(
            item, jget(update, "status_remove")) if (cJSON_IsString(item) &&
                                                     strlen(item->valuestring) == object_length &&
                                                     !strncmp(object_path, item->valuestring,
                                                              object_length)) return NULL;
        cJSON_ArrayForEach(
            item,
            jget(update, "status_set")) if (item->string && strlen(item->string) == object_length &&
                                            !strncmp(object_path, item->string,
                                                     object_length)) return jget(jget(update,
                                                                                      "status_set"),
                                                                                 object_path);
    }
    return jget(previous, path);
}

cJSON *json_update(cJSON *previous, cJSON *update) {
    if (!jbool(update, "_screen_patch", false)) {
        cJSON_Delete(previous);
        return update;
    }
    cJSON *changes = jget(update, "set"), *item;
    while (changes && changes->child) {
        item = cJSON_DetachItemViaPointer(changes, changes->child);
        cJSON *existing = cJSON_GetObjectItemCaseSensitive(previous, item->string);
        if (existing)
            cJSON_ReplaceItemViaPointer(previous, existing, item);
        else {
            char *key = strdup(item->string);
            if (key) {
                cJSON_AddItemToObject(previous, key, item);
                free(key);
            } else
                cJSON_Delete(item);
        }
    }
    cJSON_ArrayForEach(item, jget(update, "remove")) if (cJSON_IsString(item))
        cJSON_DeleteItemFromObjectCaseSensitive(previous, item->valuestring);
    cJSON *objects = jget(update, "status_set"), *removed = jget(update, "status_remove");
    if (objects || removed) {
        cJSON *status = jget(previous, "status");
        if (!cJSON_IsObject(status)) {
            status = cJSON_CreateObject();
            resource_set(previous, "status", status);
        }
        while (objects && objects->child) {
            item = cJSON_DetachItemViaPointer(objects, objects->child);
            cJSON *existing = cJSON_GetObjectItemCaseSensitive(status, item->string);
            if (existing)
                cJSON_ReplaceItemViaPointer(status, existing, item);
            else {
                char *key = strdup(item->string);
                if (key) {
                    cJSON_AddItemToObject(status, key, item);
                    free(key);
                } else
                    cJSON_Delete(item);
            }
        }
        cJSON_ArrayForEach(item, removed) if (cJSON_IsString(item))
            cJSON_DeleteItemFromObjectCaseSensitive(status, item->valuestring);
    }
    cJSON_Delete(update);
    return previous;
}

/* Fast path for stable snapshot key order; no copied trees or JSON strings.
 * A producer may reorder object keys, so retain order-independent equality as
 * a fallback. Arrays remain ordered, and missing values differ from null. */
bool json_equal(const cJSON *left, const cJSON *right) {
    if (left == right)
        return true;
    if (!left || !right)
        return false;
    bool object = cJSON_IsObject(left) && cJSON_IsObject(right);
    bool array = cJSON_IsArray(left) && cJSON_IsArray(right);
    if (!object && !array)
        return cJSON_Compare(left, right, true);
    const cJSON *a = left->child, *b = right->child;
    for (; a && b; a = a->next, b = b->next) {
        if (object && strcmp(a->string, b->string))
            return cJSON_Compare(left, right, true);
        if (!json_equal(a, b))
            return false;
    }
    return !a && !b;
}

cJSON *jget(const cJSON *object, const char *path) {
    char key[160];
    const char *p = path;
    while (object && *p) {
        const char *end = strchr(p, '.');
        size_t n = end ? (size_t)(end - p) : strlen(p);
        if (n >= sizeof(key))
            return NULL;
        memcpy(key, p, n);
        key[n] = 0;
        object = cJSON_IsArray(object) ? cJSON_GetArrayItem(object, atoi(key))
                                       : cJSON_GetObjectItemCaseSensitive(object, key);
        p = end ? end + 1 : p + n;
    }
    return (cJSON *)object;
}
const char *jstr(const cJSON *o, const char *p, const char *fallback) {
    cJSON *v = jget(o, p);
    return cJSON_IsString(v) ? v->valuestring : fallback;
}
double jnum(const cJSON *o, const char *p, double fallback) {
    cJSON *v = jget(o, p);
    return cJSON_IsNumber(v) ? v->valuedouble : fallback;
}
bool jbool(const cJSON *o, const char *p, bool fallback) {
    cJSON *v = jget(o, p);
    if (cJSON_IsBool(v))
        return cJSON_IsTrue(v);
    return cJSON_IsNumber(v) ? v->valuedouble != 0 : fallback;
}
cJSON *J(const char *p) {
    return jget(app.data, p);
}
const char *S(const char *p) {
    return jstr(app.data, p, "");
}
double N(const char *p) {
    return jnum(app.data, p, 0);
}
bool B(const char *p) {
    return jbool(app.data, p, false);
}
char *request_json(const char *action, const char *key, const char *value) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "action", action);
    if (key)
        cJSON_AddStringToObject(o, key, value);
    char *s = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    return s;
}
