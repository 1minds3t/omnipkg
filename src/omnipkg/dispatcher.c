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
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <winsock2.h>
#  include <windows.h>
#  include <direct.h>
#  include <io.h>
#  include <process.h>
#  include <sys/stat.h>
#  include <stdio.h>   /* snprintf — must precede compat functions on MinGW */

/* ── Windows compat macros ── */
#  undef MAX_PATH
#  define MAX_PATH 4096
#  define realpath(path, resolved) _fullpath(resolved, path, MAX_PATH)
#  define isatty(fd)   _isatty(fd)
#  define getcwd(buf, n) _getcwd(buf, n)
/* Winsock: map POSIX socket close → closesocket (file descriptors use _close separately) */
#  define sock_close(s) closesocket(s)
/* Windows Sleep() is milliseconds; usleep() is microseconds */
#  define usleep(us)   Sleep((DWORD)((us) / 1000 ? (us) / 1000 : 1))
/* ssize_t: MinGW (corecrt.h) already typedefs this as __int64; MSVC does not.
 * Only define our own when the MinGW sentinel macros are absent. */
#  if !defined(_SSIZE_T_DEFINED) && !defined(_SSIZE_T_)
typedef int ssize_t;
#  endif
/* pid_t: MinGW (sys/types.h via process.h) already defines this.
 * Only define our placeholder when neither MinGW sentinel is present. */
#  if !defined(_PID_T_) && !defined(__pid_t_defined)
typedef int pid_t;
#  endif
/* PATH_MAX for popen redirect suppression */
#  define PATH_SEPARATOR ";"
/* Windows read/write on sockets must use recv/send */
static ssize_t win_sock_read(int s, void *buf, size_t n) {
    return recv(s, (char*)buf, (int)n, 0);
}
static ssize_t win_sock_write(int s, const void *buf, size_t n) {
    return send(s, (const char*)buf, (int)n, 0);
}
#  define sock_read(s,b,n)  win_sock_read(s,b,n)
#  define sock_write(s,b,n) win_sock_write(s,b,n)

/* Windows mkdir takes 1 arg (no mode) */
static int win_mkdir(const char *path) { return _mkdir(path); }
#  define mkdir_compat(path, mode) win_mkdir(path)

/* execv → _spawnv(_P_WAIT) then exit with that code */
static int win_execv(const char *path, char *const argv[]) {
    intptr_t r = _spawnv(_P_WAIT, path, (const char *const *)argv);
    if (r == -1) return -1;
    exit((int)r);
}
#  define execv(p, a) win_execv(p, a)

/* dirname / basename for Windows paths */
static char *omnipkg_dirname(char *path) {
    static char buf[MAX_PATH];
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
#  define dirname(p) omnipkg_dirname(p)
#  define basename(p) omnipkg_basename(p)

/* dlopen / dlsym / dlerror → LoadLibrary / GetProcAddress */
static void *win_dlopen(const char *path) {
    return (void *)LoadLibraryA(path);
}
static void *win_dlsym(void *handle, const char *sym) {
    return (void *)GetProcAddress((HMODULE)handle, sym);
}
static const char *win_dlerror(void) {
    static char buf[256];
    DWORD err = GetLastError();
    if (!FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
                        NULL, err, 0, buf, sizeof(buf)-1, NULL))
        snprintf(buf, sizeof(buf), "error %lu", (unsigned long)err);
    return buf;
}
#  define dlopen(path, flags) win_dlopen(path)
#  define dlsym(h, sym)       win_dlsym(h, sym)
#  define dlerror()           win_dlerror()

/* glob() replacement using FindFirstFile — returns 0 on match, non-0 on miss.
 * Only handles a single trailing wildcard (e.g. "dir\*.so" or "dir\*.pyd").
 * For omnipkg's use-case (finding .so / .pyd files in a known dir) this is
 * sufficient.  gl_pathv[0] points at a static buffer. */
typedef struct {
    size_t  gl_pathc;
    char  **gl_pathv;
    char   *_internal_path;  /* storage for the one result we care about */
} glob_t;
static int win_glob(const char *pattern, int flags, void *errfunc, glob_t *pglob) {
    (void)flags; (void)errfunc;
    pglob->gl_pathc = 0;
    pglob->gl_pathv = NULL;
    pglob->_internal_path = NULL;

    /* Split pattern into directory and wildcard filename */
    char dir[MAX_PATH], pat[MAX_PATH];
    const char *last_sep = NULL;
    for (const char *p = pattern; *p; p++)
        if (*p == '/' || *p == '\\') last_sep = p;
    if (!last_sep) {
        strncpy(dir, ".", sizeof(dir)-1); dir[sizeof(dir)-1] = '\0';
        strncpy(pat, pattern, sizeof(pat)-1); pat[sizeof(pat)-1] = '\0';
    } else {
        size_t dlen = last_sep - pattern;
        if (dlen >= sizeof(dir)) dlen = sizeof(dir)-1;
        strncpy(dir, pattern, dlen); dir[dlen] = '\0';
        strncpy(pat, last_sep + 1, sizeof(pat)-1); pat[sizeof(pat)-1] = '\0';
    }

    /* Build search path = dir\wildcard */
    char search[MAX_PATH];
    snprintf(search, sizeof(search), "%s\\%s", dir, pat);

    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(search, &fd);
    if (h == INVALID_HANDLE_VALUE) return 1; /* no match */

    /* Store the first match */
    char full[MAX_PATH];
    snprintf(full, sizeof(full), "%s\\%s", dir, fd.cFileName);
    FindClose(h);

    pglob->_internal_path = _strdup(full);
    if (!pglob->_internal_path) return 1;
    pglob->gl_pathv = &pglob->_internal_path;
    pglob->gl_pathc = 1;
    return 0;
}
static void win_globfree(glob_t *pglob) {
    if (pglob->_internal_path) { free(pglob->_internal_path); pglob->_internal_path = NULL; }
    pglob->gl_pathc = 0;
    pglob->gl_pathv = NULL;
}
#  define glob(pat, flags, err, pg)  win_glob(pat, flags, err, pg)
#  define globfree(pg)               win_globfree(pg)

/* Winsock startup helper — called once at program start */
static void winsock_init(void) {
    WSADATA wsa;
    WSAStartup(MAKEWORD(2,2), &wsa);
}

