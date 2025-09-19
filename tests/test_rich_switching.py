# tests/test_rich_switching.py (Corrected)

try:
    from omnipkg.common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
import sys
import os
from pathlib import Path
import json
import subprocess
import shutil
import tempfile
import time
import traceback

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from omnipkg.i18n import _
try:
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    from omnipkg.loader import omnipkgLoader
except ImportError as e:
    safe_print(f'❌ Failed to import omnipkg modules. Is the project structure correct? Error: {e}')
    sys.exit(1)

# This is the full, self-contained `safe_print` function.
# It only depends on `sys` and built-in print, so it's perfect for injection.
SAFE_PRINT_DEFINITION = """
import sys
import traceback
_builtin_print = print
def safe_print(*args, **kwargs):
    try:
        _builtin_print(*args, **kwargs)
    except UnicodeEncodeError:
        try:
            encoding = sys.stdout.encoding or 'utf-8'
            safe_args = [
                str(arg).encode(encoding, 'replace').decode(encoding)
                for arg in args
            ]
            _builtin_print(*safe_args, **kwargs)
        except Exception:
            _builtin_print("[omnipkg: A message could not be displayed due to an encoding error.]")
"""

LATEST_RICH_VERSION = '13.7.1'
BUBBLE_VERSIONS_TO_TEST = ['13.5.3', '13.4.2']

def print_header(title):
    safe_print('\\n' + '=' * 80)
    safe_print(f'  🚀 {title}')
    safe_print('=' * 80)

def print_subheader(title):
    safe_print(f'\\n--- {title} ---')

def set_install_strategy(strategy):
    """Set the install strategy"""
    try:
        subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', strategy], capture_output=True, text=True, check=True)
        safe_print(f'   ⚙️  Install strategy set to: {strategy}')
        return True
    except Exception as e:
        safe_print(f'   ⚠️  Failed to set install strategy: {e}')
        return False

def pip_uninstall_rich():
    """Use pip to directly uninstall rich from main environment"""
    safe_print('   🧹 Using pip to uninstall rich from main environment...')
    subprocess.run(['pip', 'uninstall', 'rich', '-y'], capture_output=True, text=True, check=False)
    safe_print('   ✅ pip uninstall rich completed successfully')

def pip_install_rich(version):
    """Use pip to directly install specific rich version"""
    safe_print(f'   📦 Using pip to install rich=={version}...')
    try:
        subprocess.run(['pip', 'install', f'rich=={version}'], capture_output=True, text=True, check=True)
        safe_print(f'   ✅ pip install rich=={version} completed successfully')
        return True
    except Exception as e:
        safe_print(f'   ❌ pip install failed: {e}')
        return False

def setup_environment():
    print_header('STEP 1: Environment Setup & Cleanup')
    set_install_strategy('stable-main')
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager)
    safe_print('   🧹 Cleaning up existing Rich installations and bubbles...')
    for bubble in omnipkg_core.multiversion_base.glob('rich-*'):
        shutil.rmtree(bubble, ignore_errors=True)
    pip_uninstall_rich()
    if not pip_install_rich(LATEST_RICH_VERSION):
        safe_print('   ❌ Failed to install main environment Rich version')
        return None
    safe_print('✅ Environment prepared')
    return config_manager

def create_test_bubbles(config_manager):
    print_header('STEP 2: Creating Test Bubbles for Older Versions')
    omnipkg_core = OmnipkgCore(config_manager)
    for version in BUBBLE_VERSIONS_TO_TEST:
        safe_print(f'   🫧 Creating bubble for rich=={version}')
        omnipkg_core.smart_install([f'rich=={version}'])
    return True

