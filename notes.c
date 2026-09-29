#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <winhttp.h>

#define MAX_NOTES 32
#define MAX_LEN 128
#define NOTES_FILE "notes.txt"
#define API_HOST L"127.0.0.1"
#define API_PORT 11434
#define API_PATH L"/api/chat"
#define API_MODEL "llama3.2"
#define RESPONSE_MAX 65536

static const char MEAL_PROMPT[] =
    "Decide if the text is a meal someone would prepare and eat. "
    "If it is not a meal, reply with exactly NOT_A_MEAL and no other words. "
    "If it is a meal, reply with only the ingredients, one per line, with a short amount. "
    "No numbering and no extra text. Text: ";

static int json_escape(const char *src, char *dst, size_t dst_size)
{
    size_t used = 0;

    for (size_t i = 0; src[i] != '\0'; i++) {
        unsigned char c = (unsigned char)src[i];
        char extra[8];
        const char *add = extra;
        size_t add_len;

        if (c == '"' || c == '\\') {
            extra[0] = '\\';
            extra[1] = (char)c;
            extra[2] = '\0';
        } else if (c == '\n') {
            add = "\\n";
        } else if (c == '\r') {
            add = "\\r";
        } else if (c == '\t') {
            add = "\\t";
        } else if (c < 0x20) {
            snprintf(extra, sizeof extra, "\\u%04x", c);
        } else {
            extra[0] = (char)c;
            extra[1] = '\0';
        }

        add_len = strlen(add);
        if (used + add_len + 1 > dst_size) {
            return 0;
        }
        memcpy(dst + used, add, add_len);
        used += add_len;
    }

    dst[used] = '\0';
    return 1;
}

static int append_utf8(char *dst, size_t dst_size, size_t *used, unsigned code)
{
    unsigned char bytes[3];
    int count = 0;

    if (code < 0x80) {
        bytes[0] = (unsigned char)code;
        count = 1;
    } else if (code < 0x800) {
        bytes[0] = (unsigned char)(0xC0 | (code >> 6));
        bytes[1] = (unsigned char)(0x80 | (code & 0x3F));
        count = 2;
    } else if (code < 0x10000) {
        bytes[0] = (unsigned char)(0xE0 | (code >> 12));
        bytes[1] = (unsigned char)(0x80 | ((code >> 6) & 0x3F));
        bytes[2] = (unsigned char)(0x80 | (code & 0x3F));
        count = 3;
    } else {
        return 0;
    }

    if (*used + (size_t)count + 1 > dst_size) {
        return 0;
    }
    memcpy(dst + *used, bytes, (size_t)count);
    *used += (size_t)count;
    dst[*used] = '\0';
    return 1;
}

