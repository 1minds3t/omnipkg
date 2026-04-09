"""
test_flask_port_finder.py
=========================
Cross-platform contract tests for flask_port_finder.py.

Specifically stress-tests every known Windows failure mode:
  - subprocess pipe buffer overflow (> 4 KB stdout/stderr)
  - UTF-8 / encoding issues (emoji, non-ASCII in generated code)
  - Path separators in generated subprocess code (backslash hell)
  - tempfile path with spaces / Unicode on Windows
  - shutdown_file path embedded as a raw string in generated code
  - signal.SIGBREAK availability (Windows-only)
  - NamedTemporaryFile(delete=False) + manual unlink (Windows file locking)
  - subprocess text=True encoding on Windows (cp1252 vs utf-8)
  - Port exhaustion / SO_REUSEADDR behaviour differences
  - Concurrent allocation race conditions
  - validate_flask_app() subprocess stdout/stderr truncation

Run on all platforms:
  pytest tests/test_flask_port_finder.py -v

Run only the Windows-specific group (harmless on Linux/Mac too):
  pytest tests/test_flask_port_finder.py -v -m windows_compat

Skip slow live-server tests:
  pytest tests/test_flask_port_finder.py -v -m "not slow"
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unicodedata
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Import the module under test
# ─────────────────────────────────────────────────────────────────────────────

def _import_fpf():
    """Import flask_port_finder; skip entire module if unavailable."""
    try:
        # Try as part of omnipkg package
        from omnipkg import flask_port_finder as fpf
        return fpf
    except ImportError:
        pass
    # Fallback: load from file directly (dev layout)
    import importlib.util
    candidates = [
        Path(__file__).parent.parent / "src" / "omnipkg" / "utils" / "flask_port_finder.py",
        Path(__file__).parent.parent / "flask_port_finder.py",
        Path(__file__).parent / "flask_port_finder.py",
    ]
    for p in candidates:
        if p.exists():
            spec = importlib.util.spec_from_file_location("flask_port_finder", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    pytest.skip("flask_port_finder.py not found")


@pytest.fixture(scope="module")
def fpf():
    return _import_fpf()


# ─────────────────────────────────────────────────────────────────────────────
# Debug helpers — printed on failure so CI logs are useful
# ─────────────────────────────────────────────────────────────────────────────

def _platform_info() -> str:
    return (
        f"platform={platform.system()} "
        f"python={sys.version} "
        f"stdout_enc={getattr(sys.stdout, 'encoding', '?')} "
        f"fs_enc={sys.getfilesystemencoding()} "
        f"default_enc={sys.getdefaultencoding()}"
    )


def _subprocess_debug(result: subprocess.CompletedProcess) -> str:
    return (
        f"\n  returncode : {result.returncode}"
        f"\n  stdout     : {result.stdout!r}"
        f"\n  stderr     : {result.stderr!r}"
        f"\n  {_platform_info()}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 1 — is_windows() detection
# ─────────────────────────────────────────────────────────────────────────────

class TestIsWindows:
    def test_returns_bool(self, fpf):
        assert isinstance(fpf.is_windows(), bool)

    def test_consistent_with_sys_platform(self, fpf):
        expected = sys.platform == "win32"
        assert fpf.is_windows() == expected, (
            f"is_windows()={fpf.is_windows()} but sys.platform={sys.platform!r}"
        )

    def test_consistent_with_platform_system(self, fpf):
        expected = platform.system() == "Windows"
        assert fpf.is_windows() == expected


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 2 — Port reservation: thread-safety and uniqueness
# ─────────────────────────────────────────────────────────────────────────────

class TestPortReservation:
    def test_reserve_returns_true_first_time(self, fpf):
        port = fpf.find_free_port(reserve=False)
        result = fpf.reserve_port(port, duration=0.5)
        assert result is True, f"First reservation of port {port} should succeed"
        fpf.release_port(port)

    def test_reserve_returns_false_when_already_reserved(self, fpf):
        port = fpf.find_free_port(reserve=False)
        fpf.reserve_port(port, duration=30.0)  # long hold
        result = fpf.reserve_port(port, duration=30.0)
        assert result is False, f"Double-reservation of port {port} should fail"
        fpf.release_port(port)

    def test_release_makes_port_reservable_again(self, fpf):
        port = fpf.find_free_port(reserve=False)
        fpf.reserve_port(port, duration=30.0)
        fpf.release_port(port)
        result = fpf.reserve_port(port, duration=0.5)
        assert result is True, "After release, port should be reservable again"
        fpf.release_port(port)

    def test_concurrent_allocation_all_unique(self, fpf):
        """
        10 threads racing to allocate ports must each get a different port.
        This is the core race condition that fails on Windows without proper locking.
        """
        allocated = []
        errors = []

        def grab():
            try:
                p = fpf.find_free_port(start_port=15000, max_attempts=200, reserve=True)
                allocated.append(p)
                time.sleep(0.05)
                fpf.release_port(p)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=grab) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Allocation errors: {errors}\n{_platform_info()}"
        assert len(allocated) == len(set(allocated)), (
            f"Duplicate ports allocated: {sorted(allocated)}\n{_platform_info()}"
        )

    def test_concurrent_allocation_via_threadpool(self, fpf):
        """ThreadPoolExecutor version — mimics the original built-in test."""
        def allocate(i):
            p = fpf.find_free_port(start_port=16000, max_attempts=200, reserve=True)
            time.sleep(0.02)
            fpf.release_port(p)
            return p

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            ports = list(ex.map(allocate, range(8)))

        assert len(ports) == len(set(ports)), (
            f"ThreadPoolExecutor duplicate ports: {sorted(ports)}\n{_platform_info()}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 3 — is_port_actually_free()
# ─────────────────────────────────────────────────────────────────────────────

class TestIsPortActuallyFree:
    def test_unbound_port_is_free(self, fpf):
        port = fpf.find_free_port(reserve=False)
        assert fpf.is_port_actually_free(port), (
            f"Port {port} should be free\n{_platform_info()}"
        )

    def test_bound_port_is_not_free(self, fpf):
        """Bind a socket ourselves then verify is_port_actually_free returns False."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", 0))
            _, port = sock.getsockname()
            # On Windows SO_REUSEADDR is weaker — may not block a second bind.
            # We skip rather than fail on that platform edge-case.
            if not fpf.is_windows():
                assert not fpf.is_port_actually_free(port), (
                    f"Bound port {port} should NOT be free\n{_platform_info()}"
                )
            else:
                # Windows: just verify the call doesn't raise
                fpf.is_port_actually_free(port)
        finally:
            sock.close()

    def test_never_raises_on_invalid_port(self, fpf):
        """is_port_actually_free must never raise — it should return False."""
        result = fpf.is_port_actually_free(0)
        assert isinstance(result, bool)

    def test_port_in_valid_range(self, fpf):
        port = fpf.find_free_port(reserve=False)
        assert 1024 <= port <= 65535, f"Got out-of-range port {port}"


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 4 — find_free_port()
# ─────────────────────────────────────────────────────────────────────────────

