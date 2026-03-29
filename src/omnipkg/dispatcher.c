/*
 * omnipkg dispatcher — C replacement for dispatcher.py
 *
 * Happy-path only: handles the 95% case that runs in <1ms.
 * Falls back to Python dispatcher for edge cases (auto-adopt, shim mode, swap python, etc.)
 *
 * Build:
 *   gcc -O2 -o omnipkg_dispatch dispatcher.c -o omnipkg_dispatch
 *
 * Install:
 *   cp omnipkg_dispatch $VENV/bin/8pkg
 *   cp omnipkg_dispatch $VENV/bin/omnipkg
 *   # versioned symlinks still work: 8pkg39, 8pkg312, etc.
 *
 * Falls back to Python when:
 *   - interpreter path not in registry
 *   - target interpreter doesn't exist
 *   - called as python/pip shim
 *   - auto-adopt needed
 *   - OMNIPKG_FORCE_PYTHON_DISPATCH=1 (escape hatch)
 */
#include <stdint.h>
#include <errno.h>
#ifdef _WIN32
#include <winsock2.h>
#else
#include <unistd.h>
#include <sys/stat.h>
#include <libgen.h>
#include <limits.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <glob.h>
#include <windows.h>
#include <direct.h>
#include <io.h>
#include <process.h>
#include <sys/stat.h>
#define realpath(path, resolved) _fullpath(resolved, path, 4096)
#undef MAX_PATH
#define MAX_PATH 4096
static int win_execv(const char *path, char *const argv[]) {
    intptr_t r = _spawnv(_P_WAIT, path, (const char *const *)argv);
    if (r == -1) return -1;
    exit((int)r);
}
#define execv(p, a) win_execv(p, a)
static char *omnipkg_dirname(char *path) {
    static char buf[4096];
    char *p, *last = NULL;
    strncpy(buf, path, sizeof(buf)-1);
    buf[sizeof(buf)-1] = 0;
    for (p = buf; *p; p++) { if (*p == 47 || *p == 92) last = p; }
    if (last) { *last = 0; } else { buf[0] = 46; buf[1] = 0; }
    return buf;
}
static char *omnipkg_basename(char *path) {
    char *p, *last = NULL;
    for (p = path; *p; p++) { if (*p == 47 || *p == 92) last = p; }
    return last ? last + 1 : path;
}
#define dirname(p) omnipkg_dirname(p)
#define basename(p) omnipkg_basename(p)
#else
#include <unistd.h>
#include <sys/stat.h>
#include <libgen.h>
#include <limits.h>
#endif
#include <dlfcn.h>
#include <fcntl.h>
#include <glob.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define MAX_PATH 4096
#define MAX_VERSION 32
#define MAX_JSON 65536   /* registry.json is tiny */

/* ── tiny helpers ──────────────────────────────────────────────────────── */

static int file_exists(const char *path) {
    struct stat st;
    return stat(path, &st) == 0;
}

/* resolve symlink chain to real path.
 * If argv[0] is a bare name (no slash), search PATH first. */
static void real_path(const char *in, char *out, size_t n) {
    char tmp[MAX_PATH];

    if (strchr(in, '/') == NULL) {
        /* bare name like "8pkg" — search PATH */
        const char *path_env = getenv("PATH");
        if (path_env) {
            char path_copy[16384];
            strncpy(path_copy, path_env, sizeof(path_copy) - 1);
            path_copy[sizeof(path_copy) - 1] = '\0';
            char *dir = strtok(path_copy, ":");
            while (dir) {
                char candidate[MAX_PATH];
                snprintf(candidate, sizeof(candidate), "%s/%s", dir, in);
                if (file_exists(candidate)) {
                    if (realpath(candidate, tmp) != NULL) {
                        strncpy(out, tmp, n - 1);
                        out[n - 1] = '\0';
                        return;
                    }
                }
                dir = strtok(NULL, ":");
            }
        }
    }

    /* has slash, or PATH search failed — realpath directly */
    if (realpath(in, tmp) != NULL)
        strncpy(out, tmp, n - 1);
    else
        strncpy(out, in, n - 1);
    out[n - 1] = '\0';
}