static int hex_digit(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

static const char *json_value_start(const char *json, const char *key)
{
    char pattern[64];
    const char *found = json;

    snprintf(pattern, sizeof pattern, "\"%s\"", key);
    while ((found = strstr(found, pattern)) != NULL) {
        const char *after = found + strlen(pattern);
        while (*after == ' ' || *after == '\t' || *after == '\n' || *after == '\r') {
            after++;
        }
        if (*after == ':') {
            after++;
            while (*after == ' ' || *after == '\t' || *after == '\n' || *after == '\r') {
                after++;
            }
            return after;
        }
        found += strlen(pattern);
    }
    return NULL;
}

static int copy_json_string(const char *json, const char *key, char *dst, size_t dst_size)
{
    const char *value = json_value_start(json, key);
    size_t used = 0;

    if (value == NULL || *value != '"') {
        return 0;
    }
    value++;

    while (*value != '\0' && *value != '"') {
        if (*value == '\\') {
            value++;
            if (*value == '\0') {
                return 0;
            }
            if (*value == 'u') {
                unsigned code = 0;
                for (int i = 1; i <= 4; i++) {
                    int digit = hex_digit(value[i]);
                    if (digit < 0) {
                        return 0;
                    }
                    code = (code << 4) | (unsigned)digit;
                }
                if (!append_utf8(dst, dst_size, &used, code)) {
                    return 0;
                }
                value += 5;
                continue;
            }
            char plain = *value;
            if (*value == 'n') {
                plain = '\n';
            } else if (*value == 'r') {
                plain = '\r';
            } else if (*value == 't') {
                plain = '\t';
            }
            if (!append_utf8(dst, dst_size, &used, (unsigned char)plain)) {
                return 0;
            }
            value++;
            continue;
        }
        if (!append_utf8(dst, dst_size, &used, (unsigned char)*value)) {
            return 0;
        }
        value++;
    }

    return *value == '"';
}

static void trim_spaces(char *text)
{
    char *start = text;
    size_t len;

    while (*start == ' ' || *start == '\t') {
        start++;
    }
    if (start != text) {
        memmove(text, start, strlen(start) + 1);
    }
    len = strlen(text);
    while (len > 0 && (text[len - 1] == ' ' || text[len - 1] == '\t' || text[len - 1] == '\r')) {
        text[--len] = '\0';
    }
}

static void strip_list_marker(char *text)
{
    if ((text[0] == '-' || text[0] == '*') && text[1] == ' ') {
        memmove(text, text + 2, strlen(text + 2) + 1);
        return;
    }
    if (text[0] >= '0' && text[0] <= '9') {
        char *after = text;
        while (*after >= '0' && *after <= '9') {
            after++;
        }
        if (after[0] == '.' && after[1] == ' ') {
            memmove(text, after + 2, strlen(after + 2) + 1);
        }
    }
}

static int is_not_a_meal(const char *content)
{
    char line[64];
    size_t i = 0;
    size_t n = 0;

    while (content[i] == ' ' || content[i] == '\t' || content[i] == '\n' || content[i] == '\r') {
        i++;
    }
    while (content[i] != '\0' && content[i] != '\n' && n + 1 < sizeof line) {
        line[n++] = content[i++];
    }
    line[n] = '\0';
    trim_spaces(line);
    n = strlen(line);
    if (n > 0 && line[n - 1] == '.') {
        line[n - 1] = '\0';
    }
    return strcmp(line, "NOT_A_MEAL") == 0;
}

static void store_ingredients(char notes[][MAX_LEN], int *count, char *content)
{
    int added = 0;
    char *cursor = content;

    while (*cursor != '\0') {
        char *newline = strchr(cursor, '\n');
        char *text = cursor;
        if (newline != NULL) {
            *newline = '\0';
        }
        trim_spaces(text);
        strip_list_marker(text);
        trim_spaces(text);
        if (text[0] != '\0') {
            size_t len;
            if (*count == MAX_NOTES) {
                printf("list is full\n");
                break;
            }
            snprintf(notes[*count], MAX_LEN - 1, "%s", text);
            len = strlen(notes[*count]);
            notes[*count][len] = '\n';
            notes[*count][len + 1] = '\0';
            (*count)++;
            added++;
        }
        if (newline == NULL) {
            break;
        }
        cursor = newline + 1;
    }

    if (added > 0) {
        printf("saved\n");
    } else if (*count < MAX_NOTES) {
        printf("could not get ingredients\n");
    }
}

static int fetch_ingredients(const char *meal, char *content, size_t content_size)
{
    char plain[1024];
    char escaped[2048];
    char body[4096];
    wchar_t headers[] = L"Content-Type: application/json\r\n";
    char *response = NULL;
    HINTERNET session = NULL;
    HINTERNET connection = NULL;
    HINTERNET request = NULL;
    size_t total = 0;
    DWORD status = 0;
    DWORD status_size = sizeof status;
    int body_len;
    int ok = 0;

    snprintf(plain, sizeof plain, "%s%s", MEAL_PROMPT, meal);
    if (!json_escape(plain, escaped, sizeof escaped)) {
        printf("could not get ingredients\n");
        return 0;
    }
    body_len = snprintf(body, sizeof body,
        "{\"model\":\"%s\",\"stream\":false,"
        "\"messages\":[{\"role\":\"user\",\"content\":\"%s\"}]}",
        API_MODEL, escaped);
    if (body_len < 0 || (size_t)body_len >= sizeof body) {
        printf("could not get ingredients\n");
        return 0;
    }

    session = WinHttpOpen(L"notes/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (session == NULL) {
        printf("could not get ingredients\n");
        goto cleanup;
    }
    WinHttpSetTimeouts(session, 10000, 10000, 30000, 180000);

    connection = WinHttpConnect(session, API_HOST, API_PORT, 0);
    if (connection == NULL) {
        printf("could not get ingredients\n");
        goto cleanup;
    }

    request = WinHttpOpenRequest(connection, L"POST", API_PATH, NULL,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0);
    if (request == NULL) {
        printf("could not get ingredients\n");
        goto cleanup;
    }

    if (!WinHttpSendRequest(request, headers, (DWORD)-1, body, (DWORD)body_len,
            (DWORD)body_len, 0) ||
        !WinHttpReceiveResponse(request, NULL)) {
        printf("could not reach Ollama\n");
        goto cleanup;
    }

    WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
        WINHTTP_HEADER_NAME_BY_INDEX, &status, &status_size, WINHTTP_NO_HEADER_INDEX);

    response = malloc(RESPONSE_MAX);
    if (response == NULL) {
        printf("could not get ingredients\n");
        goto cleanup;
    }
    response[0] = '\0';

    while (total + 1 < RESPONSE_MAX) {
        DWORD available = 0;
        DWORD want;
        DWORD read = 0;
        if (!WinHttpQueryDataAvailable(request, &available)) {
            printf("could not get ingredients\n");
            goto cleanup;
        }
        if (available == 0) {
            break;
        }
        want = available;
        if (total + want >= RESPONSE_MAX) {
            want = (DWORD)(RESPONSE_MAX - 1 - total);
        }
        if (!WinHttpReadData(request, response + total, want, &read)) {
            printf("could not get ingredients\n");
            goto cleanup;
        }
        if (read == 0) {
            break;
        }
        total += read;
        response[total] = '\0';
    }

    if (status != 200) {
        char message[160];
        if (copy_json_string(response, "error", message, sizeof message)) {
            printf("request failed: %s\n", message);
        } else {
            printf("could not get ingredients\n");
        }
        goto cleanup;
    }

    if (!copy_json_string(response, "content", content, content_size)) {
        printf("could not get ingredients\n");
        goto cleanup;
    }
    ok = 1;

cleanup:
    free(response);
    if (request != NULL) {
        WinHttpCloseHandle(request);
    }
    if (connection != NULL) {
        WinHttpCloseHandle(connection);
    }
    if (session != NULL) {
        WinHttpCloseHandle(session);
    }
    return ok;
}