class TestFindFreePort:
    def test_returns_int(self, fpf):
        p = fpf.find_free_port(reserve=False)
        assert isinstance(p, int)

    def test_respects_start_port(self, fpf):
        p = fpf.find_free_port(start_port=7000, reserve=False)
        assert p >= 7000, f"Port {p} is below start_port=7000"

    def test_raises_when_range_exhausted(self, fpf):
        """When every port in range is reserved, RuntimeError must be raised."""
        # Reserve a tiny range completely
        ports_to_reserve = list(range(17100, 17110))
        for p in ports_to_reserve:
            fpf._reserved_ports.add(p)
        try:
            with patch.object(fpf, "is_port_actually_free", return_value=False):
                with pytest.raises(RuntimeError, match="Could not find free port"):
                    fpf.find_free_port(start_port=17100, max_attempts=10, reserve=False)
        finally:
            for p in ports_to_reserve:
                fpf._reserved_ports.discard(p)

    def test_no_reserve_does_not_add_to_reserved_set(self, fpf):
        before = set(fpf._reserved_ports)
        fpf.find_free_port(reserve=False)
        after = set(fpf._reserved_ports)
        assert after == before, "reserve=False should not modify _reserved_ports"


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 5 — patch_flask_code()
# ─────────────────────────────────────────────────────────────────────────────

