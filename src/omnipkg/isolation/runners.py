import subprocess
import sys
import textwrap

from omnipkg.common_utils import safe_print


def run_python_code_in_isolation(
    code: str, job_name: str = "Isolated Job", timeout: int = 30
) -> bool:
    """
    Executes a block of Python code in a pristine subprocess.
    Includes built-in omnipkg path setup and TF patching.
    """
    cleaned_code = code.strip()

    # The wrapper ensures we capture output cleanly and handle imports
    full_code = textwrap.dedent(
        f"""
    import sys
    import os
    import traceback
    try:
        from .common_utils import safe_print
    except ImportError:
        from omnipkg.common_utils import safe_print
                            
        # 1. SETUP PATHS (So we can find omnipkg)
    try:
        # Assuming this runs from site-packages or source
        import omnipkg
    except ImportError:
        sys.path.insert(0, os.getcwd())

    # 2. PATCH TENSORFLOW (Prevent C++ noise/crashes)
    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
    os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
    try:
        from omnipkg.isolation.patchers import smart_tf_patcher
        smart_tf_patcher()
    except ImportError:
        pass

    # 3. SAFETY PRINTER
    def safe_print(msg):
        try:
            print(msg, flush=True)
        except:
            pass

    # 4. USER CODE EXECUTION
    try:
{textwrap.indent(cleaned_code, '        ')}
        safe_print(f"✅ {{job_name}} SUCCESS")
        sys.exit(0)
    except Exception as e:
        safe_print(f"⚠️  {{job_name}} FAILED: {{e}}")
        traceback.print_exc()
        sys.exit(1)
    """
    ).replace("{job_name}", job_name)

    try:
        result = subprocess.run(
            [sys.executable, "-c", full_code],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        # Pass stdout through to parent (optional, or log it)
        if result.stdout:
            print(result.stdout, end="")

        if result.returncode != 0:
            if result.stderr:
                print(f"--- {job_name} STDERR ---")
                print(result.stderr)
                print("-------------------------")
            return False

        return True

    except subprocess.TimeoutExpired:
        safe_print(f"❌ {job_name} timed out after {timeout}s")
        return False
