/*
 * omnipkg_ghost.c -- Windows ghost-swap helper
 *
 * Usage:
 *   omnipkg_ghost --ghost <parent_pid> <src1> <dst1> [<src2> <dst2> ...]
 *                 [--marker <marker_path> <marker_content>]
 *
 * Steps:
 *   1. Parse args: swap pairs, optional --marker.
 *   2. OpenProcess(SYNCHRONIZE, parent_pid) to watch parent death.
 *   3. WaitForSingleObject(hParent, 10000) -- block until parent exits
 *      (releases file locks on the running exe).
 *   4. CloseHandle(hParent).
 *   5. MoveFileExA(src, dst, MOVEFILE_REPLACE_EXISTING) for each pair.
 *   5b. Write marker file so next invocation sees hash-match and skips recompile.
 *
 * Compile (MSVC):
 *   cl /nologo /O2 /Fe:omnipkg_ghost.exe ghost.c /link kernel32.lib
 *
 * Compile (GCC/MinGW):
 *   gcc -O2 -o omnipkg_ghost.exe ghost.c -lkernel32
 */

#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define MAX_SWAPS 16

int main(int argc, char *argv[]) {
    if (argc < 4 || strcmp(argv[1], "--ghost") != 0) {
        fprintf(stderr, "omnipkg_ghost: usage: --ghost <pid> <src> <dst> [...] [--marker <path> <content>]\n");
        return 1;
    }

    int parent_pid = atoi(argv[2]);

    char *srcs[MAX_SWAPS];
    char *dsts[MAX_SWAPS];
    int n_swaps = 0;
    char marker_path[4096] = {0};
    char marker_content[4096] = {0};

    int i = 3;
    while (i < argc) {
        if (strcmp(argv[i], "--marker") == 0) {
            i++;
            if (i < argc) { strncpy(marker_path,    argv[i++], sizeof(marker_path)    - 1); }
            if (i < argc) { strncpy(marker_content, argv[i++], sizeof(marker_content) - 1); }
            continue;
        }
        if (n_swaps < MAX_SWAPS && i + 1 < argc) {
            srcs[n_swaps] = argv[i];
            dsts[n_swaps] = argv[i + 1];
            n_swaps++;
            i += 2;
        } else {
            i++;
        }
    }

    if (n_swaps == 0) {
        fprintf(stderr, "omnipkg_ghost: no swap pairs provided\n");
        return 1;
    }

    /* Open parent with SYNCHRONIZE so we can wait on it */
    HANDLE hParent = OpenProcess(SYNCHRONIZE, FALSE, (DWORD)parent_pid);

    /* Wait for parent to die -- releases file locks on the running exe */
    if (hParent) {
        WaitForSingleObject(hParent, 10000);
        CloseHandle(hParent);
    } else {
        /* Parent already gone */
        Sleep(100);
    }

    /* Atomic swap each pair */
    int all_ok = 1;
    for (int s = 0; s < n_swaps; s++) {
        if (!MoveFileExA(srcs[s], dsts[s], MOVEFILE_REPLACE_EXISTING)) {
            fprintf(stderr, "omnipkg_ghost: swap FAILED %s -> %s (err=%lu)\n",
                    srcs[s], dsts[s], (unsigned long)GetLastError());
            all_ok = 0;
        }
    }

    /* Write marker so Python side sees hash-match on next invocation */
    if (all_ok && marker_path[0] && marker_content[0]) {
        FILE *mf = fopen(marker_path, "w");
        if (mf) {
            fputs(marker_content, mf);
            fclose(mf);
        }
    }

    return all_ok ? 0 : 1;
}