int main(void)
{
    char notes[MAX_NOTES][MAX_LEN];
    int count = 0;
    char line[MAX_LEN];

    printf("Commands: add <text> | list | edit <number> <text> | delete <number> | print | clear | meal <meal> | quit\n");

    while (fgets(line, sizeof line, stdin) != NULL) {
        if (strcmp(line, "quit\n") == 0 || strcmp(line, "quit") == 0) {
            break;
        }

        if (strcmp(line, "list\n") == 0 || strcmp(line, "list") == 0) {
            if (count == 0) {
                printf("(empty)\n");
            }
            for (int i = 0; i < count; i++) {
                printf("%d. %s", i + 1, notes[i]);
            }
            continue;
        }

        if (strcmp(line, "print\n") == 0 || strcmp(line, "print") == 0) {
            if (count == 0) {
                printf("(empty)\n");
                continue;
            }
            FILE *out = fopen(NOTES_FILE, "w");
            if (out == NULL) {
                printf("could not write file\n");
                continue;
            }
            for (int i = 0; i < count; i++) {
                fprintf(out, "%d. %s", i + 1, notes[i]);
            }
            fclose(out);
            printf("printed\n");
            continue;
        }

        if (strcmp(line, "clear\n") == 0 || strcmp(line, "clear") == 0) {
            count = 0;
            printf("cleared\n");
            continue;
        }

        if (strncmp(line, "add ", 4) == 0) {
            if (count == MAX_NOTES) {
                printf("list is full\n");
                continue;
            }
            snprintf(notes[count], MAX_LEN, "%s", line + 4);
            count++;
            printf("saved\n");
            continue;
        }

        if (strncmp(line, "edit ", 5) == 0) {
            char *end;
            long n = strtol(line + 5, &end, 10);
            if (end == line + 5 || *end != ' ') {
                printf("unknown command\n");
                continue;
            }
            if (n < 1 || n > count) {
                printf("no such note\n");
                continue;
            }
            if (end[1] == '\0' || end[1] == '\n') {
                printf("missing text\n");
                continue;
            }
            snprintf(notes[(int)n - 1], MAX_LEN, "%s", end + 1);
            printf("updated\n");
            continue;
        }

        if (strncmp(line, "delete ", 7) == 0) {
            char *end;
            long n = strtol(line + 7, &end, 10);
            if (end == line + 7 || (*end != '\n' && *end != '\0')) {
                printf("unknown command\n");
                continue;
            }
            if (n < 1 || n > count) {
                printf("no such note\n");
                continue;
            }
            for (int i = (int)n - 1; i < count - 1; i++) {
                memcpy(notes[i], notes[i + 1], MAX_LEN);
            }
            count--;
            printf("deleted\n");
            continue;
        }

        if (strncmp(line, "meal ", 5) == 0) {
            char meal[MAX_LEN];
            char content[8192];
            size_t len;

            snprintf(meal, sizeof meal, "%s", line + 5);
            len = strlen(meal);
            if (len > 0 && meal[len - 1] == '\n') {
                meal[--len] = '\0';
            }
            while (len > 0 && meal[len - 1] == ' ') {
                meal[--len] = '\0';
            }
            if (meal[0] == '\0') {
                printf("missing text\n");
                continue;
            }
            if (fetch_ingredients(meal, content, sizeof content)) {
                if (is_not_a_meal(content)) {
                    printf("warning: \"%s\" does not fit the meal category\n", meal);
                } else {
                    store_ingredients(notes, &count, content);
                }
            }
            continue;
        }

        printf("unknown command\n");
    }

    return 0;
}
