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

static void fallback_to_python(const char *self_dir, char **argv, const char *inject_version) {
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

    /* Build new argv: python -m omnipkg.dispatcher [--python <ver>] <original args> */
    int argc = 0;
    while (argv[argc]) argc++;
    int has_python_flag = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--python") == 0) { has_python_flag = 1; break; }
    }
    int extra = (inject_version && !has_python_flag) ? 2 : 0;
    char **new_argv = malloc((argc + 4 + extra) * sizeof(char *));
    new_argv[0] = py;
    new_argv[1] = "-m";
    new_argv[2] = "omnipkg.dispatcher";
    int idx = 3;
    if (inject_version && !has_python_flag) {
        new_argv[idx++] = "--python";
        new_argv[idx++] = (char *)inject_version;
    }
    for (int i = 1; i < argc; i++)
        new_argv[idx++] = argv[i];
    new_argv[idx] = NULL;

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
        fallback_to_python(self_dir, argv, NULL);
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
        fallback_to_python(self_dir, argv, NULL);
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
        fallback_to_python(self_dir, argv, NULL);
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
                fallback_to_python(self_dir, argv, cli_version);
            }
            if (debug)
                fprintf(stderr, "[C-DISPATCH] registry hit %s → %s\n",
                        cli_version, target_python);
        } else {
            /* Unknown version → Python fallback for proper error / auto-adopt */
            if (debug) fprintf(stderr, "[C-DISPATCH] unknown version %s → fallback\n", cli_version);
            fallback_to_python(self_dir, argv, cli_version);
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
            fallback_to_python(self_dir, argv, NULL);
        }
    }

    /* ── 6. Build final argv and execv ───────────────────────── */
    /*
     * target_python -m omnipkg.cli [--python X] [original args]
     *
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

    execv(target_python, new_argv);

    /* execv only returns on error */
    perror("omnipkg: execv failed");
    return 1;
}