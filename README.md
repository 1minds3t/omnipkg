<p align="center"> 
  <a href="https://github.com/1minds3t/omnipkg">
    <img src="https://raw.githubusercontent.com/1minds3t/omnipkg/main/.github/logo.svg" alt="omnipkg Logo" width="150">
  </a>
</p>
<h1 align="center">omnipkg — Python Runtime Hypervisor</h1>
<p align="center">
  <strong>Run infinite Python package and interpreter versions concurrently in one environment, in milliseconds.</strong>
</p>

<p align="center">
  <a href="https://github.com/1minds3t/omnipkg/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/License-AGPLv3-d94c31?logo=gnu" alt="License">
  </a>
  <a href="https://pypi.org/project/omnipkg/">
    <img src="https://img.shields.io/pypi/v/omnipkg?color=blue&logo=pypi" alt="PyPI">
  </a>
  <a href="https://anaconda.org/conda-forge/omnipkg">
    <img src="https://img.shields.io/conda/dn/conda-forge/omnipkg?logo=anaconda" alt="Conda Downloads">
  </a>
  <a href="https://pepy.tech/projects/omnipkg">
    <img src="https://static.pepy.tech/personalized-badge/omnipkg?period=total&units=INTERNATIONAL_SYSTEM&left_color=gray&right_color=blue&left_text=downloads" alt="PyPI Downloads">
  </a>
  <a href="https://hub.docker.com/r/1minds3t/omnipkg">
    <img src="https://img.shields.io/docker/pulls/1minds3t/omnipkg?logo=docker" alt="Docker Pulls">
  </a>
  <a href="https://clickpy.clickhouse.com/dashboard/omnipkg">
    <img src="https://img.shields.io/badge/global_reach-93+_countries-228B22?logo=globe" alt="Global Reach">
  </a>
  <a href="https://pypi.org/project/omnipkg/">
    <img src="https://img.shields.io/pypi/pyversions/omnipkg?logo=python&logoColor=white" alt="Python Versions">
      </a>
    <a href="https://1minds3t.echo-universe.ts.net/omnipkg/">
  <img src="https://img.shields.io/badge/Docs-Live_Interactive_Console-brightgreen?logo=readthedocs&logoColor=white" alt="Interactive Console">
</a>

</p>

<p align="center">
  <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22Security+Audit%22">
    <img src="https://img.shields.io/badge/Security-passing-success?logo=security" alt="Security">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/safety_scan.yml">
    <img src="https://img.shields.io/badge/Safety-passing-success?logo=safety" alt="Safety">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3APylint">
    <img src="https://img.shields.io/badge/Pylint-10/10-success?logo=python" alt="Pylint">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3ACodeQL+Advanced">
    <img src="https://img.shields.io/badge/CodeQL-passing-success?logo=github" alt="CodeQL">
  </a>
</p>

<!-- COMPARISON_STATS_START -->
<p align="center">
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml">
    <img src="https://img.shields.io/badge/omnipkg-2509%20Wins-brightgreen?logo=python&logoColor=white" alt="omnipkg wins">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml">
    <img src="https://img.shields.io/badge/pip-2512%20Failures-red?logo=pypi&logoColor=white" alt="pip failures">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml">
    <img src="https://img.shields.io/badge/uv-2512%20Failures-red?logo=python&logoColor=white" alt="uv failures">
  </a>
</p>
<p align="center">
  <em>Multi-version installation tests run every 3 hours. <a href="https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml">Live results.</a></em>
</p>
<!-- COMPARISON_STATS_END -->

---


## ⚡ Startup: 237µs

`8pkg` doesn't spawn a Python interpreter for every command. A custom C dispatcher
built on `uint64_t` word-loads connects to the daemon socket in under 100µs.

```text
$ hyperfine '8pkg --help' 'bun --help' 'uv --help'

8pkg --help    237.5 µs ± 30.9 µs    [2976 runs]
bun --help     683.2 µs ± 52.0 µs    [1799 runs]
uv --help        4.1 ms ±  0.1 ms     [656 runs]

  8pkg ran  2.88x faster than bun
  8pkg ran 17.12x faster than uv
```

`--help` is served from a compiled static header — zero Python. For real daemon work:

```text
$ hyperfine '8pkg swap rich' 'uv pip install rich'  # both are no-ops, package already satisfied

8pkg swap rich       1.5 ms ± 0.1 ms    [1337 runs]
uv pip install rich  9.0 ms ± 0.2 ms    [305 runs]   (prints "Checked 1 package in 2ms" internally)

  8pkg ran 6.01x faster than uv
```

Both commands find the package already satisfied and do nothing. The 7.5ms gap is
**pure overhead that omnipkg eliminated**: uv spawns a process, parses its CLI via clap,
scans the environment, then tears everything down. omnipkg embeds uv as a persistent
in-process library (`uv-ffi`) — no spawn, no CLI parse, warm site-packages cache.