#else /* ── POSIX ── */
#  include <unistd.h>
#  include <sys/stat.h>
#  include <sys/time.h>
#  include <libgen.h>
#  include <limits.h>
#  include <sys/socket.h>
#  include <sys/un.h>
#  include <dlfcn.h>
#  include <fcntl.h>
#  include <glob.h>
#  define PATH_SEPARATOR ":"
#  define sock_read(s,b,n)   read(s,b,n)
#  define sock_write(s,b,n)  write(s,b,n)
#  define sock_close(s)      close(s)
#  define mkdir_compat(path, mode) mkdir(path, mode)
#  define winsock_init() /* nothing */
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define MAX_PATH 4096
#define MAX_VERSION 32
#define MAX_JSON 65536   /* registry.json is tiny */

/* ── tiny helpers ──────────────────────────────────────────────────────── */


/* ── minimal MD5 — RFC 1321, no external deps ───────────────────────────── */
typedef struct {
    uint32_t state[4];
    uint32_t count[2];
    unsigned char buf[64];
} md5_ctx;

static const uint32_t md5_T[64] = {
    0xd76aa478,0xe8c7b756,0x242070db,0xc1bdceee,0xf57c0faf,0x4787c62a,
    0xa8304613,0xfd469501,0x698098d8,0x8b44f7af,0xffff5bb1,0x895cd7be,
    0x6b901122,0xfd987193,0xa679438e,0x49b40821,0xf61e2562,0xc040b340,
    0x265e5a51,0xe9b6c7aa,0xd62f105d,0x02441453,0xd8a1e681,0xe7d3fbc8,
    0x21e1cde6,0xc33707d6,0xf4d50d87,0x455a14ed,0xa9e3e905,0xfcefa3f8,
    0x676f02d9,0x8d2a4c8a,0xfffa3942,0x8771f681,0x6d9d6122,0xfde5380c,
    0xa4beea44,0x4bdecfa9,0xf6bb4b60,0xbebfbc70,0x289b7ec6,0xeaa127fa,
    0xd4ef3085,0x04881d05,0xd9d4d039,0xe6db99e5,0x1fa27cf8,0xc4ac5665,
    0xf4292244,0x432aff97,0xab9423a7,0xfc93a039,0x655b59c3,0x8f0ccc92,
    0xffeff47d,0x85845dd1,0x6fa87e4f,0xfe2ce6e0,0xa3014314,0x4e0811a1,
    0xf7537e82,0xbd3af235,0x2ad7d2bb,0xeb86d391
};
static const int md5_S[64] = {
    7,12,17,22,7,12,17,22,7,12,17,22,7,12,17,22,
    5, 9,14,20,5, 9,14,20,5, 9,14,20,5, 9,14,20,
    4,11,16,23,4,11,16,23,4,11,16,23,4,11,16,23,
    6,10,15,21,6,10,15,21,6,10,15,21,6,10,15,21
};
#define MD5_ROL(x,n) (((x)<<(n))|((x)>>(32-(n))))
static void md5_transform(uint32_t s[4], const unsigned char *blk) {
    uint32_t a=s[0],b=s[1],c=s[2],d=s[3],x[16],f,g,tmp;
    int i;
    for(i=0;i<16;i++) {
        x[i]=(uint32_t)blk[i*4]|((uint32_t)blk[i*4+1]<<8)|
              ((uint32_t)blk[i*4+2]<<16)|((uint32_t)blk[i*4+3]<<24);
    }
    for(i=0;i<64;i++){
        if(i<16){f=(b&c)|(~b&d);g=i;}
        else if(i<32){f=(d&b)|(~d&c);g=(5*i+1)%16;}
        else if(i<48){f=b^c^d;g=(3*i+5)%16;}
        else{f=c^(b|~d);g=(7*i)%16;}
        tmp=d;d=c;c=b;
        b=b+MD5_ROL(a+f+x[g]+md5_T[i],md5_S[i]);
        a=tmp;
    }
    s[0]+=a;s[1]+=b;s[2]+=c;s[3]+=d;
}
static void md5_init(md5_ctx *ctx) {
    ctx->state[0]=0x67452301;ctx->state[1]=0xefcdab89;
    ctx->state[2]=0x98badcfe;ctx->state[3]=0x10325476;
    ctx->count[0]=ctx->count[1]=0;
}
static void md5_update(md5_ctx *ctx, const unsigned char *data, size_t len) {
    size_t i,idx=(ctx->count[0]>>3)&0x3f;
    ctx->count[0]+=(uint32_t)(len<<3);
    if(ctx->count[0]<(uint32_t)(len<<3)) ctx->count[1]++;
    ctx->count[1]+=(uint32_t)(len>>29);
    size_t part=64-idx;
    if(len>=part){
        memcpy(&ctx->buf[idx],data,part);
        md5_transform(ctx->state,ctx->buf);
        for(i=part;i+63<len;i+=64) md5_transform(ctx->state,data+i);
        idx=0;
    } else { i=0; }
    memcpy(&ctx->buf[idx],data+i,len-i);
}
static void md5_final(md5_ctx *ctx, unsigned char digest[16]) {
    unsigned char pad[64]={0x80};
    unsigned char bits[8];
    int i;
    for(i=0;i<4;i++){
        bits[i]=(unsigned char)(ctx->count[0]>>(i*8));
        bits[i+4]=(unsigned char)(ctx->count[1]>>(i*8));
    }
    size_t idx=(ctx->count[0]>>3)&0x3f;
    size_t padlen=(idx<56)?56-idx:120-idx;
    md5_update(ctx,pad,padlen);
    md5_update(ctx,bits,8);
    for(i=0;i<4;i++){
        digest[i*4]=(unsigned char)(ctx->state[i]);
        digest[i*4+1]=(unsigned char)(ctx->state[i]>>8);
        digest[i*4+2]=(unsigned char)(ctx->state[i]>>16);
        digest[i*4+3]=(unsigned char)(ctx->state[i]>>24);
    }
}

/* md5_file: compute hex MD5 of a file into out (33 bytes incl NUL).
 * Returns 1 on success, 0 on error. */
