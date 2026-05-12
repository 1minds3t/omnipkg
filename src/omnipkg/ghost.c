/*
 * omnipkg_ghost.c -- Windows self-moving ghost helper
 *
 * Usage:
 *   <this_exe> --ghost <parent_pid> <target_path>
 *              [--marker <marker_path> <marker_content>]
 *              [--log <log_path>]
 *
 * Everything is logged to <log_path> (and stderr) so the detached process
 * leaves a full trace even though Python redirects its stdio to DEVNULL.
 * Log path: %TEMP%\omnipkg\ghost_<idx>_<name>.log  (passed by dispatcher.py)
 */

#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdlib.h>

static FILE *g_log = NULL;

static void log_open(const char *path) {
    if (path && path[0]) {
        g_log = fopen(path, "w");
        if (!g_log)
            fprintf(stderr, "omnipkg_ghost: WARNING: could not open log %s (err=%lu)\n",
                    path, (unsigned long)GetLastError());
    }
}

static void glog(const char *fmt, ...) {
    va_list ap;
    if (g_log) {
        va_start(ap, fmt);
        vfprintf(g_log, fmt, ap);
        va_end(ap);
        fflush(g_log);
    }
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
}

static int wait_for_file(const char *path, int timeout_ms) {
    int elapsed = 0;
    while (elapsed < timeout_ms) {
        WIN32_FILE_ATTRIBUTE_DATA fa;
        if (GetFileAttributesExA(path, GetFileExInfoStandard, &fa)) {
            ULONGLONG sz = ((ULONGLONG)fa.nFileSizeHigh << 32) | fa.nFileSizeLow;
            if (sz > 0) return 1;
        }
        Sleep(50);
        elapsed += 50;
    }
    return 0;
}

