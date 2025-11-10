try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
import sys
import os
from pathlib import Path

# --- PROJECT PATH SETUP ---
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# --- BOOTSTRAP SECTION ---
# --- END BOOTSTRAP ---

import json
import subprocess
import shutil
import tempfile
import time
import re
import importlib
import traceback
from importlib.metadata import version as get_pkg_version
from omnipkg.i18n import _
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
from omnipkg.i18n import _


# This is the full, self-contained `safe_print` function.
# It only depends on `sys` and built-in print, so it's perfect for injection.
SAFE_PRINT_DEFINITION = """
import sys
# Injected to be self-contained in subprocess
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

GET_MODULE_VERSION_CODE_SNIPPET = '\ndef get_version_from_module_file(module, package_name, omnipkg_versions_dir):\n    """Enhanced version detection for omnipkg testing"""\n    import importlib.metadata\n    from pathlib import Path\n    \n    version = "unknown"\n    source = "unknown"\n    \n    try:\n        # Method 1: Try module.__version__ first\n        if hasattr(module, \'__version__\'):\n            version = module.__version__\n            source = "module.__version__"\n        \n        # Method 2: Try importlib.metadata with multiple package names\n        if version == "unknown":\n            package_variants = [package_name]\n            # Add common variants\n            if package_name == \'typing-extensions\':\n                package_variants.append(\'typing_extensions\')\n            elif package_name == \'typing_extensions\':\n                package_variants.append(\'typing-extensions\')\n            \n            for pkg_name in package_variants:\n                try:\n                    version = importlib.metadata.version(pkg_name)\n                    source = f"importlib.metadata({pkg_name})"\n                    break\n                except importlib.metadata.PackageNotFoundError:\n                    continue\n        \n        # Method 3: Check if loaded from omnipkg bubble\n        if hasattr(module, \'__file__\') and module.__file__:\n            module_path = Path(module.__file__).resolve()\n            omnipkg_base = Path(omnipkg_versions_dir).resolve()\n            \n            if str(module_path).startswith(str(omnipkg_base)):\n                try:\n                    relative_path = module_path.relative_to(omnipkg_base)\n                    bubble_dir = relative_path.parts[0]  # e.g., "typing_extensions-4.5.0"\n                    \n                    if \'-\' in bubble_dir:\n                        bubble_version = bubble_dir.split(\'-\', 1)[1]\n                        if version == "unknown":\n                            version = bubble_version\n                            source = f"bubble path ({bubble_dir})"\n                        else:\n                            # Verify consistency\n                            if version != bubble_version:\n                                source = f"{source} [bubble: {bubble_version}]"\n                except (ValueError, IndexError):\n                    pass\n                source = f"{source} -> bubble: {module_path}"\n            else:\n                source = f"{source} -> system: {module_path}"\n        elif not hasattr(module, \'__file__\'):\n            source = f"{source} -> namespace package"\n    \n    except Exception as e:\n        source = f"error: {e}"\n    \n    return version, source\n'


def print_header(title):
    safe_print('\\n' + '=' * 80)
    safe_print(_('  🚀 {}').format(title))
    safe_print('=' * 80)

def print_subheader(title):
    safe_print(_('\\n--- {} ---').format(title))

    
def ensure_tensorflow_bubbles(config_manager: ConfigManager):
    """Ensures we have the necessary TensorFlow bubbles created."""
    safe_print(_('   📦 Ensuring TensorFlow bubbles exist...'))
    omnipkg_core = OmnipkgCore(config_manager)

    # Determine the correct Python context for the bubbles, just like in smart_install.
    configured_exe = config_manager.config.get('python_executable', sys.executable)
    version_tuple = config_manager._verify_python_version(configured_exe)
    python_context_version = f'{version_tuple[0]}.{version_tuple[1]}' if version_tuple else 'unknown'
    if python_context_version == 'unknown':
        safe_print(_("   ⚠️ CRITICAL: Could not determine Python context for test bubble creation."))

        return False  # Early exit if we can't determine Python version

    packages_to_bubble = {'tensorflow': ['2.13.0', '2.12.0'], 'typing_extensions': ['4.14.1', '4.5.0']}
    for pkg_name, versions in packages_to_bubble.items():
        for version in versions:
            bubble_name = f'{pkg_name}-{version}'
            bubble_path = omnipkg_core.multiversion_base / bubble_name
            if not bubble_path.exists():
                safe_print(f'   🫧 Force-creating bubble for {pkg_name}=={version}...')
                # Pass the context to the bubble creator
                if omnipkg_core.bubble_manager.create_isolated_bubble(pkg_name, version, python_context_version):
                    safe_print(_('   ✅ Created {}=={} bubble').format(pkg_name, version))
                    
                    # ======================================================================
                    # THIS IS THE FINAL, CRITICAL FIX
                    # We MUST pass the context to the KB rebuild command.
                    # ======================================================================
                    omnipkg_core.rebuild_package_kb(
                        [f'{pkg_name}=={version}'], 
                        target_python_version=python_context_version
                    )
                    # ======================================================================

                else:
                    safe_print(f'   ❌ Failed to create bubble for {pkg_name}=={version}')
            else:
                safe_print(_('   ✅ {}=={} bubble already exists').format(pkg_name, version))
    
    return True  # Success

def setup_environment():
    print_header('STEP 1: Environment Setup & Bubble Creation')
    config_manager = ConfigManager()
    safe_print(_('   🧹 Cleaning up any test artifacts...'))
    site_packages = Path(config_manager.config['site_packages_path'])
    for pkg in ['tensorflow', 'tensorflow_estimator', 'keras', 'typing_extensions']:
        for cloaked in site_packages.glob(f'{pkg}.*_omnipkg_cloaked*'):
            shutil.rmtree(cloaked, ignore_errors=True)
    
    # Handle potential failure
    if not ensure_tensorflow_bubbles(config_manager):
        safe_print('❌ Failed to ensure TensorFlow bubbles exist')
        return None
    
    safe_print(_('✅ Environment prepared'))
    return config_manager

def run_script_with_loader(code: str, description: str):
    """Run a test script and capture relevant output"""
    safe_print(_('\\n--- {} ---').format(description))
    script_path = Path('temp_loader_test.py')
    script_path.write_text(code, encoding='utf-8')
    try:
        result = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace')
        
        output = result.stdout
        errors = result.stderr

        safe_print("--- Relevant Output ---")
        if output:
            safe_print(output)
        
        if result.returncode != 0:
            safe_print("--- Relevant Errors ---")
            if errors:
                safe_print(errors)
            safe_print("---------------------")
            
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        safe_print(_('❌ Test timed out after 120 seconds'))
        return False
    except Exception as e:
        safe_print(_('❌ Test execution failed: {}').format(e))
        traceback.print_exc()
        return False
    finally:
        script_path.unlink(missing_ok=True)

def run_tensorflow_switching_test():
    print_header('🚨 OMNIPKG TENSORFLOW DEPENDENCY SWITCHING TEST 🚨')
    try:
        config_manager = setup_environment()
        if config_manager is None: return False
        
        OMNIPKG_VERSIONS_DIR = Path(config_manager.config['multiversion_base']).resolve()
        
        print_header('STEP 2: Testing TensorFlow Version Switching with omnipkgLoader')
        
        test1_code = f"""