/* dirname without modifying input */
static void dir_of(const char *path, char *out, size_t n) {
    char tmp[MAX_PATH];
    strncpy(tmp, path, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    strncpy(out, dirname(tmp), n - 1);
    out[n - 1] = '\0';
}

/* basename without modifying input */
static void base_of(const char *path, char *out, size_t n) {
    char tmp[MAX_PATH];
    strncpy(tmp, path, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    strncpy(out, basename(tmp), n - 1);
    out[n - 1] = '\0';
}

/* ── version parsing: "8pkg39" -> "3.9", "8pkg312" -> "3.12" ──────────── */

static int parse_version_suffix(const char *prog, char *out, size_t n) {
    /* skip leading "8pkg" or "omnipkg" */
    const char *p = prog;
    if      (strncmp(p, "8pkg",    4) == 0) p += 4;
    else if (strncmp(p, "omnipkg", 7) == 0) p += 7;
    else return 0;

    size_t len = strlen(p);
    if (len == 2 && p[0] >= '2' && p[0] <= '4' && p[1] >= '0' && p[1] <= '9') {
        /* "39" -> "3.9" */
        snprintf(out, n, "%c.%c", p[0], p[1]);
        return 1;
    }
    if (len == 3 && p[0] >= '2' && p[0] <= '4') {
        /* "312" -> "3.12", "311" -> "3.11" */
        snprintf(out, n, "%c.%c%c", p[0], p[1], p[2]);
        return 1;
    }
    return 0;
}

/* ── minimal JSON value extractor ─────────────────────────────────────── */
/*
 * Finds the value of "key" in a flat JSON object string.
 * Handles:  "key": "value"
 * Not a real JSON parser — sufficient for registry.json.
 */
static int json_get_str(const char *json, const char *key, char *out, size_t n) {
    char needle[MAX_VERSION + 8];
    snprintf(needle, sizeof(needle), "\"%s\"", key);
    const char *p = strstr(json, needle);
    if (!p) return 0;
    p += strlen(needle);
    while (*p == ' ' || *p == '\t' || *p == ':' || *p == ' ') p++;
    if (*p != '"') return 0;
    p++; /* skip opening quote */
    size_t i = 0;
    while (*p && *p != '"' && i < n - 1)
        out[i++] = *p++;
    out[i] = '\0';
    return i > 0;
}

/* ── registry lookup ───────────────────────────────────────────────────── */

static int registry_lookup(const char *venv_root, const char *version,
                            char *python_path, size_t n) {
    char reg_path[MAX_PATH];
    snprintf(reg_path, sizeof(reg_path),
             "%s/.omnipkg/interpreters/registry.json", venv_root);

    FILE *f = fopen(reg_path, "r");
    if (!f) return 0;

    char buf[MAX_JSON];
    size_t len = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[len] = '\0';

    return json_get_str(buf, version, python_path, n);
}

/* ── venv root detection ────────────────────────────────────────────────── */

static void find_venv_root(const char *self_path, char *out, size_t n) {
    /* 1. OMNIPKG_VENV_ROOT env var */
    const char *env = getenv("OMNIPKG_VENV_ROOT");
    if (env && file_exists(env)) {
        strncpy(out, env, n - 1);
        out[n - 1] = '\0';
        return;
    }

    /* 2. Walk up from self: bin/ -> venv_root */
    char self_dir[MAX_PATH];
    dir_of(self_path, self_dir, sizeof(self_dir));
    /* self_dir is $VENV/bin — parent is $VENV */
    char parent[MAX_PATH];
    dir_of(self_dir, parent, sizeof(parent));

    /* Sanity: registry must exist there */
    char reg[MAX_PATH];
    snprintf(reg, sizeof(reg), "%s/.omnipkg/interpreters/registry.json", parent);
    if (file_exists(reg)) {
        strncpy(out, parent, n - 1);
        out[n - 1] = '\0';
        return;
    }

    /* 3. sys.prefix fallback: CONDA_PREFIX or VIRTUAL_ENV */
    const char *conda = getenv("CONDA_PREFIX");
    if (conda) { strncpy(out, conda, n - 1); out[n - 1] = '\0'; return; }
    const char *venv  = getenv("VIRTUAL_ENV");
    if (venv)  { strncpy(out, venv,  n - 1); out[n - 1] = '\0'; return; }

    out[0] = '\0';
}

/* ── self-aware config lookup ───────────────────────────────────────────── */

static int read_self_config(const char *self_dir, char *python_path, size_t n) {
    char cfg[MAX_PATH];
    snprintf(cfg, sizeof(cfg), "%s/.omnipkg_config.json", self_dir);
    FILE *f = fopen(cfg, "r");
    if (!f) return 0;

    char buf[MAX_JSON];
    size_t len = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[len] = '\0';

    return json_get_str(buf, "python_executable", python_path, n);
}

/* ── daemon socket communication ───────────────────────────────────────── */

static int write_all(int fd, const void *buf, size_t count) {
    const char *p = buf;
    while (count > 0) {
        ssize_t r = write(fd, p, count);
        if (r <= 0) return 0;
        p += r;
        count -= r;
    }
    return 1;
}

static int send_json_msg(int sock, const char *json_str) {
    uint64_t len = strlen(json_str);
    uint64_t be_len = 0;
    for (int i = 0; i < 8; i++) {
        ((uint8_t*)&be_len)[7 - i] = (len >> (i * 8)) & 0xFF;
    }
    if (!write_all(sock, &be_len, 8)) return 0;
    if (!write_all(sock, json_str, len)) return 0;
    return 1;
}

static char* recv_json_msg(int sock) {
    uint8_t be_len[8];
    int n = 0;
    while (n < 8) {
        int r = read(sock, be_len + n, 8 - n);
        if (r <= 0) return NULL;
        n += r;
    }
    uint64_t len = 0;
    for (int i = 0; i < 8; i++) {
        len = (len << 8) | be_len[i];
    }
    if (len > 1024 * 1024 * 10) return NULL;
    char *buf = malloc(len + 1);
    if (!buf) return NULL;
    uint64_t total = 0;
    while (total < len) {
        int r = read(sock, buf + total, len - total);
        if (r <= 0) { free(buf); return NULL; }
        total += r;
    }
    buf[len] = '\0';
    return buf;
}

/* Encode a Unicode codepoint as UTF-8 and write to out. */
static void emit_utf8(uint32_t cp, FILE *out) {
    if (cp < 0x80) {
        fputc((int)cp, out);
    } else if (cp < 0x800) {
        fputc(0xC0 | (cp >> 6), out);
        fputc(0x80 | (cp & 0x3F), out);
    } else if (cp < 0x10000) {
        fputc(0xE0 | (cp >> 12), out);
        fputc(0x80 | ((cp >> 6) & 0x3F), out);
        fputc(0x80 | (cp & 0x3F), out);
    } else {
        fputc(0xF0 | (cp >> 18), out);
        fputc(0x80 | ((cp >> 12) & 0x3F), out);
        fputc(0x80 | ((cp >> 6) & 0x3F), out);
        fputc(0x80 | (cp & 0x3F), out);
    }
}

static uint32_t parse_hex4(const char *p) {
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) {
        char c = p[i];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= c - '0';
        else if (c >= 'a' && c <= 'f') v |= c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') v |= c - 'A' + 10;
    }
    return v;
}

static void print_unescaped(const char *str, FILE *out) {
    while (*str) {
        if (*str == '"') break;
        if (*str == '\\') {
            str++;
            if (*str == 'n')       { fputc('\n', out); str++; }
            else if (*str == 't')  { fputc('\t', out); str++; }
            else if (*str == 'r')  { fputc('\r', out); str++; }
            else if (*str == '"')  { fputc('"',  out); str++; }
            else if (*str == '\\') { fputc('\\', out); str++; }
            else if (*str == '/')  { fputc('/',  out); str++; }
            else if (*str == 'b')  { fputc('\b', out); str++; }
            else if (*str == 'f')  { fputc('\f', out); str++; }
            else if (*str == 'u' && str[1] && str[2] && str[3] && str[4]) {
                /* \uXXXX — decode and emit UTF-8.
                 * Also handle surrogate pairs: \uD800–\uDBFF followed by \uDC00–\uDFFF */
                uint32_t hi = parse_hex4(str + 1);
                str += 5; /* consume 'u' + 4 hex digits */
                if (hi >= 0xD800 && hi <= 0xDBFF &&
                    str[0] == '\\' && str[1] == 'u' &&
                    str[2] && str[3] && str[4] && str[5]) {
                    /* High surrogate — look for low surrogate */
                    uint32_t lo = parse_hex4(str + 2);
                    if (lo >= 0xDC00 && lo <= 0xDFFF) {
                        uint32_t cp = 0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00);
                        emit_utf8(cp, out);
                        str += 6; /* consume second \uXXXX */
                    } else {
                        emit_utf8(hi, out); /* lone high surrogate — best effort */
                    }
                } else {
                    emit_utf8(hi, out);
                }
            } else {
                /* Unknown escape — pass through as-is */
                fputc('\\', out);
                if (*str) { fputc(*str, out); str++; }
            }
        } else {
            /* Regular UTF-8 byte — pass through directly */
            fputc((unsigned char)*str, out);
            str++;
        }
    }
    fflush(out);
}

static int json_get_raw_str(const char *json, const char *key, char **out_start) {
    char needle[256];
    snprintf(needle, sizeof(needle), "\"%s\"", key);
    const char *p = strstr(json, needle);
    if (!p) return 0;
    p += strlen(needle);
    while (*p == ' ' || *p == '\t' || *p == ':') p++;
    if (*p != '"') return 0;
    p++;
    *out_start = (char*)p;
    return 1;
}

static int json_get_int(const char *json, const char *key, int *out) {
    char needle[256];
    snprintf(needle, sizeof(needle), "\"%s\"", key);
    const char *p = strstr(json, needle);
    if (!p) return 0;
    p += strlen(needle);
    while (*p == ' ' || *p == '\t' || *p == ':') p++;
    *out = atoi(p);
    return 1;
}

/*
 * try_daemon_uv — Phase 1 fast path.
 *
 * Sends a run_uv request directly to the daemon's dedicated UV worker.
 * The worker has a warm Tokio runtime + cached site-packages — ~5ms.
 * Returns 1 and populates out_json on success, 0 on failure/unavailable.
 *
 * Deliberately has a short timeout (5s) so we fall through to the dlopen
 * path quickly if the daemon is unavailable.
 */
