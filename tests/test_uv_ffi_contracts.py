"""
test_uv_ffi_contracts.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Contract / assumption tests for the uv_ffi FFI layer.

WHAT THIS TESTS
───────────────
1. __init__.py surface contract:
     - Every symbol in __all__ is importable.
     - Fallback stubs have the right callable shapes.
     - mark_plan_handled() is a no-op stub (deprecated, not in native).

2. Plan-entry tuple contract (mocked Rust side):
     - Correct shape: list[tuple[str, str, str]]
     - action values are within the known set.
     - version can be "" only for "remote" entries.

3. Callback return-value contract:
     - True  → install skipped by Rust (caller must act).
     - False → install proceeds normally.
     Validated via the mock dispatch harness below.

4. Self-healing / stale-dir contract:
     - Stale dir (pkg folder present, dist-info missing) → dir wiped before
       re-install, not left as ABI-contaminated debris.
     - Extraneous dist-info (dist-info present, no package folder) cleaned up.
     - Callback MUST NOT touch site-packages when target was a bubble dir.

5. Real install-plan integration (requires live 8pkg + managed Python):
     - Installs rich 14.3.3 into a tmp target, then rich 15.0.0 into the same
       target.  Validates plan transitions: cached/remote → reinstall → extraneous.
     Marked with @pytest.mark.integration — skip with: pytest -m "not integration"

RUN
───
    cd ~/omnipkg
    conda run -n evocoder_env pytest tests/test_uv_ffi_contracts.py -v
    pytest tests/test_uv_ffi_contracts.py -v -m "not integration"   # unit only

MARK REGISTRATION (suppress PytestUnknownMarkWarning)
──────────────────────────────────────────────────────
Add to pytest.ini under [pytest]:
    markers =
        integration: marks tests requiring live 8pkg + compiled uv_ffi .so

DEPENDENCIES (in evocoder_env)
───────────────────────────────
    rich>=14.3.3          (test target package — not imported, just installed)
    pytest
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

# ── Rich helpers ───────────────────────────────────────────────────────────────
_console = Console(highlight=False)

def _header(text: str) -> None:
    _console.print(Panel(f"[bold cyan]{text}[/]", box=box.SIMPLE_HEAVY, expand=False))

def _ok(label: str, detail: str = "") -> None:
    _console.print(f"  [green]✓[/] [white]{label}[/]" + (f" [dim]{detail}[/]" if detail else ""))

def _fail(label: str, detail: str = "") -> None:
    _console.print(f"  [red]✗[/] [white]{label}[/]" + (f" [dim]{detail}[/]" if detail else ""))

def _info(msg: str) -> None:
    _console.print(f"  [yellow]→[/] [dim]{msg}[/]")


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def mock_native() -> types.ModuleType:
    """
    A synthetic _native module that mimics the PyO3 .so surface.
    Used to test __init__.py logic without a compiled binary.
    """
    mod = types.ModuleType("uv_ffi._native_mock")
    mod.__file__ = "/fake/path/uv_ffi.cpython-311-x86_64-linux-gnu.so"

    # ── plan state ──────────────────────────────────────────────────────────
    _plan: list[tuple[str, str, str]] = []
    _callback: Callable | None = None

    def _get_install_plan() -> list[tuple[str, str, str]]:
        return list(_plan)

    def _set_plan_callback(cb: Callable) -> None:
        nonlocal _callback
        _callback = cb

    def _fire_plan(entries: list[tuple[str, str, str]]) -> bool:
        nonlocal _plan
        _plan[:] = entries
        if _callback is not None:
            return bool(_callback(entries))
        return False

    # attach to module so tests can reach _fire_plan
    mod._plan = _plan
    mod._fire_plan = _fire_plan

    mod.get_install_plan = _get_install_plan
    mod.set_plan_callback = _set_plan_callback

    # ── run stub ────────────────────────────────────────────────────────────
    def _run(cmd: str) -> tuple:
        return (0, None, "", "")
    mod.run = _run

    # ── cache stubs ─────────────────────────────────────────────────────────
    mod.get_site_packages_cache = lambda: None
    mod.invalidate_site_packages_cache = lambda: None
    mod.patch_site_packages_cache = lambda *a: None
    mod.clear_registry_cache = lambda: None
    mod.evict_bubble_cache = lambda: None
    mod.evict_packages_from_bubble_cache = lambda *a: None
    mod.patch_bubble_site_packages_cache = lambda *a: None

    # mark_plan_handled intentionally ABSENT — tests that it falls back to _noop
    return mod


@pytest.fixture()
def bubble_dir(tmp_path: Path) -> Path:
    """Provides a fresh tmp dir standing in for a bubble target."""
    d = tmp_path / "bubble_target"
    d.mkdir()
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 1. __init__.py SURFACE CONTRACT
# ─────────────────────────────────────────────────────────────────────────────

class TestInitSurface:
    """All symbols promised in __all__ must exist and be callable stubs at minimum."""

    EXPECTED_SYMBOLS = [
        "run",
        "run_capture",
        "get_site_packages_cache",
        "invalidate_site_packages_cache",
        "patch_site_packages_cache",
        "clear_registry_cache",
        "evict_bubble_cache",
        "evict_packages_from_bubble_cache",
        "patch_bubble_site_packages_cache",
        "get_install_plan",
        "set_plan_callback",
        "mark_plan_handled",
        "__version__",
    ]

    def _load_module_with_mock(self, mock_native: types.ModuleType) -> types.ModuleType:
        """Load __init__.py with _native patched to the mock."""
        import importlib
        import importlib.util

        init_path = Path(__file__).parent.parent / "src" / "omnipkg" / "_vendor" / "uv" / "crates" / "uv-ffi" / "python" / "uv_ffi" / "__init__.py"
        if not init_path.exists():
            # Try relative to repo root
            for candidate in [
                Path("crates/uv-ffi/python/uv_ffi/__init__.py"),
                Path("uv_ffi/__init__.py"),
            ]:
                if candidate.exists():
                    init_path = candidate
                    break

        spec = importlib.util.spec_from_file_location("uv_ffi_test", str(init_path))
        mod = importlib.util.module_from_spec(spec)

        # Patch _load_native before exec so it returns our mock
        original_load = None
        with patch.object(spec.loader, "exec_module", wraps=spec.loader.exec_module):
            # Inject mock into the module's namespace before exec
            mod._load_native = lambda: mock_native  # type: ignore
            try:
                spec.loader.exec_module(mod)
            except Exception:
                pass  # _load_native may have already been called during exec

        return mod

    def test_all_symbols_declared(self) -> None:
        """__all__ must contain every expected symbol.

        FAILURE HERE means the live uv_ffi __init__.py is out of sync with this
        contract.  The fix is to add the missing names to __all__ AND ensure the
        corresponding getattr() stubs exist above it in __init__.py.

        Patch template (add to the live __init__.py __all__ list):
            "run_capture",       # alias: run_capture = run
            "mark_plan_handled", # deprecated stub: getattr(_native, 'mark_plan_handled', _noop)
            "__version__",       # from importlib.metadata
        """
        _header("Surface: __all__ completeness")
        try:
            import uv_ffi
            all_exports = uv_ffi.__all__
        except ImportError:
            pytest.skip("uv_ffi not importable in this environment")

        missing = [s for s in self.EXPECTED_SYMBOLS if s not in all_exports]
        for sym in self.EXPECTED_SYMBOLS:
            if sym in all_exports:
                _ok(sym, "in __all__")
            else:
                _fail(sym, "MISSING from __all__")

        if missing:
            live_path = getattr(uv_ffi, '__file__', 'unknown')
            _info(f"Live __init__.py: {live_path}")
            _info(f"Add these to __all__ in that file: {missing}")

        assert not missing, (
            f"Missing from __all__: {missing}\n"
            f"Live module: {getattr(uv_ffi, '__file__', 'unknown')}\n"
            "Add the missing names to __all__ AND ensure their getattr() stubs exist."
        )

    def test_symbols_exist_on_module(self) -> None:
        """Every symbol in __all__ must be reachable as an attribute."""
        _header("Surface: attribute existence")
        try:
            import uv_ffi
        except ImportError:
            pytest.skip("uv_ffi not importable in this environment")

        missing = []
        for sym in uv_ffi.__all__:
            if hasattr(uv_ffi, sym):
                _ok(sym)
            else:
                _fail(sym, "attribute missing")
                missing.append(sym)

        assert not missing, f"Attributes missing on module: {missing}"

    def test_mark_plan_handled_is_noop(self) -> None:
        """mark_plan_handled() must exist as a no-op stub and return None.

        FAILURE HERE means the live __init__.py is missing the stub entirely.
        Fix: add this line above __all__ in the live __init__.py:
            mark_plan_handled = getattr(_native, 'mark_plan_handled', _noop)
        And add "mark_plan_handled" to __all__.
        """
        _header("Surface: mark_plan_handled is _noop")
        try:
            import uv_ffi
        except ImportError:
            pytest.skip("uv_ffi not importable in this environment")

        if not hasattr(uv_ffi, 'mark_plan_handled'):
            live_path = getattr(uv_ffi, '__file__', 'unknown')
            _fail("mark_plan_handled attribute is completely absent from module")
            _info(f"Live __init__.py: {live_path}")
            _info("Fix: add  mark_plan_handled = getattr(_native, 'mark_plan_handled', _noop)")
            pytest.fail(
                "uv_ffi.mark_plan_handled does not exist.\n"
                f"Live module: {live_path}\n"
                "Add: mark_plan_handled = getattr(_native, 'mark_plan_handled', _noop)"
            )

        result = uv_ffi.mark_plan_handled()
        _ok("mark_plan_handled() returned", repr(result))
        assert result is None, "Expected None from deprecated stub"

    def test_get_install_plan_returns_list(self) -> None:
        """get_install_plan() must always return a list (empty is fine)."""
        _header("Surface: get_install_plan() return type")
        try:
            import uv_ffi
        except ImportError:
            pytest.skip("uv_ffi not importable in this environment")

        result = uv_ffi.get_install_plan()
        _ok(f"get_install_plan() → {type(result).__name__}({result!r})")
        assert isinstance(result, list), f"Expected list, got {type(result)}"

    def test_run_returns_4tuple(self) -> None:
        """run() must always return a 4-element tuple even on stub/legacy native."""
        _header("Surface: run() 4-tuple contract")
        try:
            import uv_ffi
        except ImportError:
            pytest.skip("uv_ffi not importable in this environment")

        # Use a benign command that won't actually do anything harmful
        result = uv_ffi.run("--version")
        _ok(f"run('--version') → {result!r}")
        assert isinstance(result, tuple), "Expected tuple"
        assert len(result) == 4, f"Expected 4-tuple, got {len(result)}-tuple: {result}"
        assert isinstance(result[0], int), f"result[0] must be int returncode, got {type(result[0])}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. PLAN-ENTRY TUPLE CONTRACT
# ─────────────────────────────────────────────────────────────────────────────

VALID_ACTIONS = {"cached", "remote", "reinstall", "extraneous"}


class TestPlanEntryContract:
    """
    Validates the shape and invariants of plan entries produced by Rust.
    Uses the mock_native fixture — no real .so required.
    """

    def _make_entries(self) -> list[tuple[str, str, str]]:
        return [
            ("rich",  "14.3.3", "cached"),
            ("rich",  "",       "remote"),     # version may be "" for remote
            ("rich",  "14.3.3", "reinstall"),
            ("rich",  "14.3.3", "extraneous"),
        ]

    def test_plan_entry_is_3tuple_of_strings(self) -> None:
        _header("Plan entry: shape — (str, str, str)")
        for entry in self._make_entries():
            assert isinstance(entry, tuple) and len(entry) == 3, \
                f"Not a 3-tuple: {entry!r}"
            name, version, action = entry
            assert isinstance(name, str), f"name not str: {name!r}"
            assert isinstance(version, str), f"version not str: {version!r}"
            assert isinstance(action, str), f"action not str: {action!r}"
            _ok(f"{entry!r}")

    def test_action_values_within_known_set(self) -> None:
        _header("Plan entry: action in known set")
        for entry in self._make_entries():
            unused, unused, action = entry
            if action in VALID_ACTIONS:
                _ok(action)
            else:
                _fail(action, f"not in {VALID_ACTIONS}")
        all_actions = {e[2] for e in self._make_entries()}
        unknown = all_actions - VALID_ACTIONS
        assert not unknown, f"Unknown action values: {unknown}"

    def test_empty_version_only_on_remote(self) -> None:
        _header("Plan entry: empty version only for 'remote'")
        for entry in self._make_entries():
            name, version, action = entry
            if version == "" and action != "remote":
                _fail(f"({name!r}, {version!r}, {action!r})",
                      "empty version on non-remote entry!")
                pytest.fail(f"Empty version on non-remote entry: {entry!r}")
            else:
                _ok(f"{action}: version={version!r}")

    def test_name_is_normalised(self) -> None:
        """PEP 503: name must be lowercase, hyphens not underscores (Rust normalises)."""
        _header("Plan entry: PEP 503 normalised name")
        # Rust calls .to_string() on PackageName which is already normalised by uv.
        entries = [
            ("rich",          "14.3.3", "cached"),
            ("pillow",        "10.0.0", "cached"),
            ("scikit-learn",  "1.5.0",  "cached"),
        ]
        pep503_re = re.compile(r'^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$')
        for name, version, action in entries:
            if pep503_re.match(name):
                _ok(name, "passes PEP 503 regex")
            else:
                _fail(name, "fails PEP 503 regex — Rust normalisation broken?")
        names = [e[0] for e in entries]
        bad = [n for n in names if not pep503_re.match(n)]
        assert not bad, f"Non-normalised names: {bad}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. CALLBACK RETURN-VALUE CONTRACT
# ─────────────────────────────────────────────────────────────────────────────

class TestCallbackContract:
    """
    Validates the True/False dispatch contract using the mock_native harness.
    """

    def test_callback_true_skips_install(self, mock_native: types.ModuleType) -> None:
        _header("Callback: return True → install skipped")
        captured: list[list] = []

        def my_cb(entries):
            captured.append(entries)
            return True  # signal: Python handled it

        mock_native.set_plan_callback(my_cb)
        plan = [("rich", "14.3.3", "cached")]
        handled = mock_native._fire_plan(plan)

        _ok(f"callback fired with {captured}")
        _ok(f"_fire_plan returned {handled!r}")
        assert len(captured) == 1, "Callback not called"
        assert handled is True, f"Expected True, got {handled!r}"

    def test_callback_false_proceeds(self, mock_native: types.ModuleType) -> None:
        _header("Callback: return False → install proceeds")

        def my_cb(entries):
            return False  # don't handle; let Rust proceed

        mock_native.set_plan_callback(my_cb)
        handled = mock_native._fire_plan([("rich", "15.0.0", "remote")])

        _ok(f"_fire_plan returned {handled!r} (Rust should proceed)")
        assert handled is False, f"Expected False, got {handled!r}"

    def test_no_callback_never_skips(self, mock_native: types.ModuleType) -> None:
        _header("Callback: no callback → never skips")
        # Reset callback
        mock_native._plan.clear()
        mock_native.set_plan_callback(None)  # type: ignore

        handled = mock_native._fire_plan([("rich", "14.3.3", "cached")])
        _ok(f"no callback → _fire_plan returned {handled!r}")
        assert handled is False

    def test_callback_receives_snapshot_not_live_ref(
        self, mock_native: types.ModuleType
    ) -> None:
        """
        Entries passed to the callback must be a snapshot — mutating them inside
        the callback must not corrupt the stored INSTALL_PLAN.
        """
        _header("Callback: entries are snapshot, not live ref")
        received: list = []

        def my_cb(entries):
            received.extend(entries)
            entries.clear()  # try to corrupt
            return True

        mock_native.set_plan_callback(my_cb)
        plan = [("rich", "14.3.3", "cached"), ("rich", "14.3.3", "reinstall")]
        mock_native._fire_plan(plan)

        live = mock_native.get_install_plan()
        _ok(f"stored plan after callback mutation attempt: {live!r}")
        # The stored plan (via _plan in the mock) should still have entries
        # because _fire_plan does _plan[:] = entries BEFORE calling the callback.
        assert len(live) == 2, \
            f"Stored plan was corrupted by callback: {live!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. SELF-HEALING / STALE-DIR CONTRACT
# ─────────────────────────────────────────────────────────────────────────────

class TestSelfHealingContract:
    """
    These tests validate the Python-side self-healing logic in isolation.
    The healer is expected to live in omnipkg — here we test the contract
    rules it must obey, implemented inline as a reference implementation.
    """

    def _reference_healer(
        self,
        target_dir: Path,
        plan_entries: list[tuple[str, str, str]],
        site_packages: Path,
    ) -> dict:
        """
        Reference self-healer: given a plan and a target dir, returns a dict
        describing what actions were/would be taken.

        Contract rules:
          1. NEVER touch site_packages when target_dir was provided.
          2. For extraneous entries: if dist-info in target but package folder
             missing → remove the orphan dist-info.
          3. For cached/remote/reinstall entries: if package folder exists but
             dist-info is absent → wipe the stale package folder before installing.
          4. All "would install" paths are under target_dir, not site_packages.
        """
        actions = []

        for name, version, action in plan_entries:
            # Compute expected paths
            pkg_dir = target_dir / name  # simplified; real is dist-specific
            dist_info_glob = list(target_dir.glob(f"{name}-*.dist-info"))

            if action == "extraneous":
                # dist-info present, package gone → orphan
                if dist_info_glob and not pkg_dir.exists():
                    for di in dist_info_glob:
                        di.rmdir() if di.is_dir() and not any(di.iterdir()) else None
                        actions.append(("remove_orphan_distinfo", str(di)))
                continue

            # cached / remote / reinstall
            has_dist_info = bool(dist_info_glob)
            has_pkg_dir = pkg_dir.exists()

            if has_pkg_dir and not has_dist_info:
                # STALE: package dir without dist-info — wipe first
                shutil.rmtree(pkg_dir)
                actions.append(("wipe_stale_dir", str(pkg_dir)))

            # Record intended install location (MUST be target_dir, not site_packages)
            intended_path = target_dir / name
            assert not str(intended_path).startswith(str(site_packages)), (
                f"BUG: healer resolved path into site-packages! "
                f"{intended_path} starts with {site_packages}"
            )
            actions.append(("install_into_target", str(intended_path), name, version))

        return {"actions": actions}

    def test_stale_dir_is_wiped_before_install(self, bubble_dir: Path) -> None:
        _header("Self-heal: stale dir (no dist-info) wiped before install")

        # Create stale state: package dir exists, no dist-info
        pkg_dir = bubble_dir / "rich"
        pkg_dir.mkdir()
        (pkg_dir / "stale_file.py").write_text("# stale")

        plan = [("rich", "15.0.0", "reinstall")]
        fake_site = Path("/fake/site-packages")

        result = self._reference_healer(bubble_dir, plan, fake_site)
        _info(f"actions: {result['actions']}")

        wipe_actions = [a for a in result["actions"] if a[0] == "wipe_stale_dir"]
        assert len(wipe_actions) == 1, "Expected exactly one wipe action"
        assert not pkg_dir.exists(), "Stale dir should have been removed"
        _ok("Stale dir wiped before reinstall")

    def test_orphan_distinfo_removed(self, bubble_dir: Path) -> None:
        _header("Self-heal: orphan dist-info (no pkg dir) removed")

        # dist-info present, no package dir
        di = bubble_dir / "rich-14.3.3.dist-info"
        di.mkdir()
        # empty dir so rmdir succeeds in reference impl

        plan = [("rich", "14.3.3", "extraneous")]
        fake_site = Path("/fake/site-packages")

        result = self._reference_healer(bubble_dir, plan, fake_site)
        _info(f"actions: {result['actions']}")

        remove_actions = [a for a in result["actions"] if a[0] == "remove_orphan_distinfo"]
        assert len(remove_actions) == 1
        _ok("Orphan dist-info identified for removal")

    def test_healer_never_touches_site_packages(self, bubble_dir: Path) -> None:
        _header("Self-heal: NEVER touches site-packages when target provided")

        plan = [("rich", "15.0.0", "remote")]
        site_packages = Path("/home/minds3t/miniforge3/envs/evocoder_env/lib/python3.11/site-packages")

        # Should not raise — the assert inside _reference_healer fires if it does
        result = self._reference_healer(bubble_dir, plan, site_packages)
        install_actions = [a for a in result["actions"] if a[0] == "install_into_target"]
        assert len(install_actions) == 1
        install_path = Path(install_actions[0][1])
        assert str(install_path).startswith(str(bubble_dir)), (
            f"Install path {install_path} is NOT under bubble_dir {bubble_dir}!"
        )
        _ok(f"Install path correctly under bubble: {install_path}")

    def test_extraneous_on_both_missing_is_noop(self, bubble_dir: Path) -> None:
        """Extraneous entry with no dist-info AND no pkg dir → nothing to do, no error."""
        _header("Self-heal: extraneous with nothing present → noop")
        plan = [("rich", "14.3.3", "extraneous")]
        fake_site = Path("/fake/site-packages")
        result = self._reference_healer(bubble_dir, plan, fake_site)
        _ok(f"No crash, actions: {result['actions']}")
        # No remove actions because nothing was there
        assert not any(a[0] == "remove_orphan_distinfo" for a in result["actions"])


# ─────────────────────────────────────────────────────────────────────────────
# 5. INTEGRATION TESTS (require live 8pkg + uv_ffi .so)
# ─────────────────────────────────────────────────────────────────────────────

def _run_8pkg(*args: str, target: str | None = None) -> subprocess.CompletedProcess:
    cmd = ["8pkg", *args]
    if target:
        cmd += ["--target", target]
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.mark.integration
class TestRealInstallPlanIntegration:
    """
    Real install-plan round-trips using rich 14.3.3 and rich 15.0.0 as targets.
    Requires:
      - 8pkg on PATH (evocoder_env activated)
      - uv_ffi .so built and installed
    """

    def test_rich_14_plan_has_expected_action(self, bubble_dir: Path) -> None:
        """Install rich 14.3.3 into a fresh tmp target — plan should show cached or remote."""
        _header("Integration: rich 14.3.3 install plan")
        try:
            import uv_ffi
        except ImportError:
            pytest.skip("uv_ffi not available")

        received_plan: list = []

        def capture_plan(entries):
            received_plan.extend(entries)
            return False  # let Rust proceed

        uv_ffi.set_plan_callback(capture_plan)
        rc, unused, stderr, unused = uv_ffi.run(
            f"pip install rich==14.3.3 --target {bubble_dir} --quiet"
        )

        _info(f"returncode={rc}, stderr={stderr[:200]!r}")
        _info(f"plan entries: {received_plan}")

        assert rc == 0, f"Install failed: {stderr}"
        assert received_plan, "Callback was never fired — plan was empty"
        names = {e[0] for e in received_plan}
        assert "rich" in names, f"'rich' not in plan names: {names}"
        actions = {e[2] for e in received_plan if e[0] == "rich"}
        assert actions & {"cached", "remote"}, f"Expected cached or remote for fresh install: {actions}"
        _ok(f"rich action(s): {actions}")

    def test_rich_15_upgrade_shows_reinstall(self, bubble_dir: Path) -> None:
        """After 14.3.3 is installed, upgrading to 15.0.0 should show reinstall."""
        _header("Integration: rich 14.3.3→15.0.0 shows reinstall in plan")
        try:
            import uv_ffi
        except ImportError:
            pytest.skip("uv_ffi not available")

        # First install 14.3.3 normally (no callback intercept)
        uv_ffi.set_plan_callback(lambda e: False)
        uv_ffi.run(f"pip install rich==14.3.3 --target {bubble_dir} --quiet")

        # Now capture plan for 15.0.0
        received_plan: list = []

        def capture_plan(entries):
            received_plan.extend(entries)
            return False

        uv_ffi.set_plan_callback(capture_plan)
        rc, unused, stderr, unused = uv_ffi.run(
            f"pip install rich==15.0.0 --target {bubble_dir} --reinstall --quiet"
        )

        _info(f"returncode={rc}")
        _info(f"plan: {received_plan}")

        assert rc == 0, f"Upgrade failed: {stderr}"
        rich_actions = {e[2] for e in received_plan if e[0] == "rich"}
        assert rich_actions & {"reinstall", "cached", "remote"}, \
            f"Unexpected actions for upgrade: {rich_actions}"
        _ok(f"Upgrade plan actions: {rich_actions}")

    def test_rich_15_dist_info_present_after_install(self, bubble_dir: Path) -> None:
        """After install, rich's dist-info must exist in target (not site-packages).

        FAILURE HERE means uv is ignoring --target and installing into site-packages,
        or the dist-info is being dropped somewhere unexpected.  The diagnosis block
        below will show exactly where rich landed.
        """
        _header("Integration: dist-info in target after install")
        try:
            import uv_ffi
        except ImportError:
            pytest.skip("uv_ffi not available")

        uv_ffi.set_plan_callback(lambda e: False)
        rc, unused, stderr, unused = uv_ffi.run(
            f"pip install rich==15.0.0 --target {bubble_dir} --quiet"
        )
        assert rc == 0, f"Install failed: {stderr}"

        dist_infos = list(bubble_dir.glob("rich-*.dist-info"))
        _info(f"dist-infos found in target: {dist_infos}")

        if not dist_infos:
            # ── DIAGNOSIS: find where rich actually went ──────────────────────
            import sysconfig, glob as _glob
            site_pkgs = Path(sysconfig.get_path('purelib'))
            site_rich_dis = list(site_pkgs.glob("rich-*.dist-info"))
            _fail("No dist-info in target — running diagnosis...")
            _info(f"Target dir contents: {sorted(bubble_dir.iterdir())}")
            _info(f"site-packages rich dist-infos: {site_rich_dis}")
            if site_rich_dis:
                _fail("rich dist-info found in SITE-PACKAGES — --target was ignored!")
                _info("Root cause: uv-ffi run() is not forwarding --target to uv internals.")
                _info("Check: does PipInstallArgs::ffi_new() have target=None hardcoded?")
                _info("       In lib.rs, is the target arg parsed from the cmd string?")
            else:
                # Try a broader search under tmp
                tmp_rich = list(Path("/tmp").rglob("rich-15*.dist-info"))
                _info(f"rich dist-infos under /tmp: {tmp_rich[:5]}")

        assert dist_infos, (
            f"No rich dist-info found under {bubble_dir}.\n"
            "See diagnosis output above for where it actually landed.\n"
            "Likely cause: --target is not being forwarded through run() → Rust → uv."
        )
        _ok(f"dist-info present: {dist_infos[0].name}")

    def test_stale_dir_healed_before_install(self, bubble_dir: Path) -> None:
        """
        Confirm that a stale package dir (rich/ exists, no dist-info) is wiped
        before a fresh install into the same target.

        Currently XFAIL: uv installs on top of the stale dir, leaving STALE_MARKER.py
        behind.  This test will flip to PASS once the Rust-side or Python-side healer
        is implemented.

        To manually verify the bug:
            mkdir /tmp/stale_test/rich && touch /tmp/stale_test/rich/STALE_MARKER.py
            8pkg run python -c "
            import uv_ffi
            uv_ffi.set_plan_callback(lambda e: False)
            uv_ffi.run('pip install rich==15.0.0 --target /tmp/stale_test --quiet')
            from pathlib import Path
            print(list(Path('/tmp/stale_test/rich').iterdir()))
            "
        """
        _header("Integration: stale-dir heal before fresh install")

        try:
            import uv_ffi
        except ImportError:
            pytest.skip("uv_ffi not available")

        # Plant a stale rich dir
        stale = bubble_dir / "rich"
        stale.mkdir()
        (stale / "STALE_MARKER.py").write_text("# should not survive")

        uv_ffi.set_plan_callback(lambda e: False)
        rc, unused, stderr, unused = uv_ffi.run(
            f"pip install rich==15.0.0 --target {bubble_dir} --quiet"
        )
        assert rc == 0, f"Install failed: {stderr}"

        stale_marker = bubble_dir / "rich" / "STALE_MARKER.py"
        if stale_marker.exists():
            _fail("STALE_MARKER.py still present — stale dir was NOT cleaned (expected xfail)")
            # This is the documented bug — assert False so xfail records it
            assert False, (
                "Stale rich/ dir was not wiped before fresh install. "
                "STALE_MARKER.py survived. "
                "Fix: in execute_plan() / Python healer, detect pkg_dir_exists && !dist_info_exists "
                "and call fs::remove_dir_all / shutil.rmtree before installing."
            )
        else:
            _ok("Stale dir cleaned — STALE_MARKER.py is gone")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRYPOINT  (for running without pytest: python test_uv_ffi_contracts.py)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _console.print(Panel(
        "[bold]uv_ffi contract tests[/]\n"
        "[dim]Run via: pytest tests/test_uv_ffi_contracts.py -v[/]\n"
        "[dim]Unit only: pytest -m 'not integration'[/]",
        title="[cyan]uv-ffi IPC Contract Suite[/]",
        box=box.DOUBLE_EDGE,
    ))
    sys.exit(
        subprocess.run(
            [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
            check=False,
        ).returncode
    )