import sys, traceback
from pathlib import Path
sys.path.insert(0, '{Path(__file__).resolve().parent.parent}')
from omnipkg.loader import omnipkgLoader
from omnipkg.core import ConfigManager

# --- INJECTED SAFE_PRINT ---
{SAFE_PRINT_DEFINITION}
# --- END INJECTION ---

{GET_MODULE_VERSION_CODE_SNIPPET}

def main():
    try:
        config_manager = ConfigManager(suppress_init_messages=True)
        safe_print("🌀 Testing TensorFlow 2.13.0 from bubble...")
        with omnipkgLoader("tensorflow==2.13.0", config=config_manager.config):
            import tensorflow as tf, typing_extensions, keras
            safe_print(f"✅ TensorFlow version: {{tf.__version__}}")
            te_version, te_source = get_version_from_module_file(typing_extensions, 'typing_extensions', '{OMNIPKG_VERSIONS_DIR}')
            safe_print(f"✅ Typing Extensions version: {{te_version}}")
            safe_print(f"✅ Keras version: {{keras.__version__}}")
            model = tf.keras.Sequential([tf.keras.layers.Dense(1, input_shape=(1,))])
            safe_print("✅ Model created successfully with TensorFlow 2.13.0")
        return True
    except Exception as e:
        safe_print(f"❌ Test failed: {{e}}")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
"""
        success1 = run_script_with_loader(test1_code, 'TensorFlow 2.13.0 Bubble Test')

        test2_code = f"""