static int try_daemon_uv(
    const char *target_python,
    const char *pkg_spec,
    char       *out_json,
    int         max_out
) {
    int debug = (getenv("OMNIPKG_DEBUG") != NULL &&
                 strcmp(getenv("OMNIPKG_DEBUG"), "1") == 0);

    /* ── Locate socket (same logic as try_daemon_cli) ── */
    char sock_path[MAX_PATH];
    sock_path[0] = '\0';
    const char *ann_path = "/tmp/omnipkg/daemon_connection.txt";
    FILE *af = fopen(ann_path, "r");
    if (af) {
        char line[MAX_PATH];
        if (fgets(line, sizeof(line), af)) {
            line[strcspn(line, "\r\n")] = '\0';
            if (strncmp(line, "unix://", 7) == 0) {
                strncpy(sock_path, line + 7, sizeof(sock_path) - 1);
                sock_path[sizeof(sock_path) - 1] = '\0';
            }
        }
        fclose(af);
    }
    if (!sock_path[0]) {
        const char *tmpdir = getenv("TMPDIR");
        if (!tmpdir) tmpdir = "/tmp";
        snprintf(sock_path, sizeof(sock_path), "%s/omnipkg/omnipkg_daemon.sock", tmpdir);
    }

    struct stat st;
    if (stat(sock_path, &st) != 0) return 0;

    int sock = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sock < 0) return 0;

    /* Short timeouts — UV install is fast, bail quickly if something's wrong */
    struct timeval tv = { 5, 0 };
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, sock_path, sizeof(addr.sun_path) - 1);
    if (connect(sock, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        close(sock);
        return 0;
    }

    /* Build the home/cache dir */
    char cache_dir[MAX_PATH];
    const char *home = getenv("HOME");
    snprintf(cache_dir, sizeof(cache_dir), "%s/.cache/uv", home ? home : "/tmp");

    /* Build run_uv request JSON.
     * uv_args: ["pip","install","--cache-dir","<dir>","--python","<exe>","--link-mode","symlink","<pkg>"]
     */
    char req[4096];
    int rlen = snprintf(req, sizeof(req),
        "{\"type\":\"run_uv\","
        "\"python_exe\":\"%s\","
        "\"uv_args\":[\"pip\",\"install\","
        "\"--cache-dir\",\"%s\","
        "\"--python\",\"%s\","
        "\"--link-mode\",\"symlink\","
        "\"%s\"]}",
        target_python, cache_dir, target_python, pkg_spec);

    if (rlen <= 0 || rlen >= (int)sizeof(req)) { close(sock); return 0; }

    if (debug) fprintf(stderr, "[C-DISPATCH] try_daemon_uv: sending run_uv for %s\n", pkg_spec);

    if (!send_json_msg(sock, req)) { close(sock); return 0; }

    /* Drain frames until COMPLETED/ERROR */
    while (1) {
        char *msg = recv_json_msg(sock);
        if (!msg) break;

        char *status_start;
        if (json_get_raw_str(msg, "status", &status_start)) {
            if (strncmp(status_start, "COMPLETED", 9) == 0) {
                int exit_code = 0;
                json_get_int(msg, "exit_code", &exit_code);

                if (exit_code == 0) {
                    /* Build changelog JSON from installed/removed arrays in msg.
                     * The daemon returns {"status":"COMPLETED","exit_code":0,
                     * "installed":[["name","ver"],...],"removed":[...]} */
                    /* Copy the full msg as out_json for the caller to use */
                    strncpy(out_json, msg, max_out - 1);
                    out_json[max_out - 1] = '\0';
                    free(msg);
                    close(sock);
                    if (debug) fprintf(stderr, "[C-DISPATCH] try_daemon_uv: success\n");
                    return 1;
                }
                free(msg);
                close(sock);
                return 0;
            } else if (strncmp(status_start, "FFI_UNAVAILABLE", 15) == 0 ||
                       strncmp(status_start, "ERROR", 5) == 0) {
                free(msg);
                close(sock);
                return 0;
            }
        }
        /* stream frames — ignore */
        free(msg);
    }

    close(sock);
    return 0;
}

static int try_daemon_cli(const char *target_python, int argc, char **argv, int version_injected, const char *forced_version) {
    int debug = (getenv("OMNIPKG_DEBUG") != NULL &&
                 strcmp(getenv("OMNIPKG_DEBUG"), "1") == 0);

    /* ── Locate the socket ──────────────────────────────────────────────
     * Strategy (same logic as DaemonClient in Python):
     *   1. Read /tmp/omnipkg/daemon_connection.txt  (written at bind time)
     *      Format: "unix:///path/to/socket"
     *   2. Fall back to $TMPDIR/omnipkg/omnipkg_daemon.sock
     *   3. Fall back to /tmp/omnipkg/omnipkg_daemon.sock
     * Using a fixed /tmp prefix for the announcement file means we find
     * the daemon even when TMPDIR differs between shell and daemon process.
     * ─────────────────────────────────────────────────────────────────── */
    char sock_path[MAX_PATH];
    sock_path[0] = '\0';

    /* Try announcement file first */
    const char *ann_path = "/tmp/omnipkg/daemon_connection.txt";
    FILE *af = fopen(ann_path, "r");
    if (af) {
        char line[MAX_PATH];
        if (fgets(line, sizeof(line), af)) {
            /* strip newline */
            line[strcspn(line, "\r\n")] = '\0';
            if (strncmp(line, "unix://", 7) == 0) {
                strncpy(sock_path, line + 7, sizeof(sock_path) - 1);
                sock_path[sizeof(sock_path) - 1] = '\0';
                if (debug) fprintf(stderr, "[C-DISPATCH] found socket via announcement: %s\n", sock_path);
            }
        }
        fclose(af);
    }

    /* Fall back to TMPDIR-based path */
    if (!sock_path[0]) {
        const char *tmpdir = getenv("TMPDIR");
        if (!tmpdir) tmpdir = "/tmp";
        snprintf(sock_path, sizeof(sock_path), "%s/omnipkg/omnipkg_daemon.sock", tmpdir);
        if (debug) fprintf(stderr, "[C-DISPATCH] no announcement file, trying: %s\n", sock_path);
    }

    if (debug) fprintf(stderr, "[C-DISPATCH] try_daemon_cli: sock=%s\n", sock_path);

    /* Quick existence check before paying the connect syscall */
    struct stat st;
    if (stat(sock_path, &st) != 0) {
        if (debug) fprintf(stderr, "[C-DISPATCH] daemon socket not found — skipping\n");
        return 0;
    }

    int sock = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sock < 0) return 0;

    /* Short send timeout — if we can't write to the daemon in 2s something is wrong. */
    struct timeval tv_send = { 2, 0 };
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv_send, sizeof(tv_send));
    /* Recv timeout: generous for slow PyPI ops.  When the worker sends
     * NEEDS_INPUT the C side reads from the terminal and replies; after that
     * the worker resumes.  We reset the timeout after each message, so this
     * is really "max idle time between any two messages", not a wall-clock
     * limit for the whole command.  300 s covers the rare case where a human
     * is very slow at typing a prompt answer.                               */
    struct timeval tv_recv = { 300, 0 };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv_recv, sizeof(tv_recv));

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, sock_path, sizeof(addr.sun_path) - 1);
    if (connect(sock, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        if (debug) fprintf(stderr, "[C-DISPATCH] daemon connect failed: %s\n", strerror(errno));
        close(sock);
        return 0;
    }

    if (debug) fprintf(stderr, "[C-DISPATCH] daemon connected — sending run_cli\n");

    char cwd[MAX_PATH];
    if (!getcwd(cwd, sizeof(cwd))) cwd[0] = '\0';

    char *req = malloc(1024 * 1024);
    if (!req) { close(sock); return 0; }

    int len = sprintf(req, "{\"type\":\"run_cli\",\"isatty\":%s", isatty(1) ? "true" : "false");

    if (target_python && target_python[0]) {
        len += sprintf(req + len, ",\"python_exe\":\"%s\"", target_python);
    }

    if (cwd[0]) {
        len += sprintf(req + len, ",\"cwd\":\"%s\"", cwd);
    }

    len += sprintf(req + len, ",\"argv\":[\"8pkg\"");
    /* Do NOT forward --python <ver> to the daemon worker.  The worker is already
     * running under the correct interpreter (target_python); passing --python
     * causes cli.main() to call ensure_python_or_relaunch() which does execve
     * and kills the worker without ever sending COMPLETED → "daemon unhealthy".
     * The python_exe field in the request already tells the daemon which worker
     * pool to use, so --python in argv is redundant and harmful here. */
    for (int i = 1; i < argc; i++) {
        /* skip --python and its argument */
        if (strcmp(argv[i], "--python") == 0) { i++; continue; }
        req[len++] = ',';
        req[len++] = '"';
        char *p = argv[i];
        while (*p) {
            if (*p == '"' || *p == '\\') {
                req[len++] = '\\';
                req[len++] = *p;
            } else {
                req[len++] = *p;
            }
            p++;
        }
        req[len++] = '"';
    }
    req[len++] = ']';
    req[len++] = '}';
    req[len] = '\0';

    if (!send_json_msg(sock, req)) {
        free(req);
        close(sock);
        /* Stale socket — nuke announcement so next call doesn't hit it again */
        remove(ann_path);
        if (debug) fprintf(stderr, "[C-DISPATCH] daemon send failed — falling back to execv\n");
        return 0;
    }
    free(req);

    int got_terminal_status = 0;
    while (1) {
        char *msg = recv_json_msg(sock);
        if (!msg) break;

        char *stream_type;
        if (json_get_raw_str(msg, "stream", &stream_type)) {
            char *data_start;
            if (json_get_raw_str(msg, "data", &data_start)) {
                FILE *out = (strncmp(stream_type, "stderr", 6) == 0) ? stderr : stdout;
                print_unescaped(data_start, out);
            }
        } else if (json_get_raw_str(msg, "status", &stream_type)) {
            if (strncmp(stream_type, "COMPLETED", 9) == 0) {
                int exit_code = 0;
                json_get_int(msg, "exit_code", &exit_code);
                free(msg);
                close(sock);
                exit(exit_code);
            } else if (strncmp(stream_type, "NEEDS_INPUT", 11) == 0) {
                /* Worker is blocking on input() — print the prompt to the real
                 * terminal, read a line, and send it back as stdin_line. */
                char *prompt_start;
                if (json_get_raw_str(msg, "prompt", &prompt_start)) {
                    print_unescaped(prompt_start, stdout);
                }
                free(msg);

                /* Read one line from the real terminal (stdin fd 0). */
                char input_buf[4096];
                size_t ilen = 0;
                input_buf[0] = '\0';
                if (fgets(input_buf, sizeof(input_buf), stdin)) {
                    /* Strip trailing newline — worker will add it back. */
                    ilen = strlen(input_buf);
                    if (ilen > 0 && input_buf[ilen - 1] == '\n')
                        input_buf[--ilen] = '\0';
                }
                /* else: EOF/error — input_buf stays empty, ilen stays 0 */

                /* Build {"type":"stdin_line","data":"<escaped>"} */
                char *reply = malloc(ilen * 6 + 64); /* generous for JSON escaping */
                if (!reply) { close(sock); return 0; }
                int rlen = sprintf(reply, "{\"type\":\"stdin_line\",\"data\":\"");
                for (size_t k = 0; k < ilen; k++) {
                    unsigned char c = (unsigned char)input_buf[k];
                    if      (c == '"')  { reply[rlen++] = '\\'; reply[rlen++] = '"'; }
                    else if (c == '\\') { reply[rlen++] = '\\'; reply[rlen++] = '\\'; }
                    else if (c == '\n') { reply[rlen++] = '\\'; reply[rlen++] = 'n'; }
                    else if (c == '\r') { reply[rlen++] = '\\'; reply[rlen++] = 'r'; }
                    else if (c == '\t') { reply[rlen++] = '\\'; reply[rlen++] = 't'; }
                    else if (c < 0x20) {
                        rlen += sprintf(reply + rlen, "\\u%04x", c);
                    } else {
                        reply[rlen++] = c;
                    }
                }
                reply[rlen++] = '"';
                reply[rlen++] = '}';
                reply[rlen]   = '\0';

                int ok = send_json_msg(sock, reply);
                free(reply);
                if (!ok) { close(sock); return 0; }
                /* Continue the recv loop — worker will resume and send more output. */
                continue;
            } else if (strncmp(stream_type, "ERROR", 5) == 0) {
                /* Daemon-side error (broken pipe, crash, etc.) — don't exit,
                 * fall through so main() retries via execv Python fallback. */
                got_terminal_status = 1;
                free(msg);
                break;
            }
        }
        free(msg);
    }

    close(sock);
    /* If we got an ERROR status or recv loop broke without COMPLETED,
     * the daemon is unhealthy. Nuke the announcement so the next call
     * doesn't connect to a dead socket, then signal fallback. */
    if (!got_terminal_status) {
        remove(ann_path);
        if (debug) fprintf(stderr, "[C-DISPATCH] daemon unhealthy — falling back to execv\n");
    }
    return 0;
}