static int md5_file(const char *path, char out[33]) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    md5_ctx ctx; md5_init(&ctx);
    unsigned char buf[8192]; size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
        md5_update(&ctx, buf, n);
    fclose(f);
    unsigned char digest[16]; md5_final(&ctx, digest);
    for (int i = 0; i < 16; i++)
        snprintf(out + i*2, 3, "%02x", digest[i]);
    out[32] = '\0';
    return 1;
}

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
            char *dir = strtok(path_copy, PATH_SEPARATOR);
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
                dir = strtok(NULL, PATH_SEPARATOR);
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

/* Forward declaration — defined later in stamp-file helpers section */
static const char *get_tmp_dir(void);

/*
 * daemon_connect — platform-transparent daemon connection.
 *
 * Reads daemon_connection.txt from the platform temp dir.
 *   Unix:    "unix:///path/to/socket"  → AF_UNIX connect
 *   Windows: "tcp://127.0.0.1:PORT"   → AF_INET TCP connect
 *
 * Falls back to Unix socket at $TMPDIR/omnipkg/omnipkg_daemon.sock on Unix
 * and TCP 127.0.0.1:5678 on Windows if the file is missing.
 *
 * Returns a connected socket fd, or -1 on failure.
 * Caller is responsible for sock_close().
 *
 * Writes the announcement path used into ann_path_out (may be NULL).
 */
static int daemon_connect(int debug, char *ann_path_out, size_t ann_path_n) {
    char ann_path[MAX_PATH];
    snprintf(ann_path, sizeof(ann_path), "%s/omnipkg/daemon_connection.txt",
             get_tmp_dir());
    if (ann_path_out)
        snprintf(ann_path_out, ann_path_n, "%s", ann_path);

    char conn_str[MAX_PATH] = "";
    FILE *af = fopen(ann_path, "r");
    if (af) {
        if (fgets(conn_str, sizeof(conn_str), af))
            conn_str[strcspn(conn_str, "\r\n")] = '\0';
        fclose(af);
    }

    if (debug) fprintf(stderr, "[C-DISPATCH] daemon_connect: conn_str='%s'\n", conn_str);

#ifndef _WIN32
    /* ── Unix: AF_UNIX ── */
    char sock_path[MAX_PATH];
    if (strncmp(conn_str, "unix://", 7) == 0) {
        strncpy(sock_path, conn_str + 7, sizeof(sock_path) - 1);
        sock_path[sizeof(sock_path) - 1] = '\0';
    } else {
        /* fallback */
        const char *td = getenv("TMPDIR");
        if (!td) td = "/tmp";
        snprintf(sock_path, sizeof(sock_path), "%s/omnipkg/omnipkg_daemon.sock", td);
    }

    struct stat st;
    if (stat(sock_path, &st) != 0) {
        if (debug) fprintf(stderr, "[C-DISPATCH] daemon socket not found: %s\n", sock_path);
        return -1;
    }

    int sock = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sock < 0) return -1;

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, sock_path, sizeof(addr.sun_path) - 1);

    if (connect(sock, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        if (debug) fprintf(stderr, "[C-DISPATCH] AF_UNIX connect failed: %s\n", strerror(errno));
        sock_close(sock);
        return -1;
    }
    if (debug) fprintf(stderr, "[C-DISPATCH] connected via AF_UNIX: %s\n", sock_path);
    return sock;

#else
    /* ── Windows: AF_INET TCP ── */
    char host[256] = "127.0.0.1";
    int  port = 5678;

    if (strncmp(conn_str, "tcp://", 6) == 0) {
        char *colon = strrchr(conn_str + 6, ':');
        if (colon) {
            size_t hlen = (size_t)(colon - (conn_str + 6));
            if (hlen < sizeof(host)) {
                strncpy(host, conn_str + 6, hlen);
                host[hlen] = '\0';
            }
            port = atoi(colon + 1);
        }
    }

    if (debug) fprintf(stderr, "[C-DISPATCH] connecting TCP %s:%d\n", host, port);

    SOCKET sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock == INVALID_SOCKET) return -1;

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port   = htons((unsigned short)port);
    addr.sin_addr.s_addr = inet_addr(host);

    if (connect(sock, (struct sockaddr*)&addr, sizeof(addr)) != 0) {
        if (debug) fprintf(stderr, "[C-DISPATCH] TCP connect failed: %lu\n",
                           (unsigned long)WSAGetLastError());
        closesocket(sock);
        return -1;
    }
    if (debug) fprintf(stderr, "[C-DISPATCH] connected via TCP %s:%d\n", host, port);
    return (int)sock;
#endif
}

/* Set send+recv timeouts on a connected daemon socket. */
static void daemon_set_timeouts(int sock, int send_sec, int recv_sec) {
#ifdef _WIN32
    DWORD snd = (DWORD)(send_sec * 1000);
    DWORD rcv = (DWORD)(recv_sec * 1000);
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, (const char*)&snd, sizeof(snd));
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, (const char*)&rcv, sizeof(rcv));
#else
    struct timeval tv_snd = { send_sec, 0 };
    struct timeval tv_rcv = { recv_sec, 0 };
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, (const char*)&tv_snd, sizeof(tv_snd));
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv_rcv, sizeof(tv_rcv));
#endif
}

static int write_all(int fd, const void *buf, size_t count) {
    const char *p = (const char*)buf;
    while (count > 0) {
        ssize_t r = sock_write(fd, p, count);
        if (r <= 0) return 0;
        p += r;
        count -= (size_t)r;
    }
    return 1;
}

static int send_json_msg(int sock, const char *json_str) {
    uint64_t len = (uint64_t)strlen(json_str);
    uint8_t be_len[8];
    for (int i = 0; i < 8; i++)
        be_len[7 - i] = (uint8_t)((len >> (i * 8)) & 0xFF);
    if (!write_all(sock, be_len, 8)) return 0;
    if (!write_all(sock, json_str, (size_t)len)) return 0;
    return 1;
}