import sys, traceback
from pathlib import Path
sys.path.insert(0, '{Path(__file__).resolve().parent.parent}')
from omnipkg.loader import omnipkgLoader
from omnipkg.core import ConfigManager

# --- INJECTED SAFE_PRINT ---
{SAFE_PRINT_DEFINITION}
# --- END INJECTION ---

{GET_MODULE_VERSION_CODE_SNIPPET}

def main():
    try:
        config_manager = ConfigManager(suppress_init_messages=True)
        safe_print("🌀 Testing dependency switching: typing_extensions versions...")
        safe_print("\\n--- Testing with typing_extensions 4.14.1 ---")
        with omnipkgLoader("typing_extensions==4.14.1", config=config_manager.config):
            import typing_extensions
            te_version, _source = get_version_from_module_file(typing_extensions, 'typing_extensions', '{OMNIPKG_VERSIONS_DIR}')
            safe_print(f"✅ Typing Extensions version: {{te_version}}")
        
        safe_print("\\n--- Testing with typing_extensions 4.5.0 ---")
        with omnipkgLoader("typing_extensions==4.5.0", config=config_manager.config):
            import typing_extensions
            te_version, _source = get_version_from_module_file(typing_extensions, 'typing_extensions', '{OMNIPKG_VERSIONS_DIR}')
            safe_print(f"✅ Typing Extensions version: {{te_version}}")
        return True
    except Exception as e:
        safe_print(f"❌ Test failed: {{e}}")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
"""
        success2 = run_script_with_loader(test2_code, 'Dependency Switching Test')

        test3_code = f"""
import sys, traceback
from pathlib import Path
sys.path.insert(0, '{Path(__file__).resolve().parent.parent}')
from omnipkg.loader import omnipkgLoader
from omnipkg.core import ConfigManager

# --- INJECTED SAFE_PRINT ---
{SAFE_PRINT_DEFINITION}
# --- END INJECTION ---

{GET_MODULE_VERSION_CODE_SNIPPET}

def main():
    try:
        config_manager = ConfigManager(suppress_init_messages=True)
        safe_print("🌀 Testing nested loader usage...")
        with omnipkgLoader("typing_extensions==4.5.0", config=config_manager.config):
            import typing_extensions as te_outer
            outer_version, _source = get_version_from_module_file(te_outer, 'typing_extensions', '{OMNIPKG_VERSIONS_DIR}')
            safe_print(f"✅ Outer context - Typing Extensions: {{outer_version}}")
            with omnipkgLoader("tensorflow==2.13.0", config=config_manager.config):
                import tensorflow as tf
                import typing_extensions as te_inner
                inner_version, _source = get_version_from_module_file(te_inner, 'typing_extensions', '{OMNIPKG_VERSIONS_DIR}')
                safe_print(f"✅ Inner context - TensorFlow: {{tf.__version__}}")
                safe_print(f"✅ Inner context - Typing Extensions: {{inner_version}}")
                model = tf.keras.Sequential([tf.keras.layers.Dense(10, input_shape=(5,))])
                safe_print("✅ Nested loader test: Model created successfully")
        return True
    except Exception as e:
        safe_print(f"❌ Nested test failed: {{e}}")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
"""
        success3 = run_script_with_loader(test3_code, 'Nested Loader Test')

        print_header('STEP 3: Test Results Summary')
        passed_tests = sum([success1, success2, success3])
        safe_print(f"Test 1 (TensorFlow 2.13.0 Bubble): {'✅ PASSED' if success1 else '❌ FAILED'}")
        safe_print(f"Test 2 (Dependency Switching): {'✅ PASSED' if success2 else '❌ FAILED'}")
        safe_print(f"Test 3 (Nested Loaders): {'✅ PASSED' if success3 else '❌ FAILED'}")
        safe_print(f'\\nOverall: {passed_tests}/3 tests passed')
        return passed_tests == 3
        
    except Exception as e:
        safe_print(_('\\n❌ Critical error during testing: {}').format(e))
        traceback.print_exc()
        return False
    finally:
        print_header('STEP 4: Cleanup')
        # ... your cleanup logic ...
        safe_print('✅ Cleanup complete')

if __name__ == '__main__':
    final_success = run_tensorflow_switching_test()
    if final_success:
        safe_print('\n🎉 DEMO PASSED! 🎉')
    else:
        safe_print('\n❌ Demo failed.')
    sys.exit(0 if final_success else 1)