static void fallback_to_python(const char *self_dir, char **argv) {
    /*
     * Find the Python that owns this venv/bin dir and re-exec
     * the original dispatcher.py via  python -m omnipkg.dispatcher
     */
    char py[MAX_PATH];

    /* Try the config first */
    if (!read_self_config(self_dir, py, sizeof(py)) || !file_exists(py)) {
        /* Try common names in self_dir */
        const char *names[] = {"python3.11","python3.10","python3.9",
                               "python3","python",NULL};
        int found = 0;
        for (int i = 0; names[i]; i++) {
            snprintf(py, sizeof(py), "%s/%s", self_dir, names[i]);
            if (file_exists(py)) { found = 1; break; }
        }
        if (!found) {
            fprintf(stderr, "omnipkg: cannot find host Python for fallback\n");
            exit(1);
        }
    }

    /* Build new argv: python -m omnipkg.dispatcher <original args> */
    int argc = 0;
    while (argv[argc]) argc++;
    char **new_argv = malloc((argc + 4) * sizeof(char *));
    new_argv[0] = py;
    new_argv[1] = "-m";
    new_argv[2] = "omnipkg.dispatcher";
    for (int i = 1; i < argc; i++)
        new_argv[i + 2] = argv[i];
    new_argv[argc + 2] = NULL;

    execv(py, new_argv);
    perror("omnipkg: execv fallback failed");
    exit(1);
}

/* ── stamp-file helpers ─────────────────────────────────────────────────── */
/*
 * Stamp file: /tmp/omnipkg/ffi_ok/<djb2hash>.stamp
 * Presence = uv_ffi verified/installed for that interpreter.
 * Check cost: one stat() call, ~2µs — replaces the old 60ms subprocess check.
 */
static void ffi_stamp_path(const char *python_exe, char *out, size_t n) {
    unsigned long h = 5381;
    const unsigned char *p = (const unsigned char *)python_exe;
    while (*p) { h = h * 33 ^ *p++; }
    snprintf(out, n, "/tmp/omnipkg/ffi_ok/%lu.stamp", h);
}

static int ffi_stamp_exists(const char *python_exe) {
    char stamp[MAX_PATH];
    ffi_stamp_path(python_exe, stamp, sizeof(stamp));
    struct stat st;
    return stat(stamp, &st) == 0;
}

static void ffi_stamp_write(const char *python_exe) {
    mkdir("/tmp/omnipkg", 0755);
    mkdir("/tmp/omnipkg/ffi_ok", 0755);
    char stamp[MAX_PATH];
    ffi_stamp_path(python_exe, stamp, sizeof(stamp));
    FILE *f = fopen(stamp, "w");
    if (f) { fputs(python_exe, f); fclose(f); }
}

/*
 * ensure_uv_ffi_for_python — install uv_ffi into a non-main interpreter
 * (e.g. cpython-3.9.23) if it is missing, then notify the daemon worker
 * to reload its FFI handle so subsequent run_uv calls use FFI not subprocess.
 *
 * Fast path: stamp file exists → single stat(), ~2µs, no subprocess.
 * Slow path: first call only → install (~1s one-time) → write stamp.
 *
 * Returns 1 if uv_ffi is available (either was already or just installed).
 */