static char* recv_json_msg(int sock) {
    uint8_t be_len[8];
    int n = 0;
    while (n < 8) {
        int r = (int)sock_read(sock, be_len + n, (size_t)(8 - n));
        if (r <= 0) return NULL;
        n += r;
    }
    uint64_t len = 0;
    for (int i = 0; i < 8; i++) {
        len = (len << 8) | be_len[i];
    }
    if (len > 1024 * 1024 * 10) return NULL;
    char *buf = (char*)malloc((size_t)(len + 1));
    if (!buf) return NULL;
    uint64_t total = 0;
    while (total < len) {
        int r = (int)sock_read(sock, buf + total, (size_t)(len - total));
        if (r <= 0) { free(buf); return NULL; }
        total += (uint64_t)r;
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
 *
 * Uses daemon_connect() which handles tcp:// (Windows) and unix:// (POSIX).
 */
static int try_daemon_uv(
    const char *target_python,
    const char *pkg_spec,
    char       *out_json,
    int         max_out
) {
    /*
     * DISABLED: fast-path uv FFI bypass is not yet safe.
     * Re-enable when: split resolve/plan, concurrent bubble cloak,
     * KB sync hidden under latency, Tokio runtime stable for rapid calls,
     * sentinel written only after ALL background ops complete.
     */
    (void)target_python; (void)pkg_spec; (void)out_json; (void)max_out;
    return 0;

    int debug = (getenv("OMNIPKG_DEBUG") != NULL &&
                 strcmp(getenv("OMNIPKG_DEBUG"), "1") == 0);

    int sock = daemon_connect(debug, NULL, 0);
    if (sock < 0) return 0;
    daemon_set_timeouts(sock, 5, 5);

    char cache_dir[MAX_PATH];
    const char *home = getenv("HOME");
#ifdef _WIN32
    if (!home) home = getenv("USERPROFILE");
    if (!home) home = getenv("LOCALAPPDATA");
#endif
    snprintf(cache_dir, sizeof(cache_dir), "%s/.cache/uv", home ? home : get_tmp_dir());

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

    if (rlen <= 0 || rlen >= (int)sizeof(req)) { sock_close(sock); return 0; }
    if (debug) fprintf(stderr, "[C-DISPATCH] try_daemon_uv: sending run_uv for %s\n", pkg_spec);
    if (!send_json_msg(sock, req)) { sock_close(sock); return 0; }

    while (1) {
        char *msg = recv_json_msg(sock);
        if (!msg) break;
        char *status_start;
        if (json_get_raw_str(msg, "status", &status_start)) {
            if (strncmp(status_start, "COMPLETED", 9) == 0) {
                int exit_code = 0;
                json_get_int(msg, "exit_code", &exit_code);
                if (exit_code == 0) {
                    strncpy(out_json, msg, max_out - 1);
                    out_json[max_out - 1] = '\0';
                    free(msg); sock_close(sock);
                    if (debug) fprintf(stderr, "[C-DISPATCH] try_daemon_uv: success\n");
                    return 1;
                }
                free(msg); sock_close(sock);
                return 0;
            } else if (strncmp(status_start, "FFI_UNAVAILABLE", 15) == 0 ||
                       strncmp(status_start, "ERROR", 5) == 0) {
                free(msg); sock_close(sock);
                return 0;
            }
        }
        free(msg);
    }
    sock_close(sock);
    return 0;
}

static int try_daemon_cli(const char *target_python, int argc, char **argv,
                          int version_injected, const char *forced_version) {
    int debug = (getenv("OMNIPKG_DEBUG") != NULL &&
                 strcmp(getenv("OMNIPKG_DEBUG"), "1") == 0);

    char ann_path[MAX_PATH];
    int sock = daemon_connect(debug, ann_path, sizeof(ann_path));
    if (sock < 0) {
        if (debug) fprintf(stderr, "[C-DISPATCH] daemon not available — skipping\n");
        return 0;
    }

    /* Short send, generous recv (300s covers slow human at NEEDS_INPUT prompt) */
    daemon_set_timeouts(sock, 2, 300);

    if (debug) fprintf(stderr, "[C-DISPATCH] daemon connected — sending run_cli\n");

    char cwd[MAX_PATH];
    if (!getcwd(cwd, sizeof(cwd))) cwd[0] = '\0';

    char *req = malloc(1024 * 1024);
    if (!req) { close(sock); return 0; }

    int len = sprintf(req, "{\"type\":\"run_cli\",\"isatty\":%s",
                      isatty(1) ? "true" : "false");
    if (target_python && target_python[0])
        len += sprintf(req + len, ",\"python_exe\":\"%s\"", target_python);
    if (cwd[0])
        len += sprintf(req + len, ",\"cwd\":\"%s\"", cwd);

    len += sprintf(req + len, ",\"argv\":[\"8pkg\"");
    /* Do NOT forward --python to the daemon worker — python_exe already selects
     * the right pool; passing --python causes the worker to execve and die. */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--python") == 0) { i++; continue; }
        req[len++] = ','; req[len++] = '"';
        for (char *p = argv[i]; *p; p++) {
            if (*p == '"' || *p == '\\') req[len++] = '\\';
            req[len++] = *p;
        }
        req[len++] = '"';
    }
    req[len++] = ']'; req[len++] = '}'; req[len] = '\0';

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
                free(msg); sock_close(sock);
                exit(exit_code);
            } else if (strncmp(stream_type, "NEEDS_INPUT", 11) == 0) {
                char *prompt_start;
                if (json_get_raw_str(msg, "prompt", &prompt_start))
                    print_unescaped(prompt_start, stdout);
                free(msg);

                char input_buf[4096];
                size_t ilen = 0;
                input_buf[0] = '\0';
                if (fgets(input_buf, sizeof(input_buf), stdin)) {
                    ilen = strlen(input_buf);
                    if (ilen > 0 && input_buf[ilen-1] == '\n')
                        input_buf[--ilen] = '\0';
                }

                char *reply = malloc(ilen * 6 + 64);
                if (!reply) { sock_close(sock); return 0; }
                int rlen = sprintf(reply, "{\"type\":\"stdin_line\",\"data\":\"");
                for (size_t k = 0; k < ilen; k++) {
                    unsigned char c = (unsigned char)input_buf[k];
                    if      (c == '"')  { reply[rlen++]='\\'; reply[rlen++]='"'; }
                    else if (c == '\\') { reply[rlen++]='\\'; reply[rlen++]='\\'; }
                    else if (c == '\n') { reply[rlen++]='\\'; reply[rlen++]='n'; }
                    else if (c == '\r') { reply[rlen++]='\\'; reply[rlen++]='r'; }
                    else if (c == '\t') { reply[rlen++]='\\'; reply[rlen++]='t'; }
                    else if (c < 0x20)  { rlen += sprintf(reply+rlen,"\\u%04x",c); }
                    else                { reply[rlen++] = c; }
                }
                reply[rlen++]='"'; reply[rlen++]='}'; reply[rlen]='\0';
                int ok = send_json_msg(sock, reply);
                free(reply);
                if (!ok) { sock_close(sock); return 0; }
                continue;
            } else if (strncmp(stream_type, "ERROR", 5) == 0) {
                got_terminal_status = 1;
                free(msg);
                break;
            }
        }
        free(msg);
    }

    sock_close(sock);
    if (!got_terminal_status) {
        remove(ann_path);
        if (debug) fprintf(stderr, "[C-DISPATCH] daemon unhealthy — falling back to execv\n");
    }
    return 0;
}

