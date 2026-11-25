#include "screen.h"
#include <stdlib.h>
#include <string.h>
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
