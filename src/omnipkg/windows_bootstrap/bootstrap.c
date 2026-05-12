#include <windows.h>
#include <stdio.h>
#include <string.h>

void ghost_mode(int parent_pid, const char* target_path) {
    printf("Ghost (PID %d): waiting for parent (PID %d)...\n",
           GetCurrentProcessId(), parent_pid);
    fflush(stdout);

    HANDLE hParent = OpenProcess(SYNCHRONIZE, FALSE, parent_pid);
    if (hParent) {
        WaitForSingleObject(hParent, 5000);
        CloseHandle(hParent);
    }

    printf("Ghost: parent dead. Overwriting: %s\n", target_path);
    fflush(stdout);

    char self_path[MAX_PATH];
    GetModuleFileNameA(NULL, self_path, MAX_PATH);

    // This is the real test: overwrite the exe that originally launched us
    if (MoveFileExA(self_path, target_path, MOVEFILE_REPLACE_EXISTING)) {
        printf("Ghost: swap complete. %s is now the C binary.\n", target_path);
        printf("Ghost: next launch will be pure C, no Python.\n");
    } else {
        printf("Ghost: swap FAILED. Error: %lu\n", GetLastError());
    }
    fflush(stdout);
}

int main(int argc, char* argv[]) {
    if (argc > 2 && strcmp(argv[1], "--ghost") == 0) {
        int parent_pid = atoi(argv[2]);
        const char* target = argv[3];
        ghost_mode(parent_pid, target);
        return 0;
    }

    // Normal C dispatch mode - this runs after the swap
    printf("C dispatcher (PID %d): running native. No Python involved.\n",
           GetCurrentProcessId());
    fflush(stdout);
    return 0;
}