int main(int argc, char *argv[]) {
    if (argc < 4 || strcmp(argv[1], "--ghost") != 0) {
        fprintf(stderr,
            "omnipkg_ghost: usage: --ghost <pid> <target> [--marker <path> <content>] [--log <path>]\n");
        return 1;
    }

    int parent_pid = atoi(argv[2]);
    const char *target = argv[3];

    char marker_path[4096]    = {0};
    char marker_content[4096] = {0};
    char log_path[4096]       = {0};

    int i = 4;
    while (i < argc) {
        if (strcmp(argv[i], "--marker") == 0) {
            i++;
            if (i < argc) { strncpy(marker_path,    argv[i++], sizeof(marker_path)    - 1); }
            if (i < argc) { strncpy(marker_content, argv[i++], sizeof(marker_content) - 1); }
        } else if (strcmp(argv[i], "--log") == 0) {
            i++;
            if (i < argc) { strncpy(log_path, argv[i++], sizeof(log_path) - 1); }
        } else {
            i++;
        }
    }

    log_open(log_path);
    glog("omnipkg_ghost: START pid=%d target=%s\n", GetCurrentProcessId(), target);
    glog("omnipkg_ghost: waiting for parent pid=%d\n", parent_pid);

    HANDLE hParent = OpenProcess(SYNCHRONIZE, FALSE, (DWORD)parent_pid);
    if (hParent) {
        DWORD wait_result = WaitForSingleObject(hParent, 15000);
        CloseHandle(hParent);
        if (wait_result == WAIT_TIMEOUT) {
            glog("omnipkg_ghost: WARNING: parent PID %d did not exit within 15s -- proceeding anyway\n",
                 parent_pid);
        } else {
            glog("omnipkg_ghost: parent exited (wait_result=%lu)\n", (unsigned long)wait_result);
        }
    } else {
        glog("omnipkg_ghost: OpenProcess failed (err=%lu) -- parent already gone, sleeping 200ms\n",
             (unsigned long)GetLastError());
        Sleep(200);
    }

    glog("omnipkg_ghost: sleeping 300ms for async handle release\n");
    Sleep(300);

    char self_path[MAX_PATH] = {0};
    if (!GetModuleFileNameA(NULL, self_path, MAX_PATH)) {
        glog("omnipkg_ghost: FATAL: GetModuleFileNameA failed (err=%lu)\n",
             (unsigned long)GetLastError());
        return 1;
    }
    glog("omnipkg_ghost: self_path=%s\n", self_path);

    /* Check target exists before attempting move */
    DWORD target_attrs = GetFileAttributesA(target);
    glog("omnipkg_ghost: target exists=%s (attrs=0x%lx)\n",
         (target_attrs == INVALID_FILE_ATTRIBUTES) ? "NO" : "YES",
         (unsigned long)target_attrs);

    /* Check self size */
    {
        WIN32_FILE_ATTRIBUTE_DATA fa;
        if (GetFileAttributesExA(self_path, GetFileExInfoStandard, &fa)) {
            ULONGLONG sz = ((ULONGLONG)fa.nFileSizeHigh << 32) | fa.nFileSizeLow;
            glog("omnipkg_ghost: self size=%llu bytes\n", (unsigned long long)sz);
        } else {
            glog("omnipkg_ghost: WARNING: could not stat self (err=%lu)\n",
                 (unsigned long)GetLastError());
        }
    }

    int move_ok = 0;
    for (int attempt = 0; attempt < 10; attempt++) {
        glog("omnipkg_ghost: move attempt %d: MoveFileExA(%s, %s)\n",
             attempt + 1, self_path, target);
        if (MoveFileExA(self_path, target, MOVEFILE_REPLACE_EXISTING)) {
            move_ok = 1;
            glog("omnipkg_ghost: move attempt %d SUCCEEDED\n", attempt + 1);
            break;
        }
        DWORD err = GetLastError();
        glog("omnipkg_ghost: move attempt %d FAILED (err=%lu)\n",
             attempt + 1, (unsigned long)err);
        if (err == 32 || err == 5) {
            glog("omnipkg_ghost: sharing violation/access denied -- sleeping 300ms\n");
            Sleep(300);
        } else {
            glog("omnipkg_ghost: non-retryable error -- aborting retry loop\n");
            break;
        }
    }

    if (!move_ok) {
        glog("omnipkg_ghost: FATAL: all move attempts failed: %s -> %s\n", self_path, target);
        if (g_log) fclose(g_log);
        return 1;
    }

    glog("omnipkg_ghost: swap OK -- %s is now the C dispatcher\n", target);

    /* Verify target */
    {
        WIN32_FILE_ATTRIBUTE_DATA fa;
        if (GetFileAttributesExA(target, GetFileExInfoStandard, &fa)) {
            ULONGLONG sz = ((ULONGLONG)fa.nFileSizeHigh << 32) | fa.nFileSizeLow;
            glog("omnipkg_ghost: verify target size=%llu bytes -- %s\n",
                 (unsigned long long)sz, sz > 0 ? "OK" : "SUSPICIOUS (zero bytes!)");
        } else {
            glog("omnipkg_ghost: WARNING: could not verify target after move (err=%lu)\n",
                 (unsigned long)GetLastError());
        }
    }

    if (marker_path[0] && marker_content[0]) {
        glog("omnipkg_ghost: writing marker: %s\n", marker_path);
        Sleep(100);
        FILE *mf = fopen(marker_path, "w");
        if (mf) {
            fputs(marker_content, mf);
            fclose(mf);
            if (wait_for_file(marker_path, 2000)) {
                glog("omnipkg_ghost: marker confirmed OK\n");
            } else {
                glog("omnipkg_ghost: WARNING: marker not confirmed after 2s at %s\n", marker_path);
            }
        } else {
            glog("omnipkg_ghost: WARNING: could not open marker for writing (err=%lu)\n",
                 (unsigned long)GetLastError());
        }
    }

    glog("omnipkg_ghost: DONE\n");
    if (g_log) fclose(g_log);
    return 0;
}