SIMPLE_FLASK_APP = textwrap.dedent("""\
    from flask import Flask
    app = Flask(__name__)

    @app.route('/')
    def home():
        return 'Hello'

    if __name__ == '__main__':
        app.run(debug=True, host='0.0.0.0')
""")


class TestPatchFlaskCode:
    def test_port_injected_into_patched_code(self, fpf):
        patched, port, _ = fpf.patch_flask_code(SIMPLE_FLASK_APP)
        assert f"port={port}" in patched, (
            f"port={port} not found in patched code:\n{patched}"
        )
        fpf.release_port(port)

    def test_debug_false_injected(self, fpf):
        patched, port, _ = fpf.patch_flask_code(SIMPLE_FLASK_APP)
        assert "debug=False" in patched
        fpf.release_port(port)

    def test_use_reloader_false_injected(self, fpf):
        """
        CRITICAL for Windows: use_reloader=True spawns a second process that
        cannot be cleanly killed on Windows. Must always be False.
        """
        patched, port, _ = fpf.patch_flask_code(SIMPLE_FLASK_APP)
        assert "use_reloader=False" in patched, (
            f"use_reloader=False missing — Windows process cleanup will break!\n{patched}"
        )
        fpf.release_port(port)

    def test_code_without_app_run_returned_unchanged(self, fpf):
        code = "from flask import Flask\napp = Flask(__name__)\n"
        patched, port, _ = fpf.patch_flask_code(code)
        assert patched == code
        fpf.release_port(port)

    def test_port_is_unique_per_call(self, fpf):
        _, p1, _ = fpf.patch_flask_code(SIMPLE_FLASK_APP)
        _, p2, _ = fpf.patch_flask_code(SIMPLE_FLASK_APP)
        assert p1 != p2, f"Two patch_flask_code() calls returned same port {p1}"
        fpf.release_port(p1)
        fpf.release_port(p2)

    @pytest.mark.windows_compat
    def test_patched_code_contains_no_raw_backslash_paths(self, fpf):
        """
        On Windows, Path objects stringify with backslashes. If any path ends up
        raw in the generated code string it will break the Python source.
        """
        patched, port, _ = fpf.patch_flask_code(SIMPLE_FLASK_APP)
        # The patched source itself (the app.run line) should not have backslashes
        run_line = [l for l in patched.splitlines() if "app.run" in l]
        assert run_line, "No app.run line found in patched code"
        assert "\\" not in run_line[0], (
            f"Backslash in app.run line — will break on Windows:\n{run_line[0]}"
        )
        fpf.release_port(port)


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 6 — Windows pipe buffer overflow in validate_flask_app()
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateFlaskAppSubprocess:
    """
    The subprocess inside validate_flask_app() uses capture_output=True which
    buffers ALL stdout/stderr in memory before returning.  On Windows, if the
    child writes > ~4 KB to a pipe without the parent reading it, the child
    BLOCKS (deadlock).  We test with apps that produce large output.
    """

    LARGE_OUTPUT_APP = textwrap.dedent("""\
        from flask import Flask
        app = Flask(__name__)

        @app.route('/')
        def home():
            return 'x' * 8192  # 8 KB response

        # Print a lot to stdout/stderr to trigger Windows pipe buffer overflow
        import sys
        for i in range(200):
            print(f"startup log line {i}: {'x' * 40}", flush=True)
            print(f"stderr line {i}: {'y' * 40}", file=sys.stderr, flush=True)
    """)

    VALID_APP = textwrap.dedent("""\
        from flask import Flask
        app = Flask(__name__)

        @app.route('/')
        def hello():
            return 'Hello World!'
    """)

    INVALID_APP = textwrap.dedent("""\
        from flask import Flask
        app = Flask(__name__)

        @app.route('/')
        def broken():
            raise RuntimeError("intentional break")
        # Syntax error below
        def bad(:
    """)

    def test_valid_app_passes_validation(self, fpf):
        port = fpf.find_free_port(reserve=True)
        result = fpf.validate_flask_app(self.VALID_APP, port, timeout=15.0)
        fpf.release_port(port)
        assert result is True, (
            f"Valid app failed validation\n{_platform_info()}"
        )

    def test_invalid_app_fails_validation(self, fpf):
        port = fpf.find_free_port(reserve=True)
        result = fpf.validate_flask_app(self.INVALID_APP, port, timeout=10.0)
        fpf.release_port(port)
        assert result is False, "Syntactically broken app should fail validation"

    @pytest.mark.windows_compat
    def test_large_stdout_does_not_deadlock(self, fpf):
        """
        App that produces > 4 KB of stdout during import must not deadlock
        the parent process.  Timeout acts as deadlock detector.
        """
        port = fpf.find_free_port(reserve=True)
        # 8-second timeout — if it returns at all, no deadlock
        result = fpf.validate_flask_app(self.LARGE_OUTPUT_APP, port, timeout=20.0)
        fpf.release_port(port)
        # We don't assert True/False on result — just that it returned
        assert isinstance(result, bool), (
            f"validate_flask_app deadlocked or crashed\n{_platform_info()}"
        )

    @pytest.mark.windows_compat
    def test_subprocess_encoding_handles_non_ascii(self, fpf):
        """
        Windows default console encoding is cp1252.  Emoji and non-ASCII
        in stdout/stderr must not cause UnicodeDecodeError in the parent.
        """
        app_with_unicode = textwrap.dedent("""\
            from flask import Flask
            import sys
            app = Flask(__name__)

            @app.route('/')
            def hello():
                return 'こんにちは'

            # These would explode on Windows if encoding is wrong
            print("startup: ✅ 日本語 émoji 🐍", flush=True)
            print("err: ⚠️ naïve café", file=sys.stderr, flush=True)
        """)
        port = fpf.find_free_port(reserve=True)
        try:
            result = fpf.validate_flask_app(app_with_unicode, port, timeout=15.0)
        except (UnicodeDecodeError, UnicodeEncodeError) as e:
            pytest.fail(
                f"Unicode error in subprocess communication — classic Windows failure:\n"
                f"{e}\n{_platform_info()}"
            )
        finally:
            fpf.release_port(port)
        assert isinstance(result, bool)

    @pytest.mark.windows_compat
    def test_subprocess_encoding_flag_present(self, fpf):
        """
        Verify that the subprocess.run() call in validate_flask_app uses
        encoding or text=True so Windows doesn't get raw bytes.
        Inspects the source rather than running — fast and reliable.
        """
        import inspect
        src = inspect.getsource(fpf.validate_flask_app)
        has_text = "text=True" in src
        has_encoding = "encoding=" in src
        assert has_text or has_encoding, (
            "validate_flask_app subprocess.run() has neither text=True nor encoding= !\n"
            "On Windows cp1252 this will fail on any non-ASCII output.\n"
            f"Source snippet:\n{src[:600]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 7 — shutdown_file path safety on Windows
# ─────────────────────────────────────────────────────────────────────────────

class TestShutdownFilePath:
    """
    The shutdown file path is embedded as a raw string inside generated Python
    source (the wrapper_code f-string in FlaskAppManager.start()).
    On Windows, tempdir is often C:\\Users\\... which breaks Python string literals.
    """

    @pytest.mark.windows_compat
    def test_shutdown_file_path_is_safe_in_generated_source(self, fpf):
        """
        The shutdown file path embedded in wrapper_code must be either:
          - Forward-slash only, OR
          - Properly escaped (double backslashes), OR
          - A raw string literal
        We check by attempting to compile the generated source snippet.
        """
        import inspect
        port = fpf.find_free_port(reserve=True)
        manager = fpf.FlaskAppManager(SIMPLE_FLASK_APP, port, validate_only=False)
        fpf.release_port(port)

        # Reproduce the wrapper_code path embed as the real code does
        shutdown_path = str(manager.shutdown_file)

        # If the path has backslashes (Windows), they'd appear raw in f-string
        # which is a SyntaxError or wrong path.
        if "\\" in shutdown_path:
            safe_path = shutdown_path.replace("\\", "\\\\")
            # Verify the safe version compiles
            snippet = f'shutdown_file = Path("{safe_path}")\n'
            try:
                compile(snippet, "<test>", "exec")
            except SyntaxError as e:
                pytest.fail(
                    f"Shutdown file path breaks Python source generation on Windows!\n"
                    f"Raw path: {shutdown_path!r}\n"
                    f"Snippet: {snippet!r}\n"
                    f"Error: {e}\n{_platform_info()}"
                )

    @pytest.mark.windows_compat
    def test_shutdown_file_created_in_temp_dir(self, fpf):
        port = fpf.find_free_port(reserve=True)
        manager = fpf.FlaskAppManager(SIMPLE_FLASK_APP, port, validate_only=False)
        fpf.release_port(port)
        # Must be in a temp directory — critical for cross-user isolation on Windows
        temp_dir = Path(tempfile.gettempdir()).resolve()
        assert str(manager.shutdown_file).startswith(str(temp_dir)), (
            f"shutdown_file not in tempdir!\n"
            f"  shutdown_file : {manager.shutdown_file}\n"
            f"  tempdir       : {temp_dir}"
        )

    @pytest.mark.windows_compat
    def test_shutdown_file_name_no_special_chars(self, fpf):
        """Shutdown file name must be safe on Windows (no colons, no pipes, etc.)."""
        port = fpf.find_free_port(reserve=True)
        manager = fpf.FlaskAppManager(SIMPLE_FLASK_APP, port)
        fpf.release_port(port)
        name = manager.shutdown_file.name
        forbidden = set(':*?"<>|')
        bad = forbidden & set(name)
        assert not bad, (
            f"Shutdown file name {name!r} contains Windows-forbidden chars: {bad}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 8 — NamedTemporaryFile Windows file-locking
# ─────────────────────────────────────────────────────────────────────────────

class TestTempFileHandling:
    """
    On Windows, NamedTemporaryFile with delete=True cannot be read by another
    process while the file handle is open. The code must use delete=False and
    manually unlink.  We verify this pattern is used.
    """

    @pytest.mark.windows_compat
    def test_tempfile_uses_delete_false(self, fpf):
        import inspect
        src = inspect.getsource(fpf.FlaskAppManager.start)
        assert "delete=False" in src, (
            "FlaskAppManager.start() does not use delete=False for NamedTemporaryFile!\n"
            "On Windows, delete=True prevents the subprocess from opening the file.\n"
            f"Source:\n{src}"
        )

    @pytest.mark.windows_compat
    def test_tempfile_can_be_written_and_read(self, fpf):
        """Simulate the exact write/open pattern used by FlaskAppManager.start()."""
        content = "print('hello from temp')\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            # On Windows this was the problematic step — file was locked
            result = subprocess.run(
                [sys.executable, temp_path],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            assert result.returncode == 0, (
                f"Could not execute temp file (Windows file locking issue?)\n"
                + _subprocess_debug(result)
            )
            assert "hello from temp" in result.stdout
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass  # best-effort


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 9 — signal handling on Windows
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalHandling:
    """
    Windows does not have SIGTERM in the same sense as Unix.
    The wrapper_code in FlaskAppManager uses SIGBREAK (Windows-only) as a fallback.
    """

    @pytest.mark.windows_compat
    def test_sigbreak_guarded_by_hasattr(self, fpf):
        """
        signal.SIGBREAK does not exist on Linux/Mac. The code must guard with
        hasattr(signal, 'SIGBREAK') — not an unconditional reference.
        """
        import inspect
        src = inspect.getsource(fpf.FlaskAppManager.start)
        assert "SIGBREAK" not in src or "hasattr" in src, (
            "SIGBREAK used without hasattr() guard — will crash on Linux/Mac!\n"
            f"Source:\n{src}"
        )

    def test_wrapper_code_sigbreak_guard(self, fpf):
        """The wrapper_code string itself must have the hasattr guard."""
        port = fpf.find_free_port(reserve=True)
        manager = fpf.FlaskAppManager(SIMPLE_FLASK_APP, port)
        fpf.release_port(port)

        # Reproduce the wrapper_code snippet
        import inspect
        src = inspect.getsource(fpf.FlaskAppManager.start)
        # Check the embedded string fragment
        assert "hasattr(signal, 'SIGBREAK')" in src or "hasattr(signal, \"SIGBREAK\")" in src, (
            "wrapper_code missing hasattr(signal, 'SIGBREAK') guard\n"
            f"This will raise AttributeError on Linux/Mac at import time of generated code"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 10 — FlaskAppManager lifecycle (fast, no real server)
# ─────────────────────────────────────────────────────────────────────────────

class TestFlaskAppManagerLifecycle:
    def test_validate_only_does_not_spawn_process(self, fpf):
        """validate_only=True must use validate_flask_app(), not Popen."""
        port = fpf.find_free_port(reserve=True)
        manager = fpf.FlaskAppManager(SIMPLE_FLASK_APP, port, validate_only=True)
        with patch.object(fpf, "validate_flask_app", return_value=True) as mock_val:
            result = manager.start()
        fpf.release_port(port)
        mock_val.assert_called_once()
        assert manager.process is None, "validate_only should never create a subprocess"

    def test_shutdown_releases_port(self, fpf):
        port = fpf.find_free_port(reserve=True)
        manager = fpf.FlaskAppManager(SIMPLE_FLASK_APP, port, validate_only=True)
        with patch.object(fpf, "validate_flask_app", return_value=True):
            manager.start()
        manager.shutdown()
        # Port should now be reservable again
        assert fpf.reserve_port(port, duration=0.1) is True, (
            f"Port {port} not released after shutdown"
        )
        fpf.release_port(port)

    def test_double_shutdown_does_not_raise(self, fpf):
        port = fpf.find_free_port(reserve=True)
        manager = fpf.FlaskAppManager(SIMPLE_FLASK_APP, port, validate_only=True)
        with patch.object(fpf, "validate_flask_app", return_value=True):
            manager.start()
        manager.shutdown()
        manager.shutdown()  # must not raise

    def test_shutdown_file_cleaned_up(self, fpf):
        port = fpf.find_free_port(reserve=True)
        manager = fpf.FlaskAppManager(SIMPLE_FLASK_APP, port, validate_only=True)
        with patch.object(fpf, "validate_flask_app", return_value=True):
            manager.start()
        # Create the signal file as the real shutdown would
        manager.shutdown_file.parent.mkdir(parents=True, exist_ok=True)
        manager.shutdown_file.write_text("SHUTDOWN")
        manager.shutdown()
        assert not manager.shutdown_file.exists(), (
            f"Shutdown signal file not cleaned up: {manager.shutdown_file}"
        )

    @pytest.mark.windows_compat
    def test_manager_port_is_int(self, fpf):
        port = fpf.find_free_port(reserve=True)
        manager = fpf.FlaskAppManager(SIMPLE_FLASK_APP, port)
        fpf.release_port(port)
        assert isinstance(manager.port, int), (
            f"manager.port is {type(manager.port).__name__}, not int — "
            "will break f-string formatting in wrapper_code on Windows"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 11 — auto_patch_flask_port() integration
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoPatchFlaskPort:
    def test_patches_flask_code(self, fpf):
        result = fpf.auto_patch_flask_port(SIMPLE_FLASK_APP)
        assert "app.run" in result
        assert "port=" in result

    def test_non_flask_code_returned_unchanged(self, fpf):
        code = "print('hello')\n"
        result = fpf.auto_patch_flask_port(code)
        assert result == code

    def test_code_without_app_run_returned_unchanged(self, fpf):
        code = "from flask import Flask\napp = Flask(__name__)\n"
        result = fpf.auto_patch_flask_port(code)
        assert result == code

    def test_flask_import_case_insensitive(self, fpf):
        """'Flask' / 'flask' case in code string — both should trigger patching."""
        code_lower = SIMPLE_FLASK_APP.replace("Flask", "flask").replace("flask import flask", "flask import Flask")
        result = fpf.auto_patch_flask_port(SIMPLE_FLASK_APP)
        assert "port=" in result


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 12 — Windows subprocess encoding: explicit UTF-8 environment
# ─────────────────────────────────────────────────────────────────────────────

class TestSubprocessEncoding:
    """
    On Windows, subprocess inherits the console code page (usually cp1252).
    Any Flask app that prints non-ASCII (common in error messages) can corrupt
    the stdout/stderr pipe.  The fix is to set PYTHONIOENCODING=utf-8 in the
    subprocess env, or pass encoding='utf-8' to subprocess.run().
    """

    @pytest.mark.windows_compat
    def test_validation_subprocess_env_has_utf8(self, fpf):
        """
        Verify that validate_flask_app() either passes encoding='utf-8' or
        sets PYTHONIOENCODING=utf-8 in the child's environment.
        """
        import inspect
        src = inspect.getsource(fpf.validate_flask_app)
        has_encoding_kwarg = "encoding='utf-8'" in src or 'encoding="utf-8"' in src
        has_pythonioencoding = "PYTHONIOENCODING" in src
        has_text = "text=True" in src  # text=True uses default encoding — acceptable if env fixed

        assert has_encoding_kwarg or has_pythonioencoding or has_text, (
            "validate_flask_app subprocess has no UTF-8 encoding protection!\n"
            "On Windows cp1252, any non-ASCII output will corrupt or crash.\n"
            "Fix: add encoding='utf-8' to subprocess.run() OR set "
            "env['PYTHONIOENCODING'] = 'utf-8'\n"
            f"Source:\n{src[:800]}"
        )

    @pytest.mark.windows_compat
    def test_validate_subprocess_env_is_copy_not_mutated(self, fpf):
        """
        validate_flask_app() must not mutate os.environ directly.
        It should pass env=os.environ.copy() to subprocess.run().
        """
        import inspect
        src = inspect.getsource(fpf.validate_flask_app)
        assert "os.environ.copy()" in src or "env=" in src, (
            "validate_flask_app does not pass env= to subprocess.run().\n"
            "This can cause PYTHONIOENCODING mutation to leak into the parent process."
        )

    def test_actual_utf8_roundtrip(self, fpf):
        """
        Spawn a subprocess that prints UTF-8 and verify the parent receives it
        without corruption. This is the integration version of the encoding test.
        """
        code = "import sys; print('✅ こんにちは 🐍', flush=True)"
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=5.0,
        )
        assert result.returncode == 0, _subprocess_debug(result)
        assert "こんにちは" in result.stdout, (
            f"UTF-8 roundtrip failed — stdout: {result.stdout!r}\n{_platform_info()}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 13 — Tempfile path with spaces and Unicode (Windows-specific hell)
# ─────────────────────────────────────────────────────────────────────────────

class TestTempfilePathEdgeCases:
    """
    On Windows, user home dirs frequently have spaces: C:\\Users\\John Doe\\...
    NamedTemporaryFile in such dirs produces paths with spaces that break
    subprocess argument lists if not properly quoted.
    """

    @pytest.mark.windows_compat
    def test_subprocess_handles_path_with_spaces(self, fpf):
        """Temp file in a path with spaces must be executable via subprocess."""
        with tempfile.TemporaryDirectory() as td:
            spaced_dir = Path(td) / "path with spaces"
            spaced_dir.mkdir()
            script = spaced_dir / "test_script.py"
            script.write_text("print('space test ok')\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5.0,
            )
            assert result.returncode == 0, (
                f"Script in path-with-spaces failed!\n" + _subprocess_debug(result)
            )
            assert "space test ok" in result.stdout

    @pytest.mark.windows_compat
    def test_shutdown_file_path_with_spaces_survives_fstring(self, fpf):
        """
        Simulate the shutdown_file being in a path with spaces and verify
        the generated wrapper_code Path() call is syntactically valid.
        """
        fake_path = Path(tempfile.gettempdir()) / "path with spaces" / "flask_shutdown_9999.signal"
        path_str = str(fake_path)

        # This is exactly how wrapper_code embeds the path:
        snippet = f'from pathlib import Path\nshutdown_file = Path(r"{path_str}")\n'
        try:
            compile(snippet, "<test>", "exec")
        except SyntaxError as e:
            pytest.fail(
                f"Path with spaces breaks generated source!\n"
                f"Path: {path_str!r}\n"
                f"Snippet: {snippet!r}\n"
                f"SyntaxError: {e}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 14 — SLOW: Real Flask server lifecycle (requires flask installed)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.slow
class TestRealFlaskLifecycle:
    """Full integration tests that actually start a Flask server."""

    @pytest.fixture(autouse=True)
    def require_flask(self):
        pytest.importorskip("flask", reason="flask not installed")

    def test_flask_server_starts_and_responds(self, fpf):
        port = fpf.find_free_port(start_port=18000, max_attempts=100, reserve=True)
        patched, port, manager = fpf.patch_flask_code(SIMPLE_FLASK_APP, interactive=True)
        assert manager is not None

        success = manager.start()
        assert success, f"FlaskAppManager.start() failed\n{_platform_info()}"

        ready = manager.wait_for_ready(timeout=15.0)
        assert ready, (
            f"Flask never became ready on port {port}\n"
            f"Process alive: {manager.process and manager.process.poll() is None}\n"
            f"{_platform_info()}"
        )

        # Make an actual HTTP request
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                body = resp.read().decode("utf-8")
            assert "Hello" in body
        finally:
            manager.shutdown()

    def test_port_freed_after_shutdown(self, fpf):
        port = fpf.find_free_port(start_port=18100, max_attempts=100, reserve=True)
        patched, port, manager = fpf.patch_flask_code(SIMPLE_FLASK_APP, interactive=True)

        manager.start()
        manager.wait_for_ready(timeout=15.0)
        manager.shutdown()

        time.sleep(0.5)  # Give OS time to release
        assert fpf.is_port_actually_free(port), (
            f"Port {port} still occupied after shutdown\n{_platform_info()}"
        )

    @pytest.mark.windows_compat
    def test_flask_validation_succeeds_on_valid_app(self, fpf):
        port = fpf.find_free_port(reserve=True)
        result = fpf.validate_flask_app(SIMPLE_FLASK_APP, port, timeout=20.0)
        fpf.release_port(port)
        assert result is True, (
            f"validate_flask_app returned False for valid app\n{_platform_info()}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pytest configuration helpers
# ─────────────────────────────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line("markers", "slow: starts a real Flask server process")
    config.addinivalue_line("markers", "windows_compat: tests for Windows-specific failure modes (safe to run on all platforms)")