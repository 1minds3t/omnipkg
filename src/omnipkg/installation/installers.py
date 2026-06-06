"""
omnipkg/installation/installers.py

Modular installer — replaces the _run_pip_install monolith in core.py.

Call site in core.py (drop-in, identical signature):

    def _run_pip_install(self, packages, force_reinstall=False,
                         target_directory=None, extra_flags=None,
                         index_url=None, extra_index_url=None):
        from .installation.installers import ModularInstaller
        return ModularInstaller(self).install(
            packages=packages,
            force_reinstall=force_reinstall,
            target_directory=target_directory,
            extra_flags=extra_flags,
            index_url=index_url,
            extra_index_url=extra_index_url,
        )

ModularInstaller receives the live, pre-warmed OmniPkg core instance so
_uv_exe_cached / _uv_ffi_run / config are all already initialised — zero
re-init cost, daemon pre-load still benefits everything.

Staircase order:  FFI → daemon run_uv → subprocess uv → pip subprocess
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Thread-local storage for plan-callback intercept results
# ---------------------------------------------------------------------------
# When the uv-ffi plan callback fires (in smart_install._plan_callback) and
# handles a bubble swap atomically, it sets these so _try_ffi can report the
# correct installed/removed lists to the outer start_op/end_op wrapper instead
# of the empty lists uv itself returns (it saw nothing to do on disk).
_tls = threading.local()

def _set_callback_result(installed: list, removed: list) -> None:
    """Called by the plan callback after a successful intercept."""
    _tls.cb_installed = installed
    _tls.cb_removed   = removed

def _pop_callback_result():
    """Returns (installed, removed) from last callback intercept and clears it."""
    inst = getattr(_tls, 'cb_installed', None)
    rem  = getattr(_tls, 'cb_removed',  None)
    _tls.cb_installed = None
    _tls.cb_removed   = None
    return inst, rem

# ---------------------------------------------------------------------------
# Public re-exports used by the call site
# ---------------------------------------------------------------------------

__all__ = ["ModularInstaller"]


# ---------------------------------------------------------------------------
# Outcome enum  (3 states — not 2)
# ---------------------------------------------------------------------------

class InstallOutcome(Enum):
    SUCCESS = "success"
    FAILED  = "failed"   # tried, got a real error — stop chain
    SKIP    = "skip"     # not applicable for this backend — try next


# ---------------------------------------------------------------------------
# Internal request bag  (built once, passed to every backend)
# ---------------------------------------------------------------------------

@dataclass
class _Req:
    packages:          List[str]
    force_reinstall:   bool
    target_directory:  Optional[Path]
    extra_flags:       List[str]
    index_url:         Optional[str]
    extra_index_url:   Optional[str]
    # filled by ModularInstaller._prepare()
    uv_args:           List[str]    = field(default_factory=list)
    uv_packages:       List[str]    = field(default_factory=list)
    is_isolated:       bool         = False   # True  → --target install, suppress signaling


# ---------------------------------------------------------------------------
# ModularInstaller
# ---------------------------------------------------------------------------

class ModularInstaller:
    """
    Owns the full install lifecycle:
      - uv-args construction (once, shared across all paths)
      - start_op / end_op daemon signaling (hoisted here, not buried in FFI path)
      - staircase dispatch: FFI → daemon → uv-subprocess → pip-subprocess
      - post-install fixups: numpy ABI, invalid-distribution heal,
        time-machine legacy-build fallback, RECORD-corruption recovery
    """

    def __init__(self, core):
        self._core   = core          # pre-warmed OmniPkg instance
        self._config = core.config
        self._dbg    = os.environ.get("OMNIPKG_DEBUG") == "1"

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    def install(
        self,
        packages:         List[str],
        force_reinstall:  bool                = False,
        target_directory: Optional[Path]      = None,
        extra_flags:      Optional[List[str]] = None,
        index_url:        Optional[str]       = None,
        extra_index_url:  Optional[str]       = None,
    ) -> Tuple[int, Dict]:

        # ── safe_print / i18n helpers live on the core module namespace ──
        _safe_print = self._core_attr("safe_print", print)
        _           = self._core_attr("_",           lambda x: x)

        if not packages:
            return 0, {"stdout": "", "stderr": ""}

        # ── 1. auto-detect special index (torch cuda builds, etc.) ──────
        index_url, extra_index_url = self._detect_index(
            packages, index_url, extra_index_url, _safe_print, _
        )

        # ── 2. ensure uv toolchain is cached on core (idempotent) ───────
        self._ensure_uv_cache()

        req = _Req(
            packages        = packages,
            force_reinstall = force_reinstall,
            target_directory= target_directory,
            extra_flags     = extra_flags or [],
            index_url       = index_url,
            extra_index_url = extra_index_url,
            is_isolated     = bool(target_directory),
        )

        uv_exe = getattr(self._core, "_uv_exe_cached", "")
        if uv_exe:
            self._build_uv_args(req, uv_exe)

        # ── 3. signaling: START (wraps entire chain) ─────────────────────
        daemon_client, op_marker = None, None
        if not req.is_isolated:
            daemon_client, op_marker = self._signal_start(_safe_print)

        # ── 4. staircase dispatch ────────────────────────────────────────
        result      = None
        outcome     = InstallOutcome.SKIP
        installed   = []
        removed     = []

        try:
            if uv_exe:
                # PATH 1 — FFI in-process
                outcome, result, installed, removed = self._try_ffi(req, _safe_print)
                if outcome == InstallOutcome.SKIP:
                    # PATH 2 — daemon run_uv (IPC)
                    outcome, result = self._try_daemon(req, uv_exe, _safe_print)
                if outcome == InstallOutcome.SKIP:
                    # PATH 3 — uv subprocess
                    outcome, result = self._try_uv_subprocess(req, uv_exe, _safe_print)

            if outcome == InstallOutcome.SKIP:
                # PATH 4 — pip subprocess (always available, always last)
                outcome, result = self._try_pip(req, _safe_print, _)

        finally:
            # ── 5. signaling: END (always fires, even on exception) ──────
            if not req.is_isolated:
                self._signal_end(daemon_client, op_marker, installed, removed)

        if result is None:
            # all paths skipped — shouldn't happen, but be safe
            return 1, {"stdout": "", "stderr": "all install backends skipped"}

        # ── 6. inject setuptools into bubbles that need it ───────────────
        # triton (torch dep) and pytorch-lightning import setuptools/importlib.metadata
        # at import time — not listed as deps so uv never pulls them in.
        # Inject once, silently, after a successful isolated install.
        _NEEDS_SETUPTOOLS = {"torch", "triton", "pytorch-lightning", "pytorch_lightning"}
        if (
            req.is_isolated
            and target_directory is not None
            and isinstance(result, tuple) and result[0] == 0
        ):
            _pkg_names = {p.split("==")[0].split(">=")[0].split("[")[0].lower().replace("-", "_")
                          for p in packages}
            if _pkg_names & {n.replace("-", "_") for n in _NEEDS_SETUPTOOLS}:
                try:
                    import subprocess as _sp
                    _sp.run(
                        [sys.executable, "-m", "pip", "install",
                         "--target", str(target_directory), "--no-deps", "--quiet",
                         "setuptools"],
                        check=False, capture_output=True,
                    )
                except Exception:
                    pass  # best-effort, never block the install

        return result

    # ------------------------------------------------------------------ #
    #  PATH 1 — FFI in-process                                            #
    # ------------------------------------------------------------------ #

    def _try_ffi(self, req: _Req, sp) -> Tuple[InstallOutcome, Optional[Tuple], list, list]:
        """Returns (outcome, result_tuple, installed, removed)."""
        ffi_run = getattr(self._core, "_uv_ffi_run", None)
        if ffi_run is None:
            sp("[UV-PATH] FFI skipped (unavailable) — trying daemon", file=sys.stderr)
            return InstallOutcome.SKIP, None, [], []

        if "--no-reinstall" in req.extra_flags:
            sp("[UV-PATH] FFI skipped (--no-reinstall) — trying daemon", file=sys.stderr)
            return InstallOutcome.SKIP, None, [], []

        installed, removed = [], []
        _t0 = time.perf_counter()

        try:
            from omnipkg.isolation.fs_watcher import FfiWriteGuard
            watcher_guard = FfiWriteGuard.attach()
        except Exception:
            watcher_guard = contextlib.nullcontext()

        ffi_cmd    = " ".join(req.uv_args)
        _py_target = next(
            (a for i, a in enumerate(req.uv_args) if req.uv_args[i-1] == "--python"),
            "NOT SET"
        )
        sp(f"[UV-PATH] FFI in-process: uv {ffi_cmd}", file=sys.stderr)
        self._dbg_print(
            f"[UV-FFI-IDENTITY] .so={getattr(self._core, '_uv_ffi_so_path', 'cached')} "
            f"| worker={sys.executable} | targeting --python={_py_target}",
            file=sys.stderr
        )

        # fd-level stderr capture (catches Rust writes through the fd)
        ffi_stderr     = ""
        stderr_fd      = None
        old_stderr_fd  = None
        tmp_stderr_f   = None
        try:
            stderr_fd = sys.stderr.fileno()
        except Exception:
            pass
        if stderr_fd is not None:
            try:
                old_stderr_fd = os.dup(stderr_fd)
                tmp_stderr_f  = tempfile.TemporaryFile(mode="w+b")
                os.dup2(tmp_stderr_f.fileno(), stderr_fd)
            except Exception:
                old_stderr_fd = None
                tmp_stderr_f  = None

        ffi_rc = ffi_err = None
        try:
            with watcher_guard:
                ffi_rc, installed, removed, ffi_err = ffi_run(ffi_cmd)
        finally:
            if old_stderr_fd is not None:
                os.dup2(old_stderr_fd, stderr_fd)
                os.close(old_stderr_fd)
            if tmp_stderr_f is not None:
                tmp_stderr_f.seek(0)
                ffi_stderr = tmp_stderr_f.read().decode("utf-8", errors="replace")
                tmp_stderr_f.close()
                sys.stderr.write(ffi_stderr)

        ffi_ms = (time.perf_counter() - _t0) * 1000
        self._dbg_print(f"[UV-TIMING] FFI: {ffi_ms:.2f}ms rc={ffi_rc}")

        if ffi_rc == 0:
            # If a plan callback intercepted the swap, use its installed/removed
            # instead of uv's (which will be empty since uv saw nothing to do).
            cb_inst, cb_rem = _pop_callback_result()
            if cb_inst is not None:
                installed, removed = cb_inst, cb_rem

            # proactive KB patch
            if not req.is_isolated and (installed or removed):
                try:
                    from omnipkg.isolation.fs_watcher import DaemonPatchSender
                    ps = DaemonPatchSender(self._config.get_daemon_socket())
                    ps(
                        site_packages_path=self._config.get("site_packages_path"),
                        installed=installed,
                        removed=removed,
                    )
                except Exception:
                    pass

            result = (0, {
                "stdout": "", "stderr": "",
                "ffi_installed": installed, "ffi_removed": removed,
                "from_ffi": True, "is_target": req.is_isolated,
            })
            return InstallOutcome.SUCCESS, result, installed, removed

        detailed = f"{ffi_err}\n{ffi_stderr}".strip()
        sp(f"   ⚠️  FFI failed (rc={ffi_rc}): {detailed} — trying daemon", file=sys.stderr)
        # FFI tried and failed with a real error — but we still fall through
        # (daemon / subprocess may succeed where in-process FFI choked, e.g.
        #  network proxy env vars only visible to child processes).
        return InstallOutcome.SKIP, None, installed, removed

    # ------------------------------------------------------------------ #
    #  PATH 2 — daemon run_uv (IPC)                                       #
    # ------------------------------------------------------------------ #

    def _try_daemon(self, req: _Req, uv_exe: str, sp) -> Tuple[InstallOutcome, Optional[Tuple]]:
        _t0 = time.perf_counter()
        try:
            from omnipkg.isolation.worker_daemon import DaemonClient, DEFAULT_SOCKET
            dc  = DaemonClient(auto_start=False)
            self._dbg_print(f"[FS-WATCHER-CLIENT] socket_path={DEFAULT_SOCKET}")
            payload = {
                "type":       "run_uv",
                "uv_exe":     uv_exe,
                "uv_args":    req.uv_args,
                "python_exe": self._config.get("python_executable"),
                "env": {k: os.environ[k] for k in
                        ("UV_INDEX_URL", "UV_EXTRA_INDEX_URL",
                         "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
                        if k in os.environ},
            }
            sp(f"[UV-PATH] daemon run_uv: uv {' '.join(req.uv_args)}", file=sys.stderr)
            res    = dc._send(payload)
            ms     = (time.perf_counter() - _t0) * 1000

            if res.get("status") == "COMPLETED":
                rc  = res.get("exit_code", 0)
                out = res.get("stdout", "")
                err = res.get("stderr", "")
                sp(f"[UV-TIMING] daemon: {ms:.2f}ms rc={rc}", file=sys.stderr)
                if rc == 0:
                    sp(out, end="")
                    sp(err, end="", file=sys.stderr)
                    return InstallOutcome.SUCCESS, (rc, {"stdout": out, "stderr": err})
                sp(f"   ⚠️  daemon rc={rc} — falling through to subprocess", file=sys.stderr)
                return InstallOutcome.SKIP, None   # non-zero → let subprocess try

            sp(f"[UV-PATH] daemon failed ({res.get('error')}) after {ms:.2f}ms — subprocess fallback",
               file=sys.stderr)
            return InstallOutcome.SKIP, None

        except Exception as ex:
            ms = (time.perf_counter() - _t0) * 1000
            sp(f"[UV-PATH] daemon unavailable ({ex}) after {ms:.2f}ms — subprocess fallback",
               file=sys.stderr)
            return InstallOutcome.SKIP, None

    # ------------------------------------------------------------------ #
    #  PATH 3 — uv subprocess                                             #
    # ------------------------------------------------------------------ #

    def _try_uv_subprocess(self, req: _Req, uv_exe: str, sp) -> Tuple[InstallOutcome, Optional[Tuple]]:
        _t0    = time.perf_counter()
        uv_cmd = [uv_exe] + req.uv_args
        sp(f"[UV-PATH] subprocess: {' '.join(uv_cmd)}", file=sys.stderr)

        try:
            try:
                from omnipkg.isolation.fs_watcher import FfiWriteGuard
                guard = FfiWriteGuard.attach()
            except Exception:
                guard = contextlib.nullcontext()

            with guard:
                proc = subprocess.Popen(
                    uv_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
                out_lines, err_lines = [], []
                for line in proc.stdout:
                    sp(line, end="")
                    out_lines.append(line)
                for line in proc.stderr:
                    sp(line, end="", file=sys.stderr)
                    err_lines.append(line)
                proc.wait()

            ms         = (time.perf_counter() - _t0) * 1000
            stderr_str = "".join(err_lines)
            sp(f"[UV-TIMING] subprocess: {ms:.2f}ms rc={proc.returncode}", file=sys.stderr)

            failure_det = getattr(self._core, "_uv_failure_detector", None)
            if proc.returncode == 0 and (
                failure_det is None or not failure_det.detect_failure(stderr_str)
            ):
                return InstallOutcome.SUCCESS, (
                    0, {"stdout": "".join(out_lines), "stderr": stderr_str}
                )

            sp("   ⚠️  uv subprocess failed, falling back to pip...")
            return InstallOutcome.SKIP, None

        except Exception as ex:
            sp(f"   ⚠️  uv subprocess unavailable ({ex}), falling back to pip...")
            return InstallOutcome.SKIP, None

    # ------------------------------------------------------------------ #
    #  PATH 4 — pip subprocess (always-available final fallback)          #
    # ------------------------------------------------------------------ #

    def _try_pip(self, req: _Req, sp, _) -> Tuple[InstallOutcome, Optional[Tuple]]:
        cmd = [
            self._config["python_executable"],
            "-u", "-m", "pip", "install", "--no-cache-dir",
        ]
        if req.index_url:
            cmd += ["--index-url", req.index_url]
        if req.extra_index_url:
            cmd += ["--extra-index-url", req.extra_index_url]
        if req.extra_flags:
            cmd += req.extra_flags
        if req.force_reinstall:
            cmd.append("--upgrade")
        if req.target_directory:
            sp(_("   - Targeting installation to: {}").format(req.target_directory))
            cmd += ["--target", str(req.target_directory)]
        cmd += req.packages

        env = os.environ.copy()
        if req.target_directory:
            env.pop("PIP_PREFIX", None)

        try:
            try:
                from omnipkg.isolation.fs_watcher import FfiWriteGuard
                guard = FfiWriteGuard.attach()
            except Exception:
                guard = contextlib.nullcontext()

            with guard:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    bufsize=1, universal_newlines=True, env=env,
                )
                out_lines, err_lines = [], []
                for line in proc.stdout:
                    sp(line, end="")
                    out_lines.append(line)
                for line in proc.stderr:
                    sp(line, end="", file=sys.stderr)
                    err_lines.append(line)
                return_code = proc.wait()

            full_stdout    = "".join(out_lines)
            full_stderr    = "".join(err_lines)
            captured       = {"stdout": full_stdout, "stderr": full_stderr}
            full_output    = full_stdout + full_stderr

            # ── numpy ABI fix (--target installs only) ─────────────────
            if return_code == 0 and req.target_directory and req.packages:
                self._fix_numpy_abi(req, sp, _)

            # ── heal invalid distributions ──────────────────────────────
            cleanup_path = (
                req.target_directory
                if req.target_directory
                else Path(self._config.get("site_packages_path"))
            )
            self._core._auto_heal_invalid_distributions(full_output, cleanup_path)

            if return_code != 0:
                # time-machine legacy-build fallback
                if "metadata-generation-failed" in full_output.lower():
                    if req.packages and "==" in req.packages[0]:
                        pkg_name, pkg_ver = self._core._parse_package_spec(req.packages[0])
                        sp("\n" + "=" * 60)
                        sp(f"🕰️  TIME MACHINE: Detected legacy build failure for {pkg_name}=={pkg_ver}.")
                        sp("   - This is common for old packages with modern build tools.")
                        sp("=" * 60)
                        if self._core._run_historical_install_fallback(
                            pkg_name, pkg_ver,
                            target_directory_override=req.target_directory,
                            index_url=req.index_url,
                            extra_index_url=req.extra_index_url,
                        ):
                            sp(f"\n   ✅ TIME MACHINE: Successfully rebuilt {pkg_name}=={pkg_ver} from the past.")
                            return InstallOutcome.SUCCESS, (0, captured)
                        sp(_('\n   ❌ TIME MACHINE: Failed to rebuild {}=={}. The original error follows.').format(pkg_name, pkg_ver))

                # no-compatible-version error
                no_dist = (
                    "no matching distribution found" in full_output.lower()
                    or "could not find a version that satisfies" in full_output.lower()
                )
                if no_dist:
                    spec = req.packages[0]
                    pkg_name = spec.split("==")[0].split(">=")[0].split("<=")[0].split(">")[0].split("<")[0].strip()
                    if "==" in spec:
                        sp("\n❌ The specified version does not exist")
                        sp(_('💡 Package: {}').format(pkg_name))
                        sp(_('💡 Requested: {}').format(spec))
                        if req.index_url:
                            sp(_('💡 Searched in: {}').format(req.index_url))
                        sp("💡 Check pip output above for available versions")
                        return InstallOutcome.FAILED, (1, captured)
                    else:
                        # import here so we don't need it at module level
                        from omnipkg.exceptions import NoCompatiblePythonError
                        raise NoCompatiblePythonError(
                            package_name=pkg_name,
                            current_python=self._core.current_python_context,
                            message=f"No compatible version found for Python {self._core.current_python_context}",
                        )

                # RECORD corruption recovery
                m = re.search(r"no RECORD file was found for ([\w\-]+)", full_output)
                if m:
                    pkg_name = m.group(1)
                    sp("\n" + "=" * 60)
                    sp(_("🛡️  AUTO-RECOVERY: Detected corrupted package '{}'.").format(pkg_name))
                    if self._core._brute_force_package_cleanup(pkg_name, cleanup_path):
                        sp(_("   - Retrying installation on clean environment..."))
                        retry = subprocess.run(
                            cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env,
                        )
                        if retry.returncode == 0:
                            sp(retry.stdout)
                            sp(_("   - ✅ Recovery successful!"))
                            return InstallOutcome.SUCCESS, (0, {"stdout": retry.stdout, "stderr": retry.stderr})
                        sp(_("   - ❌ Recovery failed. Pip error after cleanup:"))
                        sp(retry.stderr)
                        return InstallOutcome.FAILED, (1, {"stdout": retry.stdout, "stderr": retry.stderr})
                    return InstallOutcome.FAILED, (1, captured)

                return InstallOutcome.FAILED, (return_code, captured)

            return InstallOutcome.SUCCESS, (0, captured)

        except Exception as ex:
            # let NoCompatiblePythonError propagate — it's caught by the caller
            raise

    # ------------------------------------------------------------------ #
    #  Signaling helpers                                                   #
    # ------------------------------------------------------------------ #

    def _signal_start(self, sp):
        """Touch op marker + notify daemon. Returns (daemon_client, op_marker)."""
        daemon_client = None
        op_marker     = None
        try:
            sp_path   = Path(self._config.get("site_packages_path"))
            op_marker = sp_path / ".omnipkg_op.lock"
            op_marker.touch()
            from omnipkg.isolation.worker_daemon import DaemonClient, DEFAULT_SOCKET
            daemon_client = DaemonClient(auto_start=False)
            self._dbg_print(f"[FS-WATCHER-CLIENT] socket_path={DEFAULT_SOCKET}")
            result = daemon_client.start_omnipkg_op()
            self._dbg_print(f"[FS-WATCHER-CLIENT] start_op result={result} for {sp_path}")
        except Exception as ex:
            self._dbg_print(f"[FS-WATCHER-CLIENT] Failed to signal start_op: {ex}")
            daemon_client = None
        return daemon_client, op_marker

    def _signal_end(self, daemon_client, op_marker, installed, removed):
        """Notify daemon of completion + remove op marker. Always called from finally."""
        if daemon_client:
            self._dbg_print(f"[CORE-SENDER] Sending to Daemon: INST={installed}, REM={removed}")
            try:
                daemon_client.end_omnipkg_op(installed=installed, removed=removed)
            except Exception as ex:
                self._dbg_print(f"[FS-WATCHER-CLIENT] Failed to signal end_op: {ex}")
        if op_marker and op_marker.exists():
            try:
                op_marker.unlink()
            except Exception as ex:
                self._dbg_print(f"[FS-WATCHER-CLIENT] Failed to unlink op_marker: {ex}")

    # ------------------------------------------------------------------ #
    #  uv-args construction (once, shared by paths 1–3)                   #
    # ------------------------------------------------------------------ #

    def _build_uv_args(self, req: _Req, uv_exe: str):
        import platform
        link_mode = self._config.get("uv_link_mode", "copy")

        args = [
            "pip", "install",
            "--cache-dir", getattr(self._core, "_uv_cache_dir", ""),
            "--link-mode", link_mode,
        ]
        if req.index_url:
            args += ["--index-url", req.index_url]
        if req.extra_index_url:
            args += ["--extra-index-url", req.extra_index_url]
        if req.extra_flags:
            args += [
                "--no-reinstall" if f == "--ignore-installed" else f
                for f in req.extra_flags
            ]
        if req.force_reinstall and "--no-reinstall" not in req.extra_flags:
            args.append("--upgrade")
        if req.target_directory:
            if platform.system() == "Windows" and "--link-mode" in args:
                idx = args.index("--link-mode")
                args[idx + 1] = "copy"
            args += ["--target", str(req.target_directory)]

        py_exe = self._config.get("python_executable", "")
        if py_exe and os.path.exists(py_exe) and "--python" not in args:
            args += ["--python", py_exe]

        # Strip PEP 440 local version tags (+cu118 etc.) — uv can't resolve them.
        # Promote matching extra-index-url → index-url so uv fetches the right wheel.
        uv_pkgs = []
        for p in req.packages:
            m = re.match(r'^(.+==[\d.]+)\+([A-Za-z0-9_.]+)$', p)
            if m:
                uv_pkgs.append(m.group(1))
                local_tag = m.group(2)
                if "--extra-index-url" in args:
                    ei_idx = args.index("--extra-index-url")
                    ei_val = args[ei_idx + 1]
                    if local_tag in ei_val:
                        args.pop(ei_idx + 1)
                        args.pop(ei_idx)
                        if "--index-url" not in args:
                            args.insert(2, ei_val)
                            args.insert(2, "--index-url")
            else:
                uv_pkgs.append(p)

        args += uv_pkgs
        req.uv_args     = args
        req.uv_packages = uv_pkgs

    # ------------------------------------------------------------------ #
    #  Index auto-detection (torch cuda builds etc.)                      #
    # ------------------------------------------------------------------ #

    def _detect_index(self, packages, index_url, extra_index_url, sp, _):
        if index_url or extra_index_url:
            return index_url, extra_index_url
        if not hasattr(self._core, "package_index_registry"):
            from omnipkg.installation.package_index_registry import PackageIndexRegistry
            self._core.package_index_registry = PackageIndexRegistry(
                self._core.multiversion_base.parent
            )
        name, ver = self._core._parse_package_spec(packages[0])
        det_idx, det_extra = self._core.package_index_registry.detect_index_url(name, ver)
        if det_idx:
            sp(f"   🔍 Auto-detected special variant for {name}")
            sp(_('   🎯 Using index: {}').format(det_idx))
            index_url = det_idx
        if det_extra:
            sp(_('   🔍 Auto-detected extra index: {}').format(det_extra))
            extra_index_url = det_extra
        return index_url, extra_index_url

    # ------------------------------------------------------------------ #
    #  uv toolchain cache init (idempotent, mirrors current core logic)   #
    # ------------------------------------------------------------------ #

    def _ensure_uv_cache(self):
        """Populate _uv_exe_cached / _uv_cache_dir / _uv_ffi_run on core if not yet done."""
        core = self._core
        if hasattr(core, "_uv_exe_cached"):
            return

        import shutil as _sh
        exe = self._config.get("uv_executable") or _sh.which("uv") or ""
        if exe and not os.path.isabs(exe):
            exe = _sh.which(exe) or exe
        if not exe or not os.path.exists(exe):
            bundled = os.path.join(os.path.dirname(sys.executable), "uv")
            if os.path.exists(bundled):
                exe = bundled
        core._uv_exe_cached = exe if (exe and os.path.exists(exe)) else ""

        core._uv_cache_dir = (
            self._config.get("uv_cache_dir")
            or os.path.join(tempfile.gettempdir(), "uv_cache")
        )
        if "UV_TMPDIR" not in os.environ:
            os.environ["UV_TMPDIR"] = tempfile.gettempdir()

        try:
            import omnipkg._vendor.uv_ffi as _ffi
            core._uv_ffi_run    = _ffi.run
            core._uv_ffi_so_path = getattr(_ffi, "_loaded_so_path", "unknown")
        except ImportError:
            core._uv_ffi_run = None

        try:
            from omnipkg.common_utils import UVFailureDetector
            core._uv_failure_detector = UVFailureDetector()
        except ImportError:
            core._uv_failure_detector = None

    # ------------------------------------------------------------------ #
    #  numpy ABI fixup (pip path only, --target installs)                 #
    # ------------------------------------------------------------------ #

    def _fix_numpy_abi(self, req: _Req, sp, _):
        try:
            from omnipkg.installation.dependency_constraints import get_numpy_constraint
        except ImportError:
            return

        spec     = req.packages[0]
        pkg_name = spec.split("==")[0].split(">=")[0].split("<=")[0].strip()
        if "==" not in spec:
            return

        pkg_ver         = spec.split("==")[1]
        numpy_constraint = get_numpy_constraint(pkg_name, pkg_ver)
        if not numpy_constraint:
            return

        check = subprocess.run(
            [self._config["python_executable"], "-c",
             f"import sys; sys.path.insert(0, '{req.target_directory}'); "
             f"import numpy; print(numpy.__version__)"],
            capture_output=True, text=True, timeout=10,
        )
        if check.returncode != 0:
            return

        installed_numpy = check.stdout.strip()
        needs_fix = False
        if "<2" in numpy_constraint and installed_numpy.startswith("2."):
            needs_fix = True
        elif "<1.28" in numpy_constraint:
            from packaging.version import parse as parse_version
            if parse_version(installed_numpy) >= parse_version("1.28"):
                needs_fix = True
        if not needs_fix:
            return

        sp(f"\n⚠️  Detected numpy {installed_numpy} incompatible with {pkg_name} {pkg_ver}")
        sp(_('🔧 Fixing: Installing numpy{}...').format(numpy_constraint))

        import shutil as _sh
        for np_path in req.target_directory.glob("numpy*"):
            if np_path.is_dir():
                _sh.rmtree(np_path)
            else:
                np_path.unlink()

        fix_cmd = [
            self._config["python_executable"], "-m", "pip", "install",
            "--target", str(req.target_directory),
            "--no-cache-dir", "--upgrade",
            f"numpy{numpy_constraint}",
        ]
        if req.index_url:
            fix_cmd += ["--index-url", req.index_url]
        if req.extra_index_url:
            fix_cmd += ["--extra-index-url", req.extra_index_url]

        fix = subprocess.run(fix_cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        if fix.returncode == 0:
            sp("✅ Successfully installed correct numpy version")
        else:
            sp("❌ Failed to fix numpy version")
            sp(fix.stderr)

    # ------------------------------------------------------------------ #
    #  Utilities                                                           #
    # ------------------------------------------------------------------ #

    def _dbg_print(self, msg, **kwargs):
        if self._dbg:
            print(f"[DEBUG-CORE] {msg}", flush=True, **kwargs)

    def _core_attr(self, name, default):
        """Grab a function/attr from the core's module globals, with a fallback."""
        try:
            import sys as _sys
            mod = _sys.modules.get(type(self._core).__module__)
            if mod and hasattr(mod, name):
                return getattr(mod, name)
        except Exception:
            pass
        return getattr(self._core, name, default)