def test_python_import(expected_version: str, config_manager, is_bubble: bool):
    safe_print(f'   🔧 Testing import of version {expected_version}...')
    config = config_manager.config
    project_root_str = str(Path(__file__).resolve().parent.parent)

    # THE CORE FIX IS HERE: Injecting SAFE_PRINT_DEFINITION into the script content
    test_script_content = f'''
import sys
import json
import traceback
from pathlib import Path
sys.path.insert(0, r'{project_root_str}')

# --- INJECTED DEFINITION ---
{SAFE_PRINT_DEFINITION}
# --- END INJECTION ---

try:
    from omnipkg.loader import omnipkgLoader
    from importlib.metadata import version

    config = json.loads('{json.dumps(config)}')
    is_bubble = {is_bubble}
    expected_version = "{expected_version}"
    target_spec = f"rich=={{expected_version}}"

    if is_bubble:
        with omnipkgLoader(target_spec, config=config, quiet=True):
            import rich
            actual_version = version('rich')
            assert actual_version == expected_version, f"Version mismatch! Expected {{expected_version}}, got {{actual_version}}"
            safe_print(f"✅ Imported and verified version {{actual_version}}")
    else:
        import rich
        actual_version = version('rich')
        assert actual_version == expected_version, f"Version mismatch! Expected {{expected_version}}, got {{actual_version}}"
        safe_print(f"✅ Imported and verified version {{actual_version}}")

except Exception as e:
    safe_print(f"❌ TEST FAILED: {{e}}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
'''
    temp_script_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(test_script_content)
            temp_script_path = f.name
        
        python_exe = config.get('python_executable', sys.executable)
        result = subprocess.run([python_exe, temp_script_path], capture_output=True, text=True, timeout=90)
        
        if result.returncode == 0:
            safe_print(f'      └── {result.stdout.strip()}')
            return True
        else:
            safe_print(f'   ❌ Subprocess FAILED for version {expected_version}:')
            if result.stdout.strip():
                safe_print(f'      STDOUT: {result.stdout.strip()}')
            if result.stderr.strip():
                safe_print(f'      STDERR: {result.stderr.strip()}')
            return False
    finally:
        if temp_script_path and os.path.exists(temp_script_path):
            os.unlink(temp_script_path)

def run_comprehensive_test():
    print_header('🚨 OMNIPKG RICH LIBRARY STRESS TEST 🚨')
    try:
        config_manager = setup_environment()
        if not config_manager: return False

        if not create_test_bubbles(config_manager): return False
        
        print_header('STEP 3: Comprehensive Version Testing')
        test_results = {}
        
        print_subheader(f'Testing Main Environment (rich=={LATEST_RICH_VERSION})')
        test_results[f'main-{LATEST_RICH_VERSION}'] = test_python_import(LATEST_RICH_VERSION, config_manager, is_bubble=False)
        
        for version in BUBBLE_VERSIONS_TO_TEST:
            print_subheader(f'Testing Bubble (rich=={version})')
            test_results[f'bubble-{version}'] = test_python_import(version, config_manager, is_bubble=True)
            
        print_header('FINAL TEST RESULTS')
        safe_print('📊 Test Summary:')
        all_tests_passed = all(test_results.values())
        for test_name, passed in test_results.items():
            status = '✅ PASSED' if passed else '❌ FAILED'
            safe_print(f'   {test_name.ljust(25)}: {status}')
        
        if all_tests_passed:
            safe_print('\\n🎉🎉🎉 ALL RICH LIBRARY TESTS PASSED! 🎉🎉🎉')
        else:
            safe_print('\\n💥 SOME TESTS FAILED - RICH HANDLING NEEDS WORK 💥')
            
        return all_tests_passed
    except Exception as e:
        safe_print(f'\\n❌ Critical error during testing: {e}')
        traceback.print_exc()
        return False
    finally:
        print_header('STEP 4: Cleanup & Restoration')
        config_manager = ConfigManager()
        omnipkg_core = OmnipkgCore(config_manager)
        safe_print('   🧹 Cleaning up test bubbles via omnipkg API...')
        specs_to_uninstall = [f'rich=={v}' for v in BUBBLE_VERSIONS_TO_TEST]
        if specs_to_uninstall:
            omnipkg_core.smart_uninstall(specs_to_uninstall, force=True, install_type='bubble')
        safe_print(f'   📦 Restoring main environment: rich=={LATEST_RICH_VERSION}')
        pip_uninstall_rich()
        pip_install_rich(LATEST_RICH_VERSION)
        safe_print('✅ Cleanup complete')

if __name__ == '__main__':
    success = run_comprehensive_test()
    sys.exit(0 if success else 1)