static void fallback_to_python_v(const char *self_dir, char **argv,
                                  const char *inject_version) {
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
    int out = 3;
    if (inject_version) {
        new_argv[out++] = "--python";
        new_argv[out++] = (char *)inject_version;
    }
    for (int i = 1; i < argc; i++)
        new_argv[i + out - 1] = argv[i];
    new_argv[argc + out - 1] = NULL;
    execv(py, new_argv);
    perror("omnipkg: execv fallback failed");
    exit(1);
}
static void fallback_to_python(const char *self_dir, char **argv) {
    fallback_to_python_v(self_dir, argv, NULL);
}

/* ── stamp-file helpers ─────────────────────────────────────────────────── */
/*
 * Stamp file: /tmp/omnipkg/ffi_ok/<djb2hash>.stamp
 * Presence = uv_ffi verified/installed for that interpreter.
 * Check cost: one stat() call, ~2µs — replaces the old 60ms subprocess check.
 */
/* Return platform temp dir, no trailing slash */
static const char *get_tmp_dir(void) {
#ifdef _WIN32
    static char _tmpbuf[MAX_PATH];
    /* TEMP > TMP > C:\Windows\Temp */
    const char *t = getenv("TEMP");
    if (!t) t = getenv("TMP");
    if (t) { strncpy(_tmpbuf, t, sizeof(_tmpbuf)-1); _tmpbuf[sizeof(_tmpbuf)-1]='\0'; return _tmpbuf; }
    return "C:\\Windows\\Temp";
#else
    const char *t = getenv("TMPDIR");
    return t ? t : "/tmp";
#endif
}

static void ffi_stamp_path(const char *python_exe, char *out, size_t n) {
    unsigned long h = 5381;
    const unsigned char *p = (const unsigned char *)python_exe;
    while (*p) { h = h * 33 ^ *p++; }
    snprintf(out, n, "%s/omnipkg/ffi_ok/%lu.stamp", get_tmp_dir(), h);
}



static void ffi_stamp_write(const char *python_exe) {
    char dir1[MAX_PATH], dir2[MAX_PATH];
    snprintf(dir1, sizeof(dir1), "%s/omnipkg", get_tmp_dir());
    snprintf(dir2, sizeof(dir2), "%s/omnipkg/ffi_ok", get_tmp_dir());
    mkdir_compat(dir1, 0755);
    mkdir_compat(dir2, 0755);
    char stamp[MAX_PATH];
    ffi_stamp_path(python_exe, stamp, sizeof(stamp));
    FILE *f = fopen(stamp, "w");
    if (f) { fputs(python_exe, f); fclose(f); }
}

static void ffi_stamp_write_so(const char *python_exe, const char *so_path) {
    char dir1[MAX_PATH], dir2[MAX_PATH];
    snprintf(dir1, sizeof(dir1), "%s/omnipkg", get_tmp_dir());
    snprintf(dir2, sizeof(dir2), "%s/omnipkg/ffi_ok", get_tmp_dir());
    mkdir_compat(dir1, 0755);
    mkdir_compat(dir2, 0755);
    char stamp[MAX_PATH];
    ffi_stamp_path(python_exe, stamp, sizeof(stamp));
    FILE *f = fopen(stamp, "w");
    if (f) { fputs(so_path, f); fclose(f); }
}

