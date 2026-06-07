import os, sys, subprocess, glob

BOOTSTRAP_DIR = r"C:\omnipkg\bootstrap"
C_SRC    = os.path.join(BOOTSTRAP_DIR, "bootstrap.c")
C_EXE    = os.path.join(BOOTSTRAP_DIR, "bootstrap_new.exe")
SELF_EXE = os.path.join(BOOTSTRAP_DIR, "launcher.exe")

def find_cl():
    hits = glob.glob(
        r"C:\Program Files (x86)\Microsoft Visual Studio"
        r"\2022\BuildTools\VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe"
    )
    return hits[0] if hits else None

def get_vs_env():
    vsdevcmd = (
        r"C:\Program Files (x86)\Microsoft Visual Studio"
        r"\2022\BuildTools\Common7\Tools\VsDevCmd.bat"
    )
    r = subprocess.run(
        f'"{vsdevcmd}" -arch=amd64 > nul 2>&1 && set',
        shell=True, capture_output=True, text=True
    )
    env = os.environ.copy()
    for line in r.stdout.splitlines():
        if '=' in line:
            k, unused, v = line.partition('=')
            env[k.strip()] = v.strip()
    return env

def compile_bootstrap():
    cl = find_cl()
    if not cl:
        print("launcher: cl.exe not found.")
        return False
    print(f"launcher: compiling {C_SRC}")
    env = get_vs_env()
    r = subprocess.run(
        [cl, C_SRC, f"/Fe:{C_EXE}", "/nologo", "/link", "kernel32.lib"],
        capture_output=True, text=True, env=env
    )
    print("STDOUT:", r.stdout)
    print("STDERR:", r.stderr)
    print("RC:", r.returncode)
    if r.returncode != 0:
        return False
    print(f"launcher: compiled -> {C_EXE}")
    return True

def spawn_ghost():
    my_pid = os.getpid()
    cmd = [C_EXE, "--ghost", str(my_pid), SELF_EXE]
    subprocess.Popen(
        cmd,
        stdin=None, stdout=None, stderr=None,
        close_fds=True,
        creationflags=0x00000008 | 0x08000000,
    )
    print(f"launcher: ghost spawned, exiting.")
    sys.exit(0)

if __name__ == "__main__":
    print(f"launcher.py (PID {os.getpid()}): Python entrypoint")
    if os.path.exists(C_EXE):
        os.remove(C_EXE)
    if compile_bootstrap():
        spawn_ghost()
    else:
        print("launcher: staying as Python fallback.")