For actual version switches, omnipkg intercepts uv's resolved install plan via a Rust
callback before any file I/O runs. Python performs an atomic `os.rename()` to swap
the package directories (~0.1ms), returns `True` to short-circuit uv's installer, then
uv does ~2ms of cleanup. The version swap itself is essentially free once the plan arrives.

--- 

## What omnipkg is

omnipkg is a persistent daemon that manages isolated Python worker processes ("bubbles"), each with its own package versions, ABI, and optionally a different Python interpreter. The C dispatcher connects to the daemon socket directly, bypassing Python startup entirely for hot paths.

You can run `torch==1.13.1+cu116` and `torch==2.2.0+cu121` **simultaneously in the same script**, pass GPU tensors between them without ever leaving VRAM, and route through Python 3.9 and 3.12 workers in the same pipeline — all without containers or separate virtualenvs.

→ [Architecture](docs/architecture.md) · [CLI reference](docs/cli_commands_reference.md) · [Getting started](docs/getting_started.md)

---

## The Impossible Pipeline: 3 PyTorch ABIs, One VRAM Buffer

The daemon's **Universal CUDA IPC** passes raw `cudaIpcGetMemHandle` pointers between workers via ctypes, using no PyTorch on the transport path. Data never crosses the PCIe bus.

```text
$ 8pkg demo 11  # test 8: Grand Unified Benchmark

📦 Payload: 3.81 MB float32 tensor
🌊 Pipeline: torch 1.13.1+cu116 → 2.0.1+cu118 → 2.1.0+cu121

MODE 1 — Pickle + subprocess fork:          4540 ms   (baseline)
MODE 2 — CPU shared memory (zero-copy):       24 ms   (185x faster)
MODE 3 — Universal CUDA IPC (VRAM-only):    6.67 ms   (warm workers)

[WARM] GPU Stage 1: torch 1.13.1+cu116    2.40 ms
[WARM] GPU Stage 2: torch 2.0.1+cu118     2.10 ms
[WARM] GPU Stage 3: torch 2.2.0+cu121     2.17 ms
  ↳ Total: 6.67 ms  — TRUE ZERO-COPY across 3 CUDA ABIs
```

This also works **across Python interpreter versions**. Tested up to py3.9 (cu118) → py3.12 (cu130):

```text
[WARM] py3.9  alloc + share    1.64 ms   torch=2.0.1+cu118
[WARM] py3.12 relu→norm→tanh   2.07 ms   torch=2.12.0+cu130
  ↳ Total: 3.71 ms  (757x faster than cold spawn)
```

Run it yourself:
```bash
8pkg demo 11
```

---

## ABI-Aware Backend Injection

When a compiled-package worker boots, omnipkg queries its knowledge base for the numpy ABI range that was baked into that specific build, then pre-injects the correct numpy bubble automatically before any user code runs:

```text
[DAEMON] numpy ABI range from KB: 1.20-1.23 (spec=torch==1.13.1+cu116)
[DAEMON] numpy bubble pre-injected: numpy-1.23.5  [KB-guided (1.23.5 in 1.20-1.23.*)]
```

No manual pinning. No ABI crash on first import. The KB accumulates this mapping for every compiled package omnipkg has ever managed, and the pattern generalizes beyond numpy — anything with a compiled ABI dependency gets the same treatment.

---

## Concurrent Isolation

**10 threads, 3 numpy versions, 0 corruptions:**

```text
🔥 10 threads × 3 swaps each — numpy 1.24.3 / 1.26.4 / 2.3.5 (500×500 float32)

✅ Success Rate:   30/30  (100%)
⚡ Throughput:     502 swaps/sec
🚀 Avg latency:    2.37 ms/swap
✅ Memory integrity verified — no cross-contamination
```

**3 Python interpreters, 3 PyTorch versions, concurrent:**

```text
Sequential baseline (3.9 → 3.10 → 3.11):   3869 ms
Concurrent via daemon:                         21 ms
Speedup:                                      186x
```

Each universe gets its own interpreter binary and torch CUDA build. Data moves between them via shared memory with zero serialization.

---

## Multi-Version Support

