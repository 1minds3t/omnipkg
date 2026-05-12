/*
 * omnipkg_ghost.c -- Windows self-moving ghost helper
 *
 * Usage:
 *   <this_exe> --ghost <parent_pid> <target_path>
 *              [--marker <marker_path> <marker_content>]
 *
 * Architecture (v3 — "compile-as-ghost"):
 *   dispatcher.py compiles dispatcher.c directly to a unique temp path per
 *   target (e.g. %TEMP%\omnipkg\_ghost_0_8pkg.exe).  That temp exe IS the
 *   new dispatcher binary — it just needs to move itself into place.
 *
 *   Steps:
 *   1. Parse args: single target path, optional --marker.
 *   2. OpenProcess(SYNCHRONIZE, parent_pid) to watch parent death.
 *   3. WaitForSingleObject(hParent, 15000) — block until parent exits
 *      (releases the file lock on the running 8pkg.exe).
 *   4. CloseHandle(hParent).
 *   5. GetModuleFileNameA(NULL, self_path) — find our own exe path.
 *   6. Extra Sleep(300) for Windows async handle release.
 *   7. Retry loop: MoveFileExA(self_path, target, MOVEFILE_REPLACE_EXISTING)
 *      One atomic move — no separate _tmp to clean up, no size mismatch,
 *      no second ghost with a missing source.  On NTFS, MoveFileExA within
 *      the same volume is a metadata-only rename (near-instant, no copy).
 *      Cross-volume (temp on a different drive): falls back to copy+delete,
 *      still correct.
 *   8. Write marker file (if --marker provided) after successful move.
 *
 * One ghost process is spawned per target binary (8pkg.exe, omnipkg.exe …).
 * dispatcher.py compiles dispatcher.c once per target to a unique temp path,
 * then spawns each resulting exe as a ghost for its own target.  No shared
 * _tmp binary means no serialization and no "src already moved" failures.
 *
 * Compile (MSVC):
 *   cl /nologo /O2 /Fe:<output>.exe ghost.c /link kernel32.lib
 *
 * Compile (GCC/MinGW):
 *   gcc -O2 -o <output>.exe ghost.c -lkernel32
 */

#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

/* Wait up to timeout_ms for a file to exist and be non-empty.
 * Returns 1 if confirmed, 0 on timeout. */
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
            "omnipkg_ghost: usage: --ghost <pid> <target_path> [--marker <path> <content>]\n");
        return 1;
    }

    int parent_pid = atoi(argv[2]);
    const char *target = argv[3];

    char marker_path[4096]    = {0};
    char marker_content[4096] = {0};

    int i = 4;
    while (i < argc) {
        if (strcmp(argv[i], "--marker") == 0) {
            i++;
            if (i < argc) { strncpy(marker_path,    argv[i++], sizeof(marker_path)    - 1); }
            if (i < argc) { strncpy(marker_content, argv[i++], sizeof(marker_content) - 1); }
        } else {
            i++;
        }
    }

    /* --- Wait for parent to release the lock on the target exe --- */
    HANDLE hParent = OpenProcess(SYNCHRONIZE, FALSE, (DWORD)parent_pid);
    if (hParent) {
        DWORD wait_result = WaitForSingleObject(hParent, 15000);
        CloseHandle(hParent);
        if (wait_result == WAIT_TIMEOUT) {
            fprintf(stderr,
                "omnipkg_ghost: WARNING: parent PID %d did not exit within 15s — proceeding anyway\n",
                parent_pid);
        }
    } else {
        /* Parent already gone — brief settle for OS handle release */
        Sleep(200);
    }

    /* Windows releases file handles asynchronously after process exit.
     * Without this, MoveFileExA on the previously-running exe can still fail
     * with ERROR_SHARING_VIOLATION for ~100-500ms after WaitForSingleObject. */
    Sleep(300);

    /* --- Find our own path (we ARE the new binary) --- */
    char self_path[MAX_PATH];
    if (!GetModuleFileNameA(NULL, self_path, MAX_PATH)) {
        fprintf(stderr, "omnipkg_ghost: GetModuleFileNameA failed (err=%lu)\n",
                (unsigned long)GetLastError());
        return 1;
    }

    /* --- Move self -> target (retry on sharing violations) --- */
    int move_ok = 0;
    for (int attempt = 0; attempt < 10; attempt++) {
        if (MoveFileExA(self_path, target, MOVEFILE_REPLACE_EXISTING)) {
            move_ok = 1;
            break;
        }
        DWORD err = GetLastError();
        fprintf(stderr,
            "omnipkg_ghost: move attempt %d FAILED %s -> %s (err=%lu) — retrying\n",
            attempt + 1, self_path, target, (unsigned long)err);
        /* ERROR_SHARING_VIOLATION (32) or ERROR_ACCESS_DENIED (5): wait */
        if (err == 32 || err == 5) {
            Sleep(300);
        } else {
            /* Any other error won't improve with retrying */
            break;
        }
    }

    if (!move_ok) {
        fprintf(stderr, "omnipkg_ghost: swap FAILED %s -> %s\n", self_path, target);
        return 1;
    }

    fprintf(stderr, "omnipkg_ghost: swap OK %s -> %s\n", self_path, target);

    /* --- Write marker --- */
    if (marker_path[0] && marker_content[0]) {
        Sleep(100);  /* brief flush before writing marker */
        FILE *mf = fopen(marker_path, "w");
        if (mf) {
            fputs(marker_content, mf);
            fclose(mf);
            if (!wait_for_file(marker_path, 2000)) {
                fprintf(stderr,
                    "omnipkg_ghost: WARNING: marker file not confirmed at %s\n",
                    marker_path);
            }
        } else {
            fprintf(stderr,
                "omnipkg_ghost: WARNING: could not open marker file %s for writing\n",
                marker_path);
        }
    }

    return 0;
}
