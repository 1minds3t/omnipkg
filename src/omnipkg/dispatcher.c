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

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <libgen.h>
#include <limits.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <stdint.h>
#include <errno.h>

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

/* ── fallback to Python dispatcher ─────────────────────────────────────── */

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
        /* recv loop broke unexpectedly — also nuke */
    }
    remove(ann_path);
    if (debug) fprintf(stderr, "[C-DISPATCH] daemon unhealthy — falling back to execv\n");
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

    /* ── 6. Build final argv and execv ───────────────────────── */

    /* Skip the daemon for `swap python` and interactive commands.
     * Interactive commands need a real TTY for user input — the NEEDS_INPUT
     * relay through the daemon is unreliable. execv path handles these fine. */
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
     * If version was injected from command name, we must insert --python X
     * into the new argv (since original argv doesn't have it).
     */
    int extra = version_injected ? 2 : 0;   /* "--python", "3.X" */
    char **new_argv = malloc((argc + 4 + extra) * sizeof(char *));
    int idx = 0;

    new_argv[idx++] = target_python;
    new_argv[idx++] = "-m";
    new_argv[idx++] = "omnipkg.cli";

    /* copy original args starting at 1 (skip argv[0]) */
    /* but if version_injected, insert --python before them */
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