static int ensure_uv_ffi_for_python(
    const char *target_python,
    const char *venv_root,
    const char *sock_path,
    int debug
) {
    /* Fast path — stamp present means already verified/installed */
    if (ffi_stamp_exists(target_python)) {
        if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi stamp hit for %s\n",
                           target_python);
        return 1;
    }

    if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi missing for %s — installing\n",
                       target_python);

    /* Find uv */
    char uv_path[MAX_PATH];
    const char *uv_exe = NULL;
    snprintf(uv_path, sizeof(uv_path), "%s/bin/uv", venv_root);
    if (file_exists(uv_path)) {
        uv_exe = uv_path;
    } else {
        const char *path_env = getenv("PATH");
        if (path_env) {
            char path_copy[16384];
            strncpy(path_copy, path_env, sizeof(path_copy) - 1);
            path_copy[sizeof(path_copy) - 1] = '\0';
            char *dir = strtok(path_copy, ":");
            static char uv_found2[MAX_PATH];
            while (dir) {
                snprintf(uv_found2, sizeof(uv_found2), "%s/uv", dir);
                if (file_exists(uv_found2)) { uv_exe = uv_found2; break; }
                dir = strtok(NULL, ":");
            }
        }
    }

    char install_cmd[MAX_PATH * 4];
    int rc = -1;
    if (uv_exe) {
        snprintf(install_cmd, sizeof(install_cmd),
            "\"%s\" pip install --python \"%s\" uv_ffi",
            uv_exe, target_python);
        if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi install: %s\n", install_cmd);
        rc = system(install_cmd);
    }
    if (rc != 0) {
        snprintf(install_cmd, sizeof(install_cmd),
            "\"%s\" -m pip install uv_ffi", target_python);
        if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi pip fallback: %s\n", install_cmd);
        rc = system(install_cmd);
    }

    if (rc != 0) {
        if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi install failed rc=%d\n", rc);
        return 0;
    }

    if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi installed for %s\n", target_python);
    ffi_stamp_write(target_python);  /* fast path on all future calls */

    /* Tell the daemon worker to reload its FFI handle for this interpreter.
     * Fire-and-forget: we don't wait for a reply — the worker will reload
     * asynchronously and the next run_uv call will use FFI instead of subprocess. */
    if (sock_path && sock_path[0]) {
        struct stat _st;
        if (stat(sock_path, &_st) == 0) {
            int _s = socket(AF_UNIX, SOCK_STREAM, 0);
            if (_s >= 0) {
                struct timeval _tv = { 1, 0 };
                setsockopt(_s, SOL_SOCKET, SO_SNDTIMEO, &_tv, sizeof(_tv));
                setsockopt(_s, SOL_SOCKET, SO_RCVTIMEO, &_tv, sizeof(_tv));
                struct sockaddr_un _addr;
                memset(&_addr, 0, sizeof(_addr));
                _addr.sun_family = AF_UNIX;
                strncpy(_addr.sun_path, sock_path, sizeof(_addr.sun_path) - 1);
                if (connect(_s, (struct sockaddr*)&_addr, sizeof(_addr)) == 0) {
                    char _msg[MAX_PATH + 64];
                    snprintf(_msg, sizeof(_msg),
                        "{\"type\":\"reload_ffi\",\"python_exe\":\"%s\"}", target_python);
                    send_json_msg(_s, _msg);
                    /* don't wait for reply — fire and forget */
                    if (debug) fprintf(stderr,
                        "[C-DISPATCH] sent reload_ffi to daemon for %s\n", target_python);
                }
                close(_s);
            }
        }
    }

    return 1;
}

/* ── find or auto-install uv_ffi .so ───────────────────────────────────── */

static int find_or_install_uv_ffi_so(
    const char *venv_root,
    const char *target_python,
    char *so_path_out,
    size_t so_path_n
) {
    int debug = (getenv("OMNIPKG_DEBUG") != NULL &&
                 strcmp(getenv("OMNIPKG_DEBUG"), "1") == 0);

    /* ── 0. Ask Python directly — handles editable/dev/vendor installs ── */
    {
        char _py_cmd[MAX_PATH * 2];
        char _py_out[MAX_PATH] = "";
        snprintf(_py_cmd, sizeof(_py_cmd),
            "\"%s\" -c \""
            "import os\n"
            "try:\n"
            "    import omnipkg._vendor.uv_ffi as m\n"
            "except: pass\n"
            "else:\n"
            "    d=os.path.dirname(m.__file__)\n"
            "    [print(os.path.join(d,f)) for f in os.listdir(d) if f.endswith(chr(46)+chr(115)+chr(111))]\n"
            "\" 2>/dev/null",
            target_python);
        FILE *_pp = popen(_py_cmd, "r");
        if (_pp) {
            if (fgets(_py_out, sizeof(_py_out), _pp)) {
                _py_out[strcspn(_py_out, "\r\n")] = '\0';
                if (_py_out[0] && file_exists(_py_out)) {
                    strncpy(so_path_out, _py_out, so_path_n - 1);
                    so_path_out[so_path_n - 1] = '\0';
                    pclose(_pp);
                    if (debug) fprintf(stderr, "[C-DISPATCH] Found uv_ffi.so via Python: %s\n", so_path_out);
                    return 1;
                }
            }
            pclose(_pp);
        }
    }

    /* ── 1. Search known locations for an existing .so ── */
    char patterns[8][MAX_PATH];
    const char *home = getenv("HOME");
    /* standard installed: uv_ffi/_native/ */
    snprintf(patterns[0], MAX_PATH,
        "%s/lib/python3.*/site-packages/uv_ffi/_native/*.so", venv_root);
    snprintf(patterns[1], MAX_PATH,
        "%s/lib/python3*/site-packages/uv_ffi/_native/*.so", venv_root);
    /* flat install: uv_ffi/*.so */
    snprintf(patterns[2], MAX_PATH,
        "%s/lib/python3.*/site-packages/uv_ffi/*.so", venv_root);
    snprintf(patterns[3], MAX_PATH,
        "%s/lib/python3*/site-packages/uv_ffi/*.so", venv_root);
    /* dev/vendor install: omnipkg/_vendor/uv_ffi/*.so */
    snprintf(patterns[4], MAX_PATH,
        "%s/lib/python3.*/site-packages/omnipkg/_vendor/uv_ffi/*.so", venv_root);
    snprintf(patterns[5], MAX_PATH,
        "%s/lib/python3*/site-packages/omnipkg/_vendor/uv_ffi/*.so", venv_root);
    /* user local */
    snprintf(patterns[6], MAX_PATH,
        "%s/.local/lib/python3.*/site-packages/uv_ffi/_native/*.so",
        home ? home : "/tmp");
    snprintf(patterns[7], MAX_PATH,
        "%s/.local/lib/python3.*/site-packages/uv_ffi/*.so",
        home ? home : "/tmp");

    glob_t globbuf;
    for (int pi = 0; pi < 8; pi++) {
        if (glob(patterns[pi], 0, NULL, &globbuf) == 0 && globbuf.gl_pathc > 0) {
            strncpy(so_path_out, globbuf.gl_pathv[0], so_path_n - 1);
            so_path_out[so_path_n - 1] = '\0';
            globfree(&globbuf);
            if (debug) fprintf(stderr, "[C-DISPATCH] Found uv_ffi.so: %s\n", so_path_out);
            return 1;
        }
        globfree(&globbuf);
    }

    if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi.so not found — attempting auto-install\n");

    /* ── 2. Not found: try to install uv_ffi from PyPI ── */
    char py_ver_tag[16] = "";
    const char *py_base = strrchr(target_python, '/');
    py_base = py_base ? py_base + 1 : target_python;
    if (strncmp(py_base, "python3.", 8) == 0) {
        const char *minor_start = py_base + 8; /* skip "python3." */
        size_t mlen = strlen(minor_start);
        if (mlen == 1)
            snprintf(py_ver_tag, sizeof(py_ver_tag), "cp3%c", minor_start[0]);
        else if (mlen >= 2)
            snprintf(py_ver_tag, sizeof(py_ver_tag), "cp3%c%c", minor_start[0], minor_start[1]);
    }

    if (!py_ver_tag[0]) {
        if (debug) fprintf(stderr, "[C-DISPATCH] Could not determine Python ABI tag\n");
        return 0;
    }

    if (debug) fprintf(stderr, "[C-DISPATCH] Auto-installing uv_ffi for %s\n", py_ver_tag);

    /* Find uv */
    char uv_path[MAX_PATH];
    const char *uv_exe = NULL;
    snprintf(uv_path, sizeof(uv_path), "%s/bin/uv", venv_root);
    if (file_exists(uv_path)) {
        uv_exe = uv_path;
    } else {
        const char *path_env = getenv("PATH");
        if (path_env) {
            char path_copy[16384];
            strncpy(path_copy, path_env, sizeof(path_copy) - 1);
            path_copy[sizeof(path_copy) - 1] = '\0';
            char *dir = strtok(path_copy, ":");
            static char uv_found[MAX_PATH];
            while (dir) {
                snprintf(uv_found, sizeof(uv_found), "%s/uv", dir);
                if (file_exists(uv_found)) { uv_exe = uv_found; break; }
                dir = strtok(NULL, ":");
            }
        }
    }

    char install_cmd[MAX_PATH * 4];
    int install_rc = -1;
    if (uv_exe) {
        snprintf(install_cmd, sizeof(install_cmd),
            "\"%s\" pip install --python \"%s\" uv_ffi",
            uv_exe, target_python);
        if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi install: %s\n", install_cmd);
        install_rc = system(install_cmd);
    }
    if (install_rc != 0) {
        snprintf(install_cmd, sizeof(install_cmd),
            "\"%s\" -m pip install uv_ffi", target_python);
        if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi pip fallback: %s\n", install_cmd);
        install_rc = system(install_cmd);
    }
    if (install_rc != 0) {
        if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi auto-install failed rc=%d\n", install_rc);
        return 0;
    }

    /* ── 3. Re-glob after install ── */
    for (int pi = 0; pi < 8; pi++) {
        if (glob(patterns[pi], 0, NULL, &globbuf) == 0 && globbuf.gl_pathc > 0) {
            strncpy(so_path_out, globbuf.gl_pathv[0], so_path_n - 1);
            so_path_out[so_path_n - 1] = '\0';
            globfree(&globbuf);
            if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi.so post-install: %s\n", so_path_out);
            return 1;
        }
        globfree(&globbuf);
    }

    if (debug) fprintf(stderr, "[C-DISPATCH] uv_ffi.so still missing after install\n");
    return 0;
}

