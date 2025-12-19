import os
import sys
import textwrap

from omnipkg.common_utils import safe_print
from omnipkg.loader import omnipkgLoader

from .workers import PersistentWorker


class TrueSwitcher:
    """
    A task runner that guarantees true version isolation by automatically
    selecting the best strategy for the current OS (fork > worker pool).
    """

    def __init__(self):
        self._strategy = "fork" if hasattr(os, "fork") else "worker_pool"
        self._worker_pool = {}
        # Only print this in verbose contexts, or keep it quiet
        # safe_print(f"🚀 TrueSwitcher initialized with '{self._strategy}' strategy.")

    def run(self, spec: str, code_to_run: str) -> bool:
        if self._strategy == "fork":
            return self._run_with_fork(spec, code_to_run)
        else:  # worker_pool
            return self._run_with_worker(spec, code_to_run)

    def _run_with_fork(self, spec: str, code_to_run: str) -> bool:
        # Fork is only available on Unix
        pid = os.fork()
        if pid == 0:  # Child process
            try:
                # We are in a forked process, so omnipkgLoader is already imported
                with omnipkgLoader(spec, quiet=True):
                    # Dynamically create the function to run
                    # We wrap code in a function to isolate locals
                    exec(f"def task():\n{textwrap.indent(code_to_run, '    ')}")
                    locals()["task"]()
                sys.exit(0)  # Success
            except Exception as e:
                # Ideally log the error to stderr so parent sees it
                sys.stderr.write(f"Forked task failed: {e}\n")
                sys.exit(1)  # Failure
        else:  # Parent process
            _, status = os.waitpid(pid, 0)
            return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0

    def _run_with_worker(self, spec: str, code_to_run: str) -> bool:
        if spec not in self._worker_pool:
            try:
                self._worker_pool[spec] = PersistentWorker(spec)
            except Exception as e:
                safe_print(f"   ❌ Failed to create persistent worker for {spec}: {e}")
                return False

        # The worker's eval can't handle complex multiline logic easily via simple eval()
        # But for the chaos tests, we usually pass simple lines.
        # If complex logic is needed, PersistentWorker needs 'exec' support not just 'eval'.

        # Simple hack for single expressions:
        single_line_code = code_to_run.strip().replace("\n", "; ")

        result = self._worker_pool[spec].execute(single_line_code)

        if not result.get("success"):
            safe_print(f"   ❌ Worker execution failed for {spec}: {result.get('error')}")

        return result.get("success", False)

    def shutdown(self):
        for worker in self._worker_pool.values():
            worker.shutdown()
        self._worker_pool.clear()

    # Make it a context manager for easy cleanup
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()