static int ffi_stamp_exists(const char *python_exe) {
    char stamp[MAX_PATH];
    ffi_stamp_path(python_exe, stamp, sizeof(stamp));
    struct stat st;
    if (stat(stamp, &st) != 0) return 0;
    /* read the .so path recorded in the stamp and verify it still exists */
    FILE *f = fopen(stamp, "r");
    if (!f) return 0;
    char so_path[MAX_PATH] = {0};
    fgets(so_path, sizeof(so_path), f);
    fclose(f);
    so_path[strcspn(so_path, "\n")] = 0;
    if (so_path[0] == '/' && stat(so_path, &st) != 0) return 0;
    return 1;
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
            char *dir = strtok(path_copy, PATH_SEPARATOR);
            static char uv_found2[MAX_PATH];
            while (dir) {
                snprintf(uv_found2, sizeof(uv_found2), "%s/uv", dir);
                if (file_exists(uv_found2)) { uv_exe = uv_found2; break; }
                dir = strtok(NULL, PATH_SEPARATOR);
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
    /* stamp written by find_or_install_uv_ffi_so with verified .so path */

    /* Tell the daemon worker to reload its FFI handle for this interpreter.
     * Fire-and-forget via daemon_connect (handles tcp:// on Windows, unix:// on POSIX). */
    if (sock_path && sock_path[0]) {
        int _s = daemon_connect(debug, NULL, 0);
        if (_s >= 0) {
            daemon_set_timeouts(_s, 1, 1);
            char _msg[MAX_PATH + 64];
            snprintf(_msg, sizeof(_msg),
                "{\"type\":\"reload_ffi\",\"python_exe\":\"%s\"}", target_python);
            send_json_msg(_s, _msg);
            if (debug) fprintf(stderr,
                "[C-DISPATCH] sent reload_ffi to daemon for %s\n", target_python);
            sock_close(_s);
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
            "except ImportError:\n"
            "    try:\n"
            "        import uv_ffi as m\n"
            "    except: pass\n"
            "except: pass\n"
            "else:\n"
            "    d=os.path.dirname(m.__file__)\n"
            "    import glob as _g\n"
            "    hits=_g.glob(os.path.join(d,'*.so'))+_g.glob(os.path.join(d,'_native','*.so'))+_g.glob(os.path.join(d,'_native','*.pyd'))\n"
            "    [print(h) for h in hits]\n"
            "\" 2>%s",
            target_python,
#ifdef _WIN32
            "NUL"
#else
            "/dev/null"
#endif
            );
        FILE *_pp = popen(_py_cmd, "r");
        if (_pp) {
            if (fgets(_py_out, sizeof(_py_out), _pp)) {
                _py_out[strcspn(_py_out, "\r\n")] = '\0';
                if (_py_out[0] && file_exists(_py_out)) {
                    strncpy(so_path_out, _py_out, so_path_n - 1);
                    so_path_out[so_path_n - 1] = '\0';
                    pclose(_pp);
                    if (debug) fprintf(stderr, "[C-DISPATCH] Found uv_ffi.so via Python: %s\n", so_path_out);
                    ffi_stamp_write_so(target_python, so_path_out);
                    return 1;
                }
            }
            pclose(_pp);
        }
    }

    /* ── 1. Search known locations for an existing .so / .pyd ── */
    char patterns[8][MAX_PATH];
    /* Windows home: USERPROFILE or HOMEDRIVE+HOMEPATH */
    const char *home = getenv("HOME");
#ifdef _WIN32
    if (!home) home = getenv("USERPROFILE");
#endif
    /* derive target interpreter prefix for ABI-correct .so lookup */
    char _target_prefix[MAX_PATH] = {0};
    {
        char _tp_cmd[MAX_PATH * 2];
        snprintf(_tp_cmd, sizeof(_tp_cmd),
            "\"%s\" -c \"import sys; print(sys.prefix)\"", target_python);
        FILE *_tp_fp = popen(_tp_cmd, "r");
        if (_tp_fp) { fgets(_target_prefix, sizeof(_target_prefix), _tp_fp); pclose(_tp_fp); }
        _target_prefix[strcspn(_target_prefix, "\n")] = 0;
    }
    const char *_search_root = _target_prefix[0] ? _target_prefix : venv_root;
    /* standard installed: uv_ffi/_native/ */
    snprintf(patterns[0], MAX_PATH,
        "%s/lib/python3.*/site-packages/uv_ffi/_native/*.pyd", _search_root);
    snprintf(patterns[1], MAX_PATH,
        "%s/lib/python3.*/site-packages/uv_ffi/_native/*.so", _search_root);
    /* flat install: uv_ffi/*.pyd / *.so */
    snprintf(patterns[2], MAX_PATH,
        "%s/lib/python3.*/site-packages/uv_ffi/*.pyd", _search_root);
    snprintf(patterns[3], MAX_PATH,
        "%s/lib/python3.*/site-packages/uv_ffi/*.so", _search_root);
    /* dev/vendor install: omnipkg/_vendor/uv_ffi/ */
    snprintf(patterns[4], MAX_PATH,
        "%s/lib/python3.*/site-packages/omnipkg/_vendor/uv_ffi/*.pyd", _search_root);
    snprintf(patterns[5], MAX_PATH,
        "%s/lib/python3.*/site-packages/omnipkg/_vendor/uv_ffi/*.so", _search_root);
    /* user local */
    snprintf(patterns[6], MAX_PATH,
        "%s/.local/lib/python3.*/site-packages/uv_ffi/_native/*.pyd",
        home ? home : get_tmp_dir());
    snprintf(patterns[7], MAX_PATH,
        "%s/.local/lib/python3.*/site-packages/uv_ffi/*.pyd",
        home ? home : get_tmp_dir());

    glob_t globbuf;
    for (int pi = 0; pi < 8; pi++) {
        if (glob(patterns[pi], 0, NULL, &globbuf) == 0 && globbuf.gl_pathc > 0) {
            strncpy(so_path_out, globbuf.gl_pathv[0], so_path_n - 1);
            so_path_out[so_path_n - 1] = '\0';
            globfree(&globbuf);
            if (debug) fprintf(stderr, "[C-DISPATCH] Found uv_ffi.so: %s\n", so_path_out);
            ffi_stamp_write_so(target_python, so_path_out);
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
            char *dir = strtok(path_copy, PATH_SEPARATOR);
            static char uv_found[MAX_PATH];
            while (dir) {
                snprintf(uv_found, sizeof(uv_found), "%s/uv", dir);
                if (file_exists(uv_found)) { uv_exe = uv_found; break; }
                dir = strtok(NULL, PATH_SEPARATOR);
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
            ffi_stamp_write_so(target_python, so_path_out);
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

    /* ── 1b. Self-staleness check ────────────────────────────────
     *
     * The C dispatcher cannot call _maybe_install_c_dispatcher() because
     * that lives in Python.  Instead we check whether dispatcher.c has
     * changed since we were last compiled by comparing the MD5 hash stored
     * in the marker file against the current file mtime.
     *
     * We avoid MD5 in C (no stdlib) and instead compare mtime of
     * dispatcher.c against mtime of the marker file — same signal, zero
     * extra dependencies.  If dispatcher.c is newer → wipe the marker →
     * fallback_to_python() → Python sees no marker → recompiles → re-execs
     * back as the updated C binary on the very next invocation.
     *
     * Candidate search mirrors _maybe_install_c_dispatcher() in Python:
     *   1. <self_dir>/../src/omnipkg/dispatcher.c   (editable install)
     *   2. <self_dir>/../dispatcher.c               (alt layout)
     *   3. <self_dir>/../../src/omnipkg/dispatcher.c
     * We skip this check when OMNIPKG_FORCE_PYTHON_DISPATCH is set (already
     * handled above) and when we can't find dispatcher.c at all (installed
     * binary with no source — nothing to compare against).
     */
    {
        char c_src[MAX_PATH] = "";
        /* candidate 1: <bin>/../src/omnipkg/dispatcher.c */
        char vr_tmp[MAX_PATH];
        dir_of(self_dir, vr_tmp, sizeof(vr_tmp));  /* one level up from bin/ */
        char cand[MAX_PATH];
        snprintf(cand, sizeof(cand), "%s/src/omnipkg/dispatcher.c", vr_tmp);
        if (file_exists(cand)) {
            strncpy(c_src, cand, sizeof(c_src) - 1);
        }
        /* candidate 2: <bin>/../dispatcher.c */
        if (!c_src[0]) {
            snprintf(cand, sizeof(cand), "%s/dispatcher.c", vr_tmp);
            if (file_exists(cand)) strncpy(c_src, cand, sizeof(c_src) - 1);
        }
        /* candidate 3: <bin>/../../src/omnipkg/dispatcher.c */
        if (!c_src[0]) {
            char vr2[MAX_PATH];
            dir_of(vr_tmp, vr2, sizeof(vr2));
            snprintf(cand, sizeof(cand), "%s/src/omnipkg/dispatcher.c", vr2);
            if (file_exists(cand)) strncpy(c_src, cand, sizeof(c_src) - 1);
        }

        if (debug)
            fprintf(stderr, "[C-STALE] c_src found=%s path=%s\n",
                    c_src[0] ? "yes" : "no", c_src[0] ? c_src : "(none)");

        /* Read marker — format written by Python: "<md5>:<abs/path/to/dispatcher.c>" */
        char marker_path[MAX_PATH];
        snprintf(marker_path, sizeof(marker_path),
                 "%s/.omnipkg_dispatch_compiled", self_dir);

        char stored_hash[33] = "";
        int marker_found = 0;
        FILE *mf = fopen(marker_path, "r");
        if (mf) {
            marker_found = 1;
            char marker_content[MAX_PATH * 2];
            if (fgets(marker_content, sizeof(marker_content), mf)) {
                marker_content[strcspn(marker_content, "\r\n")] = '\0';
                char *colon = strchr(marker_content, ':');
                if (colon) {
                    size_t hlen = (size_t)(colon - marker_content);
                    if (hlen < sizeof(stored_hash)) {
                        memcpy(stored_hash, marker_content, hlen);
                        stored_hash[hlen] = '\0';
                    }
                    strncpy(c_src, colon + 1, sizeof(c_src) - 1);
                }
            }
            fclose(mf);
        }

        if (debug)
            fprintf(stderr, "[C-STALE] marker=%s stored_hash=%s c_src=%s\n",
                    marker_found ? marker_path : "(missing)",
                    stored_hash[0] ? stored_hash : "(none)",
                    c_src[0] ? c_src : "(none)");

        if (c_src[0]) {
            /* Compare stored MD5 hash against current file — mtime is unreliable
             * (pip installs, adopt operations, and file copies all reset it).
             * If the path from the marker doesn't exist, treat as stale too. */
            char current_hash[33] = "";
            int src_ok = md5_file(c_src, current_hash);

            /* stored_hash was parsed from marker content above */
            int hash_match = (src_ok && stored_hash[0] &&
                              strcmp(current_hash, stored_hash) == 0);

            if (debug)
                fprintf(stderr,
                    "[C-STALE] stored=%s current=%s match=%s\n",
                    stored_hash[0] ? stored_hash : "(none)",
                    src_ok         ? current_hash : "(unreadable)",
                    hash_match     ? "yes" : "NO — stale");

            if (!hash_match) {
                remove(marker_path);
                if (debug)
                    fprintf(stderr,
                        "[C-STALE] dispatcher.c changed — wiping marker, "
                        "falling back to Python for recompile\n");
                fallback_to_python(self_dir, argv);
            }
        }
    }

    /* ── 2. Shim mode? Fall back immediately ──────────────────── */
    if (strncmp(prog, "python", 6) == 0 || strcmp(prog, "pip") == 0) {
        if (debug) fprintf(stderr, "[C-DISPATCH] shim mode → python fallback\n");
        /* In swap context, execv directly to the swapped Python — don't wrap in dispatcher */
        const char *swap_active = getenv("_OMNIPKG_SWAP_ACTIVE");
        const char *swap_ver    = getenv("OMNIPKG_PYTHON");
        const char *_swap_venv = getenv("OMNIPKG_VENV_ROOT");
        if (swap_active && swap_ver && _swap_venv && _swap_venv[0]) {
            char swap_py[MAX_PATH] = "";
            registry_lookup(_swap_venv, swap_ver, swap_py, sizeof(swap_py));
            if (swap_py[0] && file_exists(swap_py)) {
                if (debug) fprintf(stderr, "[C-DISPATCH] swap shim → execv %s\n", swap_py);
                if (strcmp(prog, "pip") == 0) {
                    /* pip: execv python -m pip <args> */
                    int argc = 0;
                    while (argv[argc]) argc++;
                    char **new_argv = malloc((argc + 3) * sizeof(char *));
                    new_argv[0] = swap_py;
                    new_argv[1] = "-m";
                    new_argv[2] = "pip";
                    for (int i = 1; i < argc; i++)
                        new_argv[i + 2] = argv[i];
                    new_argv[argc + 2] = NULL;
                    execv(swap_py, new_argv);
                } else {
                    argv[0] = swap_py;
                    execv(swap_py, argv);
                }
                perror("omnipkg: execv swap python failed");
                exit(1);
            }
        }
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
                    fallback_to_python_v(self_dir, argv, cli_version);
                }
            } else {
                if (debug) fprintf(stderr, "[C-DISPATCH] unknown version %s → fallback\n", cli_version);
                fallback_to_python_v(self_dir, argv, cli_version);
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

            /* ── Pre-flight: resolve daemon_sock (passed to ensure_uv_ffi) ── */
            char daemon_sock[MAX_PATH];
            daemon_sock[0] = '\0';
            {
                char ann[MAX_PATH];
                snprintf(ann, sizeof(ann), "%s/omnipkg/daemon_connection.txt", get_tmp_dir());
                FILE *_af = fopen(ann, "r");
                if (_af) {
                    char _line[MAX_PATH];
                    if (fgets(_line, sizeof(_line), _af)) {
                        _line[strcspn(_line, "\r\n")] = '\0';
                        /* Store the raw conn string — ensure_uv_ffi just uses it
                         * as a non-empty sentinel; actual connect goes via daemon_connect() */
                        strncpy(daemon_sock, _line, sizeof(daemon_sock) - 1);
                        daemon_sock[sizeof(daemon_sock) - 1] = '\0';
                    }
                    fclose(_af);
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
            /* DISABLED: bypasses KB/bubble/strategy logic in Python core.
             * Re-enable when daemon run_uv handles KB sync + bubble preservation.
             * Until then all installs must go through run_cli Python path. */
            if (0 && ffi_rc != 0) {
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
#ifndef _WIN32
                        /* macOS: .dylib variant */
                        if (!py_handle) {
                            snprintf(pylib, sizeof(pylib), "%s/lib/lib%s.dylib",
                                     venv_root, py_ver);
                            py_handle = dlopen(pylib, RTLD_LAZY | RTLD_GLOBAL);
                        }
                        /* Ask target Python for its exact library name via sysconfig */
                        if (!py_handle) {
                            char _pylib_cmd[MAX_PATH * 2];
                            char _pylib_out[MAX_PATH] = "";
                            snprintf(_pylib_cmd, sizeof(_pylib_cmd),
                                "\"%s\" -c \"import sysconfig; "
                                "print(sysconfig.get_config_var('LDLIBRARY') or '')\""
                                " 2>/dev/null",
                                target_python);
                            FILE *_pp = popen(_pylib_cmd, "r");
                            if (_pp) {
                                if (fgets(_pylib_out, sizeof(_pylib_out), _pp))
                                    _pylib_out[strcspn(_pylib_out, "\r\n")] = '\0';
                                pclose(_pp);
                            }
                            if (_pylib_out[0]) {
                                /* Try venv/lib/filename first */
                                snprintf(pylib, sizeof(pylib), "%s/lib/%s",
                                         venv_root, _pylib_out);
                                py_handle = dlopen(pylib, RTLD_LAZY | RTLD_GLOBAL);
                                /* Try as absolute path (framework Python returns full rel path) */
                                if (!py_handle)
                                    py_handle = dlopen(_pylib_out, RTLD_LAZY | RTLD_GLOBAL);
                                /* Try LIBDIR + LDLIBRARY (framework Python canonical path) */
                                if (!py_handle) {
                                    char _pylib_cmd2[MAX_PATH * 2];
                                    char _libdir[MAX_PATH] = "";
                                    snprintf(_pylib_cmd2, sizeof(_pylib_cmd2),
                                        "\"%s\" -c \"import sysconfig; "
                                        "print(sysconfig.get_config_var('LIBDIR') or '')\""
                                        " 2>/dev/null",
                                        target_python);
                                    FILE *_pp2 = popen(_pylib_cmd2, "r");
                                    if (_pp2) {
                                        if (fgets(_libdir, sizeof(_libdir), _pp2))
                                            _libdir[strcspn(_libdir, "\r\n")] = '\0';
                                        pclose(_pp2);
                                    }
                                    if (_libdir[0]) {
                                        snprintf(pylib, sizeof(pylib), "%s/%s",
                                                 _libdir, _pylib_out);
                                        py_handle = dlopen(pylib, RTLD_LAZY | RTLD_GLOBAL);
                                    }
                                }
                            }
                        }
                        /* Last resort: libpython already loaded in this process */
                        if (!py_handle)
                            py_handle = dlopen(NULL, RTLD_LAZY | RTLD_GLOBAL);
#endif
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
                        ffi_handle = dlopen(so_path, RTLD_LAZY | RTLD_GLOBAL);
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
                    int _ds = daemon_connect(debug, NULL, 0);
                    if (_ds >= 0) {
                        daemon_set_timeouts(_ds, 1, 1);
                        /* Message 1: patch cache (UV workers update Rust SITE_PACKAGES_CACHE) */
                        char patch_msg[MAX_JSON];
                        int patch_len = snprintf(patch_msg, sizeof(patch_msg),
                            "{\"type\":\"patch_site_packages_cache\","
                            "\"site_packages_path\":\"\","
                            "\"installed\":%s,\"removed\":%s}",
                            inst_buf, rem_buf);
                        if (patch_len > 0)
                            send_json_msg(_ds, patch_msg);
                        /* Message 2: kb_sentinel (Redis/KB bookkeeping) */
                        char kb_msg[MAX_JSON];
                        int kb_len = snprintf(kb_msg, sizeof(kb_msg),
                            "{\"type\":\"kb_sentinel\",\"installed\":%s,\"removed\":%s}",
                            inst_buf, rem_buf);
                        if (kb_len > 0)
                            send_json_msg(_ds, kb_msg);
                        sock_close(_ds);
                    }
                }

                /* Fork background Python only if something actually changed */
                int has_changes = (strstr(out_json, "[\"") != NULL);
                if (has_changes) {
                    /* Build script + argv here — shared by POSIX child and Windows inline path */
                    char py_ctx_ver[16] = "3.11";
                    {
                        const char *_pv = strstr(target_python, "python3.");
                        if (_pv) {
                            _pv += 7;
                            size_t _vi = 0;
                            while (_pv[_vi] &&
                                   (_pv[_vi] == '.' ||
                                    (_pv[_vi] >= '0' && _pv[_vi] <= '9')) &&
                                   _vi < sizeof(py_ctx_ver) - 1) {
                                py_ctx_ver[_vi] = _pv[_vi]; _vi++;
                            }
                            py_ctx_ver[_vi] = '\0';
                        }
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
                        out_json, py_ctx_ver, py_ctx_ver
                    );
                    char *bg_argv[] = {
                        (char *)target_python, (char *)"-c", py_script, NULL
                    };

#ifndef _WIN32
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
                        execv(target_python, bg_argv);
                        exit(0);
                    }
#else
                    /* Windows: no fork() — run background KB work inline */
                    execv(target_python, bg_argv);
#endif /* _WIN32 */
                }
                /* No changes (already satisfied) or fork parent — exit cleanly */
                exit(0);
            }
        }
    }

    /* ── 7. Build final argv and execv (or try daemon) ───────────────────────── */
    int is_interactive_command = 0;
    if (argc >= 2) {
        int is_info   = (strcmp(argv[1], "info")   == 0);
        int is_config = (strcmp(argv[1], "config") == 0);
        /* "info python" (exactly) is non-interactive — let daemon handle it.
         * Any other "info <arg>" or bare "info" stays interactive.
         * Match argv[2] == "python" exactly to avoid catching package names
         * that contain the word python (e.g. "info python-dotenv"). */
        int info_python = (is_info && argc >= 3 &&
                           strcmp(argv[2], "python") == 0);
        is_interactive_command = ((is_info && !info_python) || is_config);
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