/* ════════════════════════════════════════════════════════════════════════ */

int main(int argc, char **argv) {
    int debug = (getenv("OMNIPKG_DEBUG") != NULL &&
                 strcmp(getenv("OMNIPKG_DEBUG"), "1") == 0);

    /* Force-fallback escape hatch */
    if (getenv("OMNIPKG_FORCE_PYTHON_DISPATCH")) {
        char self_real[MAX_PATH];
        real_path(argv[0], self_real, sizeof(self_real));
        char self_dir[MAX_PATH];
        dir_of(self_real, self_dir, sizeof(self_dir));
        fallback_to_python(self_dir, argv);
    }

    /* ── 1. Resolve self ─────────────────────────────────────── */
    char self_real[MAX_PATH];
    real_path(argv[0], self_real, sizeof(self_real));
    char self_dir[MAX_PATH];
    dir_of(self_real, self_dir, sizeof(self_dir));

    char prog[256];
    base_of(argv[0], prog, sizeof(prog));
    /* lowercase */
    for (char *p = prog; *p; p++)
        if (*p >= 'A' && *p <= 'Z') *p += 32;

    if (debug)
        fprintf(stderr, "[C-DISPATCH] self=%s prog=%s\n", self_real, prog);

    /* ── 2. Shim mode? Fall back immediately ──────────────────── */
    if (strncmp(prog, "python", 6) == 0 || strcmp(prog, "pip") == 0) {
        if (debug) fprintf(stderr, "[C-DISPATCH] shim mode → python fallback\n");
        fallback_to_python(self_dir, argv);
    }

    /* ── 3. Swap-python edge case → python fallback ───────────── */
    /* detect: argv contains "swap" followed by "python" */
    int is_swap_python = 0;
    for (int i = 1; i < argc - 1; i++) {
        if (strcmp(argv[i], "swap") == 0 &&
            strncmp(argv[i+1], "python", 6) == 0) {
            is_swap_python = 1; break;
        }
    }
    if (is_swap_python && getenv("_OMNIPKG_SWAP_ACTIVE")) {
        if (debug) fprintf(stderr, "[C-DISPATCH] swap python in swap shell → python fallback\n");
        fallback_to_python(self_dir, argv);
    }

    /* ── 4. Detect version-specific command name ─────────────── */
    char forced_version[MAX_VERSION] = "";
    int  version_injected = 0;

    if (parse_version_suffix(prog, forced_version, sizeof(forced_version))) {
        /* inject --python <ver> if not already present */
        int has_python_flag = 0;
        for (int i = 1; i < argc; i++)
            if (strcmp(argv[i], "--python") == 0) { has_python_flag = 1; break; }

        if (!has_python_flag) {
            version_injected = 1;
            if (debug)
                fprintf(stderr, "[C-DISPATCH] version cmd %s → --python %s\n",
                        prog, forced_version);
        }
    }

    /* ── 5. Determine target Python ──────────────────────────── */
    char target_python[MAX_PATH] = "";

    /* 5a. --python flag (or injected from version command) */
    const char *cli_version = NULL;
    if (version_injected) {
        cli_version = forced_version;
    } else {
        for (int i = 1; i < argc - 1; i++) {
            if (strcmp(argv[i], "--python") == 0) {
                cli_version = argv[i + 1];
                break;
            }
        }
    }

    if (cli_version) {
        char venv_root[MAX_PATH];
        find_venv_root(self_real, venv_root, sizeof(venv_root));
        if (venv_root[0] && registry_lookup(venv_root, cli_version,
                                            target_python, sizeof(target_python))) {
            if (!file_exists(target_python)) {
                /* Not adopted yet → fallback to Python for auto-adopt */
                if (debug) fprintf(stderr, "[C-DISPATCH] %s not found → auto-adopt fallback\n", target_python);
                fallback_to_python(self_dir, argv);
            }
            if (debug)
                fprintf(stderr, "[C-DISPATCH] registry hit %s → %s\n",
                        cli_version, target_python);
        } else {
            /* Registry miss — could be the native interpreter (not adopted, so not
             * in registry.json).  Check if the self-aware config python matches the
             * requested version before giving up and paying the Python fallback cost. */
            char self_py[MAX_PATH] = "";
            read_self_config(self_dir, self_py, sizeof(self_py));
            if (self_py[0] && file_exists(self_py)) {
                /* Extract version from the path, e.g. ".../bin/python3.11" → "3.11" */
                const char *base = strrchr(self_py, '/');
                base = base ? base + 1 : self_py;
                /* skip "python" prefix */
                const char *ver_in_path = base;
                if (strncmp(ver_in_path, "python", 6) == 0) ver_in_path += 6;
                if (strncmp(ver_in_path, "3.", 2) == 0 &&
                    strcmp(ver_in_path, cli_version) == 0) {
                    /* Native interpreter matches — use it directly, no fallback needed */
                    strncpy(target_python, self_py, sizeof(target_python) - 1);
                    target_python[sizeof(target_python) - 1] = '\0';
                    if (debug)
                        fprintf(stderr, "[C-DISPATCH] native match %s → %s\n",
                                cli_version, target_python);
                } else {
                    /* Genuinely unknown version → Python fallback for auto-adopt */
                    if (debug) fprintf(stderr, "[C-DISPATCH] unknown version %s → fallback\n", cli_version);
                    fallback_to_python(self_dir, argv);
                }
            } else {
                if (debug) fprintf(stderr, "[C-DISPATCH] unknown version %s → fallback\n", cli_version);
                fallback_to_python(self_dir, argv);
            }
        }
    }

    /* 5b. Self-aware config (no swap active) */
    if (!target_python[0] && !getenv("_OMNIPKG_SWAP_ACTIVE")) {
        if (read_self_config(self_dir, target_python, sizeof(target_python))) {
            if (!file_exists(target_python)) target_python[0] = '\0';
            else if (debug)
                fprintf(stderr, "[C-DISPATCH] self-aware → %s\n", target_python);
        }
    }

    /* 5c. OMNIPKG_PYTHON inside swap shell */
    if (!target_python[0]) {
        const char *swap_ver = getenv("OMNIPKG_PYTHON");
        const char *swap_active = getenv("_OMNIPKG_SWAP_ACTIVE");
        if (swap_ver && swap_active && strcmp(swap_active, "1") == 0) {
            char venv_root[MAX_PATH];
            find_venv_root(self_real, venv_root, sizeof(venv_root));
            if (venv_root[0])
                registry_lookup(venv_root, swap_ver, target_python, sizeof(target_python));
        }
    }

    /* 5d. Fallback to sys.executable equivalent — host python in self_dir */
    if (!target_python[0]) {
        /* Try to get host python from self-config even in swap mode */
        if (!read_self_config(self_dir, target_python, sizeof(target_python)) ||
            !file_exists(target_python)) {
            /* Really can't figure it out — hand off to Python */
            fallback_to_python(self_dir, argv);
        }
    }

    /* ── 6. Native FFI Fast-Path (swap + install) ── */
    int is_swap = 0;
    const char *pkg_spec = NULL;

    if (argc >= 3 && strcmp(argv[1], "swap") == 0) {
        is_swap = 1; pkg_spec = argv[2];
    } else if (argc >= 4 && strcmp(argv[1], "pip") == 0 && strcmp(argv[2], "install") == 0) {
        is_swap = 1; pkg_spec = argv[3];
    } else if (argc >= 3 && strcmp(argv[1], "install") == 0) {
        /* direct install fast-path — only single pinned specs (name==ver) */
        const char *candidate = argv[2];
        if (candidate[0] != '-' && strstr(candidate, "==") != NULL) {
            is_swap = 1; pkg_spec = candidate;
        }
    }

    if (is_swap && pkg_spec && pkg_spec[0] != '-') {
        if (debug) fprintf(stderr, "[C-DISPATCH] Fast-path evaluation started. pkg_spec=%s\n", pkg_spec);

        char venv_root[MAX_PATH];
        find_venv_root(self_real, venv_root, sizeof(venv_root));

        char cfg_path[MAX_PATH];
        snprintf(cfg_path, sizeof(cfg_path), "%s/.omnipkg_config.json", self_dir);

        char strategy[128] = "latest-active";
        FILE *f = fopen(cfg_path, "r");
        if (f) {
            char buf[8192];
            size_t n = fread(buf, 1, sizeof(buf)-1, f);
            buf[n] = '\0';
            json_get_str(buf, "install_strategy", strategy, sizeof(strategy));
            fclose(f);
        }

        if (debug) fprintf(stderr, "[C-DISPATCH] Strategy detected: %s\n", strategy);

        if (1) { /* all strategies use FFI fast-path */
            char out_json[65536];
            out_json[0] = '\0';
            int ffi_rc = -1;

            /* ── Pre-flight: resolve daemon socket path (reused below) ── */
            char daemon_sock[MAX_PATH];
            daemon_sock[0] = '\0';
            {
                const char *ann = "/tmp/omnipkg/daemon_connection.txt";
                FILE *_af = fopen(ann, "r");
                if (_af) {
                    char _line[MAX_PATH];
                    if (fgets(_line, sizeof(_line), _af)) {
                        _line[strcspn(_line, "\r\n")] = '\0';
                        if (strncmp(_line, "unix://", 7) == 0) {
                            strncpy(daemon_sock, _line + 7, sizeof(daemon_sock) - 1);
                            daemon_sock[sizeof(daemon_sock) - 1] = '\0';
                        }
                    }
                    fclose(_af);
                }
                if (!daemon_sock[0]) {
                    const char *_td = getenv("TMPDIR");
                    if (!_td) _td = "/tmp";
                    snprintf(daemon_sock, sizeof(daemon_sock),
                             "%s/omnipkg/omnipkg_daemon.sock", _td);
                }
            }

            /* ── Pre-flight: ensure uv_ffi is installed for target python ──
             * If the target interpreter (e.g. cpython-3.9) is missing uv_ffi,
             * install it now and tell the daemon worker to reload its FFI handle.
             * This turns "via: subprocess_fallback" into "via: ffi" on next call.
             * We only do this when target_python differs from the main env python
             * (i.e. it's a managed interpreter, not the one omnipkg itself runs in). */
            {
                char main_py[MAX_PATH] = "";
                read_self_config(self_dir, main_py, sizeof(main_py));
                int is_foreign = (main_py[0] == '\0' ||
                                  strcmp(main_py, target_python) != 0);
                if (is_foreign) {
                    ensure_uv_ffi_for_python(target_python, venv_root,
                                             daemon_sock, debug);
                }
            }

            /* ── Phase 1a: try daemon UV worker (warm Tokio, ~5ms) ── */
            if (try_daemon_uv(target_python, pkg_spec, out_json, sizeof(out_json))) {
                ffi_rc = 0;
                if (debug) fprintf(stderr, "[C-DISPATCH] daemon UV fast-path succeeded\n");
            }

            /* ── Phase 1b: dlopen fallback (cold, ~11ms) ── */
            if (ffi_rc != 0) {
                char so_path[MAX_PATH];
                if (find_or_install_uv_ffi_so(venv_root, target_python,
                                               so_path, sizeof(so_path))) {
                    const char *py_ver_ptr = strstr(target_python, "python3.");
                    if (py_ver_ptr) {
                        char py_ver[32];
                        strncpy(py_ver, py_ver_ptr, sizeof(py_ver) - 1);
                        py_ver[sizeof(py_ver) - 1] = '\0';
                        char pylib[MAX_PATH];
                        snprintf(pylib, sizeof(pylib), "%s/lib/lib%s.so",
                                 venv_root, py_ver);
                        void *py_handle = dlopen(pylib, RTLD_LAZY | RTLD_GLOBAL);
                        if (!py_handle) {
                            snprintf(pylib, sizeof(pylib), "%s/lib/lib%s.so.1.0",
                                     venv_root, py_ver);
                            py_handle = dlopen(pylib, RTLD_LAZY | RTLD_GLOBAL);
                        }
                        if (debug)
                            fprintf(stderr, "[C-DISPATCH] Preloading %s -> %s\n",
                                    pylib, py_handle ? "OK" : dlerror());
                    }

                    void *ffi_handle = NULL;
                    /* Retry up to 3 times with 5ms gaps.
                     * On rapid back-to-back swaps the daemon worker may have the
                     * .so open in another dlopen() call — the file exists but the
                     * dynamic linker returns EBUSY / transient errors.  Three
                     * retries adds at most 10ms and eliminates the false-missing
                     * reports that triggered the expensive reinstall path. */
                    for (int _retry = 0; _retry < 3 && !ffi_handle; _retry++) {
                        ffi_handle = dlopen(so_path, RTLD_LAZY);
                        if (!ffi_handle && _retry < 2) {
                            if (debug) fprintf(stderr,
                                "[C-DISPATCH] dlopen attempt %d failed: %s — retrying\n",
                                _retry + 1, dlerror());
                            usleep(5000);  /* 5ms */
                        }
                    }
                    if (!ffi_handle) {
                        if (debug) fprintf(stderr, "[C-DISPATCH] dlopen failed: %s\n",
                                           dlerror());
                    } else {
                        typedef int (*run_c_t)(const char*, char*, int);
                        run_c_t run_fn = (run_c_t)dlsym(ffi_handle,
                                                         "omnipkg_uv_run_c");
                        if (run_fn) {
                            char cache_dir[MAX_PATH];
                            const char *home2 = getenv("HOME");
                            snprintf(cache_dir, sizeof(cache_dir), "%s/.cache/uv",
                                     home2 ? home2 : "/tmp");
                            char cmd[MAX_PATH * 2];
                            snprintf(cmd, sizeof(cmd),
                                "pip install --cache-dir %s --python %s"
                                " --link-mode symlink %s",
                                cache_dir, target_python, pkg_spec);
                            if (debug)
                                fprintf(stderr, "[C-DISPATCH] dlopen FFI: %s\n", cmd);
                            if (run_fn(cmd, out_json, sizeof(out_json)) == 0)
                                ffi_rc = 0;
                        } else {
                            if (debug) fprintf(stderr, "[C-DISPATCH] dlsym failed: %s\n",
                                               dlerror());
                        }
                    }
                }
            }

            /* ── Phase 2: on success, exit fast + fork background KB work ── */
            if (ffi_rc == 0) {
                if (debug) fprintf(stderr, "[C-DISPATCH] FFI returned 0. Changelog: %s\n", out_json);

                /* Always return terminal to user immediately */
                printf("🎉 Package operations complete.\n");
                fflush(stdout);

                /* ── Extract installed/removed arrays from out_json ──────────
                 * Used for BOTH the cache patch message and the kb_sentinel.
                 * The changelog includes ALL packages uv touched (e.g. setuptools
                 * that got co-upgraded), not just the originally requested pkg. */
                char inst_buf[4096] = "[]";
                char rem_buf[4096]  = "[]";
                {
                    const char *inst_start = strstr(out_json, "\"installed\":");
                    const char *rem_start  = strstr(out_json, "\"removed\":");
                    if (inst_start && rem_start) {
                        const char *inst_arr = strchr(inst_start, '[');
                        const char *rem_arr  = strchr(rem_start,  '[');
                        if (inst_arr) {
                            int depth = 0; size_t i = 0;
                            for (const char *p = inst_arr; *p && i < sizeof(inst_buf)-1; p++) {
                                inst_buf[i++] = *p;
                                if (*p == '[') depth++;
                                else if (*p == ']' && --depth == 0) break;
                            }
                            inst_buf[i] = '\0';
                        }
                        if (rem_arr) {
                            int depth = 0; size_t i = 0;
                            for (const char *p = rem_arr; *p && i < sizeof(rem_buf)-1; p++) {
                                rem_buf[i++] = *p;
                                if (*p == '[') depth++;
                                else if (*p == ']' && --depth == 0) break;
                            }
                            rem_buf[i] = '\0';
                        }
                    }
                }

                /* ── Push cache patch + KB sentinel to daemon in ONE connection ──
                 *
                 * CRITICAL: send patch_site_packages_cache FIRST so every UV worker's
                 * in-memory Rust cache is updated before the next run_uv call arrives.
                 * Without this, the fs_watcher would have to notice the disk change
                 * (~150ms debounce) before the next FFI call sees correct state.
                 *
                 * This replaces the old "wait for fs_watcher" path with an instant push
                 * for ALL packages that changed (transitive deps included), then the
                 * kb_sentinel for Redis/KB bookkeeping.
                 */
                if (daemon_sock[0]) {
                    int _ds = socket(AF_UNIX, SOCK_STREAM, 0);
                    if (_ds >= 0) {
                        struct timeval _tv = {1, 0};
                        setsockopt(_ds, SOL_SOCKET, SO_SNDTIMEO, &_tv, sizeof(_tv));
                        struct sockaddr_un _da;
                        memset(&_da, 0, sizeof(_da));
                        _da.sun_family = AF_UNIX;
                        strncpy(_da.sun_path, daemon_sock, sizeof(_da.sun_path)-1);
                        if (connect(_ds, (struct sockaddr*)&_da, sizeof(_da)) == 0) {
                            /* Message 1: patch cache (UV workers update Rust SITE_PACKAGES_CACHE) */
                            char patch_msg[MAX_JSON];
                            int patch_len = snprintf(patch_msg, sizeof(patch_msg),
                                "{\"type\":\"patch_site_packages_cache\","
                                "\"site_packages_path\":\"\","
                                "\"installed\":%s,\"removed\":%s}",
                                inst_buf, rem_buf);
                            if (patch_len > 0) {
                                uint64_t _plen = (uint64_t)patch_len;
                                uint8_t _phdr[8];
                                for (int _i = 0; _i < 8; _i++)
                                    _phdr[7-_i] = (_plen >> (_i*8)) & 0xFF;
                                write(_ds, _phdr, 8);
                                write(_ds, patch_msg, _plen);
                            }
                            /* Message 2: kb_sentinel (Redis/KB bookkeeping) */
                            char kb_msg[MAX_JSON];
                            int kb_len = snprintf(kb_msg, sizeof(kb_msg),
                                "{\"type\":\"kb_sentinel\",\"installed\":%s,\"removed\":%s}",
                                inst_buf, rem_buf);
                            if (kb_len > 0) {
                                uint64_t _klen = (uint64_t)kb_len;
                                uint8_t _khdr[8];
                                for (int _i = 0; _i < 8; _i++)
                                    _khdr[7-_i] = (_klen >> (_i*8)) & 0xFF;
                                write(_ds, _khdr, 8);
                                write(_ds, kb_msg, _klen);
                            }
                        }
                        close(_ds);
                    }
                }

                /* Fork background Python only if something actually changed */
                int has_changes = (strstr(out_json, "[\"") != NULL);
                if (has_changes) {
                    pid_t pid = fork();
                    if (pid > 0) {
                        /* parent — exit immediately, terminal is free */
                        exit(0);
                    } else if (pid == 0) {
                        /* child — run KB/bubble work silently */
                        setsid();
                        int devnull = open("/dev/null", O_WRONLY);
                        if (devnull >= 0) {
                            dup2(devnull, 1);
                            dup2(devnull, 2);
                            close(devnull);
                        }

                        /* Extract "3.11" safely into a fixed buffer */
                        char py_ctx_ver[16] = "3.11";
                        const char *_pv = strstr(target_python, "python3.");
                        if (_pv) {
                            _pv += 7; /* skip "python" — points at "3.11" etc */
                            size_t _vi = 0;
                            while (_pv[_vi] &&
                                   (_pv[_vi] == '.' ||
                                    (_pv[_vi] >= '0' && _pv[_vi] <= '9')) &&
                                   _vi < sizeof(py_ctx_ver) - 1) {
                                py_ctx_ver[_vi] = _pv[_vi];
                                _vi++;
                            }
                            py_ctx_ver[_vi] = '\0';
                        }

                        char py_script[8192];
                        snprintf(py_script, sizeof(py_script),
                            "import json, os, sys\n"
                            "from omnipkg.core import ConfigManager, omnipkg\n"
                            "from omnipkg.installation.smart_install import SmartInstaller\n"
                            "core = omnipkg(ConfigManager())\n"
                            "core._connect_cache()\n"
                            "try:\n"
                            "    cl = json.loads('''%s''')\n"
                            "    inst = cl.get('installed', [])\n"
                            "    rem  = cl.get('removed',   [])\n"
                            "    bg = {\n"
                            "        'force_reinstall': False,\n"
                            "        'protected_from_cleanup': [],\n"
                            "        'initial_packages': {},\n"
                            "        'final_main_state':    {n: v for n, v in inst},\n"
                            "        'main_env_kb_updates': {n: v for n, v in inst},\n"
                            "        'bubbled_kb_updates':  {n: v for n, v in rem},\n"
                            "        'python_context_version': '%s',\n"
                            "        'priority_specs': [f'{n}=={v}' for n, v in inst],\n"
                            "        'bubble_paths_to_scan': {},\n"
                            "        'pending_bubble_tasks': [\n"
                            "            {'type': 'create_isolated_bubble',\n"
                            "             'pkg_name': n, 'version': v,\n"
                            "             'python_context_version': '%s'}\n"
                            "            for n, v in rem\n"
                            "        ],\n"
                            "        'run_doctor': False,\n"
                            "    }\n"
                            "    SmartInstaller(core)._run_background(bg, core)\n"
                            "except Exception:\n"
                            "    pass\n",
                            out_json,
                            py_ctx_ver,
                            py_ctx_ver
                        );

                        char *bg_argv[] = {
                            (char *)target_python,
                            (char *)"-c",
                            py_script,
                            NULL
                        };
                        execv(target_python, bg_argv);
                        exit(0);
                    }
                }
                /* No changes (already satisfied) or fork parent — exit cleanly */
                exit(0);
            }
        }
    }

    /* ── 7. Build final argv and execv (or try daemon) ───────────────────────── */
    int is_interactive_command = 0;
    if (argc >= 2) {
        is_interactive_command = (
            strcmp(argv[1], "info")   == 0 ||
            strcmp(argv[1], "config") == 0
        );
    }
    if (!is_swap_python && !is_interactive_command) {
        try_daemon_cli(target_python, argc, argv, version_injected, forced_version);
        /* try_daemon_cli calls exit() on success. Reaching here means it failed. */
        if (debug) fprintf(stderr, "[C-DISPATCH] daemon fast-path failed — falling back to execv\n");
    }

    /*
     * target_python -m omnipkg.cli[--python X] [original args]
     */
    int extra = version_injected ? 2 : 0;   /* "--python", "3.X" */
    char **new_argv = malloc((argc + 4 + extra) * sizeof(char *));
    int idx = 0;

    new_argv[idx++] = target_python;
    new_argv[idx++] = "-m";
    new_argv[idx++] = "omnipkg.cli";

    /* copy original args starting at 1 (skip argv[0]) */
    if (version_injected) {
        new_argv[idx++] = "--python";
        new_argv[idx++] = forced_version;
    }

    for (int i = 1; i < argc; i++)
        new_argv[idx++] = argv[i];
    new_argv[idx] = NULL;

    if (debug) {
        fprintf(stderr, "[C-DISPATCH] execv: %s", target_python);
        for (int i = 1; new_argv[i]; i++)
            fprintf(stderr, " %s", new_argv[i]);
        fprintf(stderr, "\n");
    }

    putenv("_OMNIPKG_ISATTY=1");
    execv(target_python, new_argv);

    /* execv only returns on error */
    perror("omnipkg: execv failed");
    return 1;
}
