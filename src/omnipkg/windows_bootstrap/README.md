# Windows Bootstrap / Ghost Spawn

## Problem
pip installs a Python stub as `8pkg.exe`. We want that entrypoint to become
a native C binary after first run, without any user intervention.

## Solution: Ghost Spawn
1. `launcher.py` (embedded in Python stub) compiles `bootstrap.c` via MSVC
2. Spawns `bootstrap_new.exe --ghost <parent_pid> <path_to_8pkg.exe>`
3. Python exits cleanly (file lock released)
4. Ghost polls parent PID via `WaitForSingleObject(OpenProcess(SYNCHRONIZE))`
5. Parent confirmed dead → `MoveFileExA(bootstrap_new.exe → 8pkg.exe)`
6. Next invocation: pure C, no Python ever again

## Files
- `bootstrap.c`  — the real C dispatcher + ghost logic
- `launcher.py`  — Python bootstrap embedded in the pip stub

## Key flags
- `DETACHED_PROCESS | CREATE_NO_WINDOW` — ghost survives parent death
- `MOVEFILE_REPLACE_EXISTING` — atomic overwrite of the exe entrypoint
- `inherit_handles=False` — no file handle leaks to ghost
