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
 *   3. WaitForSingleObject(hParent, 15000) -- block until parent exits
 *      (releases file locks on the running exe).
 *   4. CloseHandle(hParent).
 *   5. For each pair: CopyFileA(src, dst, FALSE) so the source is NOT consumed
 *      and all N targets receive a fresh copy of the same binary.
 *      (Old MoveFileExA approach deleted src after the first swap, causing all
 *      subsequent targets to fail with ERROR_FILE_NOT_FOUND.)
 *   5b. After all copies succeed, DeleteFileA(srcs[0]) to remove the _tmp binary.
 *   5c. Verify each dst replaced correctly by checking file size matches src.
 *   6. Write marker file so next invocation sees hash-match and skips recompile.
 *      Marker is written as long as the PRIMARY target (index 0) was swapped --
 *      secondary targets (OMNIPKG.exe, 8PKG.exe case-aliases) failing is not
 *      fatal enough to suppress the marker and force a recompile on every call.
 *
 * KEY FIX (v2): replaced MoveFileExA with CopyFileA + final DeleteFileA.
 *   The old code moved src -> dst[0], deleting src in the process, then
 *   tried to move the now-deleted src -> dst[1], which failed silently.
 *   Because ghost.c checked `all_ok` before writing the marker, a partial
 *   failure meant the marker was never written, causing infinite recompile
 *   loops on every subsequent 8pkg invocation.
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

/* Wait up to `timeout_ms` for a file to exist and be non-empty.
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

    /* Wait for parent to die -- releases file locks on the running exe.
     * Increased to 15s: Python startup on a cold machine with AV scanning
     * can take several seconds before the process fully exits. */
    if (hParent) {
        DWORD wait_result = WaitForSingleObject(hParent, 15000);
        CloseHandle(hParent);
        if (wait_result == WAIT_TIMEOUT) {
            fprintf(stderr, "omnipkg_ghost: WARNING: parent PID %d did not exit within 15s — proceeding anyway\n", parent_pid);
        }
    } else {
        /* Parent already gone — give a moment for the OS to release handles */
        Sleep(200);
    }

    /* Additional settling time: Windows releases file handles asynchronously
     * after process exit. Without this sleep, CopyFileA on the dst (which is
     * the previously-running exe) can still fail with ERROR_SHARING_VIOLATION
     * for ~100-500ms after WaitForSingleObject returns. */
    Sleep(300);

    /* Get the file size of src so we can verify each copy landed correctly */
    LARGE_INTEGER src_size_li;
    src_size_li.QuadPart = 0;
    {
        HANDLE hSrc = CreateFileA(srcs[0], GENERIC_READ, FILE_SHARE_READ, NULL,
                                  OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (hSrc != INVALID_HANDLE_VALUE) {
            GetFileSizeEx(hSrc, &src_size_li);
            CloseHandle(hSrc);
        }
    }

    /*
     * KEY FIX: Use CopyFileA instead of MoveFileExA.
     *
     * Old code: MoveFileExA(src, dst[0]) -- deletes src after first swap.
     *           MoveFileExA(src, dst[1]) -- ERROR_FILE_NOT_FOUND: src gone.
     *
     * New code: CopyFileA(src, dst[N]) for every N -- src is never consumed.
     *           DeleteFileA(src) once at the end to clean up the _tmp binary.
     *
     * CopyFileA with bFailIfExists=FALSE atomically overwrites the destination
     * on NTFS (it uses an internal transaction for the metadata update), which
     * is sufficient for our use case since the old process has already exited
     * and released its lock on dst.
     */
    int primary_ok = 0;  /* did dst[0] succeed? controls marker write */
    int all_ok = 1;

    for (int s = 0; s < n_swaps; s++) {
        /* Retry loop: give Windows time to fully release the file lock.
         * Even after WaitForSingleObject + Sleep(300), a stray handle from
         * AV or the Windows loader can linger for another few hundred ms. */
        int copy_ok = 0;
        for (int attempt = 0; attempt < 10; attempt++) {
            if (CopyFileA(srcs[s], dsts[s], FALSE)) {
                copy_ok = 1;
                break;
            }
            DWORD err = GetLastError();
            fprintf(stderr, "omnipkg_ghost: copy attempt %d FAILED %s -> %s (err=%lu) — retrying\n",
                    attempt + 1, srcs[s], dsts[s], (unsigned long)err);
            /* ERROR_SHARING_VIOLATION (32) or ERROR_ACCESS_DENIED (5): wait longer */
            if (err == 32 || err == 5) {
                Sleep(300);
            } else {
                /* Any other error (file not found, path too long, etc.) won't
                 * improve with retrying — break immediately */
                break;
            }
        }

        if (copy_ok) {
            /* Verify: the destination file must now exist and match src size */
            if (src_size_li.QuadPart > 0) {
                LARGE_INTEGER dst_size;
                dst_size.QuadPart = 0;
                HANDLE hDst = CreateFileA(dsts[s], GENERIC_READ, FILE_SHARE_READ, NULL,
                                          OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
                if (hDst != INVALID_HANDLE_VALUE) {
                    GetFileSizeEx(hDst, &dst_size);
                    CloseHandle(hDst);
                }
                if (dst_size.QuadPart != src_size_li.QuadPart) {
                    fprintf(stderr, "omnipkg_ghost: VERIFY FAILED %s: expected %lld bytes, got %lld\n",
                            dsts[s],
                            (long long)src_size_li.QuadPart,
                            (long long)dst_size.QuadPart);
                    copy_ok = 0;
                }
            }
        }

        if (copy_ok) {
            if (s == 0) primary_ok = 1;
        } else {
            fprintf(stderr, "omnipkg_ghost: swap FAILED %s -> %s\n", srcs[s], dsts[s]);
            all_ok = 0;
        }
    }

    /* Delete the _tmp binary now that all copies are done.
     * srcs[0] is always the _tmp.exe path (all pairs share the same src). */
    if (!DeleteFileA(srcs[0])) {
        /* Non-fatal: the _tmp file will be cleaned up on the next compile. */
        fprintf(stderr, "omnipkg_ghost: WARNING: could not delete tmp binary %s (err=%lu)\n",
                srcs[0], (unsigned long)GetLastError());
    }

    /*
     * Write marker so Python side sees hash-match on next invocation.
     *
     * Write the marker if the PRIMARY binary (dst[0], i.e. 8pkg.exe) was
     * successfully replaced. Secondary aliases (OMNIPKG.exe, 8PKG.exe) are
     * case-duplicates on case-insensitive NTFS — their failure is not fatal
     * enough to force an infinite recompile loop.
     *
     * Old code used `all_ok` here, which meant a single secondary failure
     * would suppress the marker entirely, causing _maybe_install_c_dispatcher
     * to recompile + re-ghost on every single 8pkg invocation forever.
     */
    if (primary_ok && marker_path[0] && marker_content[0]) {
        /* Brief wait to let the filesystem flush before writing marker */
        Sleep(100);
        FILE *mf = fopen(marker_path, "w");
        if (mf) {
            fputs(marker_content, mf);
            fclose(mf);
            /* Verify the marker was actually written */
            if (!wait_for_file(marker_path, 2000)) {
                fprintf(stderr, "omnipkg_ghost: WARNING: marker file not confirmed at %s\n", marker_path);
            }
        } else {
            fprintf(stderr, "omnipkg_ghost: WARNING: could not open marker file %s for writing\n", marker_path);
        }
    }

    return all_ok ? 0 : 1;
}