[![omnipkg](https://img.shields.io/badge/omnipkg-2509%20Wins-brightgreen?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml) [![pip](https://img.shields.io/badge/pip-2512%20Failures-red?logo=pypi&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml) [![uv](https://img.shields.io/badge/uv-2512%20Failures-red?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml)

pip and uv can only have one version of a package active at a time. omnipkg runs conflicting versions simultaneously in isolated workers. The test matrix above runs every 3 hours against real packages on real PyPI.

---

## Other Capabilities

**Auto-healing runner** — `8pkg run script.py` detects import failures, version conflicts, and C-extension ABI errors at runtime, activates the correct bubble, and re-executes — without touching your main environment. → [docs](docs/auto_healing.md)

**Reproducible environments** — `8pkg export` snapshots your full interpreter registry + bubble state to a content-addressed TOML lock. `8pkg sync` rebuilds from it. SHA is deterministic and path-free, safe to commit. → [docs](docs/reproducible.md)

**Python interpreter management** — omnipkg manages CPython 3.7–3.15 (including pre-releases) inside a single environment. Adopt, switch, and run them concurrently. → [docs](docs/python_hot_swapping.md)

**Environment recovery** — `8pkg revert` restores from last-known-good snapshot when an external tool (`pip`, `uv`) damages your environment.

**24-language i18n** — all output is localized. Auto-detects from system locale, override with `--lang` or `omnipkg config set language zh_CN`.

---

## Install

```bash
# pip / uv
pip install omnipkg
uv pip install omnipkg

# conda-forge
conda install -c conda-forge omnipkg
mamba install -c conda-forge omnipkg

# pixi
pixi add omnipkg

# Docker
docker pull 1minds3t/omnipkg:latest
```

Try it immediately:
```bash
8pkg demo        # interactive demo menu
8pkg demo 11     # full IPC showcase (all transports, warm/cold timing)
8pkg stress-test # concurrent chaos tests
```

---

## Platform Support

omnipkg ships pre-compiled C dispatcher wheels using the stable ABI (`cp37-abi3`):

| Platform | Wheel tag |
|----------|-----------|
| Linux x86_64 (glibc 2.17+) | `manylinux2014_x86_64` |
| Linux aarch64 (glibc 2.17+) | `manylinux2014_aarch64` |
| Linux x86_64 (musl 1.2+) | `musllinux_1_2_x86_64` |
| Linux aarch64 (musl 1.2+) | `musllinux_1_2_aarch64` |
| Windows x64 | `win_amd64` |
| Windows ARM64 | `win_arm64` |
| macOS 11+ (Intel + Apple Silicon) | `macosx_11_0_universal2` |
| Raspberry Pi armv6l | [piwheels](https://www.piwheels.org/project/omnipkg/) |

**Python:** 3.7 – 3.15, all on the same stable ABI wheel. A pure-Python fallback wheel (`py3-none-any`) covers anything else.

**conda-forge:** linux-64, linux-aarch64, linux-ppc64le, osx-64, osx-arm64, win-64 (Python 3.10–3.13)

<details>
<summary>Installation notes for specific distros</summary>

**Ubuntu 24.04+ / Debian 12+ (PEP 668):**
```bash
pip install --break-system-packages omnipkg
# or use a venv
```

**Alpine Linux:**
```bash
apk add --no-cache gcc python3-dev musl-dev linux-headers
pip install --break-system-packages omnipkg
```

**Arch Linux:**
```bash
pip install --break-system-packages omnipkg
```

**Rocky/AlmaLinux 8 (ships Python 3.6):**
```bash
sudo dnf install -y python39 python39-pip
python3.9 -m pip install omnipkg
```
</details>

---

## Documentation & Interactive Console

The docs run against **your local omnipkg daemon** — every code block has a live
"Run Command" button that executes on your machine via a local web bridge.

```bash
8pkg web start   # starts local bridge on port 5000
```

Then open: **https://1minds3t.echo-universe.ts.net/omnipkg/**

Chrome will prompt once to allow local network access — that's the CORS handshake to
your daemon. No data leaves your machine.

The console includes 33+ runnable demos:

- [Getting Started](https://1minds3t.echo-universe.ts.net/omnipkg/getting_started/)
- [Architecture & Performance](https://1minds3t.echo-universe.ts.net/omnipkg/architecture/)
- [CLI Reference](https://1minds3t.echo-universe.ts.net/omnipkg/cli_commands/)
- [Platform Support](https://1minds3t.echo-universe.ts.net/omnipkg/platform_support/)
- [Demos](https://1minds3t.echo-universe.ts.net/omnipkg/demos/)

Or skip the browser entirely:
```bash
8pkg demo        # same demos, terminal UI
8pkg demo 11     # IPC showcase directly
```
---

## License

Dual-licensed:
- **AGPLv3** — open source and academic use ([LICENSE](LICENSE))
- **Commercial** — proprietary and enterprise deployment ([COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md))

Commercial inquiries: [1minds3t@proton.me](mailto:1minds3t@proton.me)

---

## Contributing

Bug reports, pull requests, and translation corrections are welcome.

→ [Open an issue](https://github.com/1minds3t/omnipkg/issues)

```
 ________________________________________________________________
/                                                                \
| pip:    "Version conflicts? New env please!"                   |
| Docker: "Spin up containers for 45s each!"                     |
| venv:   "90s of setup for one Python version!"                 |
|                                                                |
| omnipkg: *runs 3 torch CUDA ABIs across 3 Pythons"             |
|          "through one VRAM buffer in 6.67ms*                   |
|          "Hold my multiverse!"                                 |
\________________________________________________________________/
        \   ^__^
         \  (🐍)\_______
            (__)\       )\/\
                ||----w |
                ||     ||

                ~ omnipkg: The Python Runtime Hypervisor ~
```
