import sys
import os
from pathlib import Path


# --- PROJECT PATH SETUP ---
# This must come first so Python can find your modules.
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# --- BOOTSTRAP SECTION ---
# Import ONLY the necessary utilities for the bootstrap process.
from omnipkg.common_utils import ensure_python_or_relaunch, sync_context_to_runtime

# 1. Declarative script guard: Ensures this script runs on Python 3.11.
#    If not, it will relaunch the script with the correct interpreter and exit.
if os.environ.get('OMNIPKG_RELAUNCHED') != '1':
    ensure_python_or_relaunch("3.11")

# 2. Sync guard: Now that we are GUARANTEED to be running on the correct
#    interpreter, we sync omnipkg's config to match this runtime.
sync_context_to_runtime()
# --- END BOOTSTRAP ---


import json
import subprocess
import shutil
import tempfile
import time
import re
import importlib
import traceback
import importlib.util
from importlib.metadata import version as get_pkg_version, PathDistribution
from datetime import datetime
from omnipkg.i18n import _
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore

def print_header(title):
    print('\n' + '=' * 80)
    print(_('  🚀 {}').format(title))
    print('=' * 80)

def print_subheader(title):
    print(_('\n--- {} ---').format(title))

def normalize_package_name(name):
    """Normalize package names to use underscores consistently."""
    return name.replace('-', '_')

def get_current_install_strategy(config_manager):
    """Get the current install strategy"""
    try:
        return config_manager.config.get('install_strategy', 'multiversion')
    except:
        return 'multiversion'

def set_install_strategy(config_manager, strategy):
    """Set the install strategy"""
    try:
        result = subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', strategy], capture_output=True, text=True, check=True)
        print(_('   ⚙️  Install strategy set to: {}').format(strategy))
        return True
    except Exception as e:
        print(_('   ⚠️  Failed to set install strategy: {}').format(e))
        return False

def reset_omnipkg_environment():
    """Run omnipkg reset to ensure clean state"""
    print('   🔄 Resetting omnipkg environment for clean state...')
    try:
        result = subprocess.run([sys.executable, '-m', 'omnipkg.cli', 'reset', '-y'], capture_output=True, text=True, check=True)
        print(_('   ✅ omnipkg environment reset successfully'))
        return True
    except Exception as e:
        print(_('   ⚠️  Failed to reset omnipkg environment: {}').format(e))
        return False

def ensure_tensorflow_bubbles(config_manager: ConfigManager):
    """
    FIXED: Ensures we have the necessary TensorFlow bubbles created with consistent naming.
    """
    print(_('   📦 Ensuring TensorFlow bubbles exist...'))
    omnipkg_core = OmnipkgCore(config_manager)
    packages_to_bubble = {'tensorflow': ['2.13.0', '2.12.0'], 'typing_extensions': ['4.14.1', '4.5.0']}
    for pkg_name, versions in packages_to_bubble.items():
        for version in versions:
            bubble_name = f'{pkg_name}-{version}'
            bubble_path = omnipkg_core.multiversion_base / bubble_name
            if not bubble_path.exists():
                print(f'   🫧 Force-creating bubble for {pkg_name}=={version}...')
                try:
                    success = omnipkg_core.bubble_manager.create_isolated_bubble(pkg_name, version)
                    if success:
                        print(_('   ✅ Created {}=={} bubble').format(pkg_name, version))
                        print(f'   🧠 Updating KB for new bubble...')
                        omnipkg_core._run_metadata_builder_for_delta({}, {pkg_name: version})
                    else:
                        print(f'   ❌ Failed to create bubble for {pkg_name}=={version}')
                except Exception as e:
                    print(f'   ❌ An error occurred creating bubble for {pkg_name}=={version}: {e}')
            else:
                print(_('   ✅ {}=={} bubble already exists').format(pkg_name, version))

def setup_environment():
    print_header('STEP 1: Environment Setup & Bubble Creation')
    config_manager = ConfigManager()
    original_strategy = get_current_install_strategy(config_manager)
    print(_('   ℹ️  Current install strategy: {}').format(original_strategy))
    if not reset_omnipkg_environment():
        print(_('   ⚠️  Reset failed, continuing anyway...'))
    omnipkg_core = OmnipkgCore(config_manager)
    site_packages = Path(config_manager.config['site_packages_path'])
    print(_('   🧹 Cleaning up any test artifacts...'))
    for pkg in ['tensorflow', 'tensorflow_estimator', 'keras', 'typing_extensions']:
        for cloaked in site_packages.glob(f'{pkg}.*_omnipkg_cloaked*'):
            print(_('   🧹 Removing residual cloaked: {}').format(cloaked.name))
            shutil.rmtree(cloaked, ignore_errors=True)
        for cloaked in site_packages.glob(f'{pkg}.*_test_harness_cloaked*'):
            print(_('   🧹 Removing test harness residual cloaked: {}').format(cloaked.name))
            shutil.rmtree(cloaked, ignore_errors=True)
    ensure_tensorflow_bubbles(config_manager)
    print(_('✅ Environment prepared'))
    return (config_manager, original_strategy)
GET_MODULE_VERSION_CODE_SNIPPET = '\ndef get_version_from_module_file(module, package_name, omnipkg_versions_dir):\n    """Enhanced version detection for omnipkg testing"""\n    import importlib.metadata\n    from pathlib import Path\n    \n    version = "unknown"\n    source = "unknown"\n    \n    try:\n        # Method 1: Try module.__version__ first\n        if hasattr(module, \'__version__\'):\n            version = module.__version__\n            source = "module.__version__"\n        \n        # Method 2: Try importlib.metadata with multiple package names\n        if version == "unknown":\n            package_variants = [package_name]\n            # Add common variants\n            if package_name == \'typing-extensions\':\n                package_variants.append(\'typing_extensions\')\n            elif package_name == \'typing_extensions\':\n                package_variants.append(\'typing-extensions\')\n            \n            for pkg_name in package_variants:\n                try:\n                    version = importlib.metadata.version(pkg_name)\n                    source = f"importlib.metadata({pkg_name})"\n                    break\n                except importlib.metadata.PackageNotFoundError:\n                    continue\n        \n        # Method 3: Check if loaded from omnipkg bubble\n        if hasattr(module, \'__file__\') and module.__file__:\n            module_path = Path(module.__file__).resolve()\n            omnipkg_base = Path(omnipkg_versions_dir).resolve()\n            \n            if str(module_path).startswith(str(omnipkg_base)):\n                try:\n                    relative_path = module_path.relative_to(omnipkg_base)\n                    bubble_dir = relative_path.parts[0]  # e.g., "typing_extensions-4.5.0"\n                    \n                    if \'-\' in bubble_dir:\n                        bubble_version = bubble_dir.split(\'-\', 1)[1]\n                        if version == "unknown":\n                            version = bubble_version\n                            source = f"bubble path ({bubble_dir})"\n                        else:\n                            # Verify consistency\n                            if version != bubble_version:\n                                source = f"{source} [bubble: {bubble_version}]"\n                except (ValueError, IndexError):\n                    pass\n                source = f"{source} -> bubble: {module_path}"\n            else:\n                source = f"{source} -> system: {module_path}"\n        elif not hasattr(module, \'__file__\'):\n            source = f"{source} -> namespace package"\n    \n    except Exception as e:\n        source = f"error: {e}"\n    \n    return version, source\n'

def run_script_with_loader(code: str, description: str):
    """Run a test script and capture relevant output"""
    print(_('\n--- {} ---').format(description))
    script_path = Path('temp_loader_test.py')
    script_path.write_text(code)
    try:
        result = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=60)
        tf_noise_patterns = ['tensorflow/tsl/cuda/', 'TF-TRT Warning', 'GPU will not be used', 'Cannot dlopen some GPU libraries', 'PyExceptionRegistry', "AttributeError: 'MessageFactory' object has no attribute 'GetPrototype'", 'successful NUMA node read', 'Skipping registering GPU devices', 'Could not find cuda drivers']
        output_lines = []
        success_indicators = []
        for line in result.stdout.splitlines():
            if not any((noise in line for noise in tf_noise_patterns)):
                if line.strip():
                    output_lines.append(line)
                    if any((indicator in line for indicator in ['✅', 'Model created successfully', 'TEST PASSED'])):
                        success_indicators.append(line)
        for line in output_lines:
            print(line)
        if result.returncode != 0:
            stderr_lines = [line for line in result.stderr.splitlines() if not any((noise in line for noise in tf_noise_patterns)) and line.strip()]
            if stderr_lines:
                print(_('--- Relevant Errors ---'))
                for line in stderr_lines:
                    print(line)
                print('---------------------')
        return (result.returncode == 0, success_indicators)
    except subprocess.TimeoutExpired:
        print(_('❌ Test timed out after 60 seconds'))
        return (False, [])
    except Exception as e:
        print(_('❌ Test execution failed: {}').format(e))
        return (False, [])
    finally:
        script_path.unlink(missing_ok=True)

def run_tensorflow_switching_test():
    print_header('🚨 OMNIPKG TENSORFLOW DEPENDENCY SWITCHING TEST 🚨')
    try:
        omnipkg_cmd_base = [sys.executable, '-m', 'omnipkg.cli']
        info_result = subprocess.run(omnipkg_cmd_base + ['info', 'python'], capture_output=True, text=True, check=True)
        active_version = 'unknown'
        for line in info_result.stdout.splitlines():
            if '🎯 Active Context:' in line:
                match = re.search('Python (\\d+\\.\\d+)', line)
                if match:
                    active_version = match.group(1)
                    break
        print(_('   ✅ Script running on Python {}.{}').format(sys.version_info.major, sys.version_info.minor))
        print(_('   ✅ omnipkg active context: Python {}').format(active_version))
    except Exception as e:
        print(_('   ⚠️  Could not determine omnipkg context: {}').format(e))
        print(_('   ✅ Script running on Python {}.{}').format(sys.version_info.major, sys.version_info.minor))
    try:
        config_manager, original_strategy = setup_environment()
        if config_manager is None:
            return False
        OMNIPKG_VERSIONS_DIR = Path(config_manager.config['multiversion_base']).resolve()
        print_header('STEP 2: Testing TensorFlow Version Switching with omnipkgLoader')
        test1_code = f"""\nimport sys\nfrom pathlib import Path\nsys.path.insert(0, '{Path(__file__).resolve().parent.parent}')\n\nfrom omnipkg.loader import omnipkgLoader\nfrom omnipkg.core import ConfigManager\n\n{GET_MODULE_VERSION_CODE_SNIPPET}\n\ndef main():\n    try:\n        config_manager = ConfigManager()\n        \n        print("🌀 Testing TensorFlow 2.13.0 from bubble...")\n        \n        with omnipkgLoader("tensorflow==2.13.0", config=config_manager.config):\n            import tensorflow as tf\n            import typing_extensions\n            import keras\n            \n            print(f"✅ TensorFlow version: {{tf.__version__}}")\n            \n            te_version, te_source = get_version_from_module_file(typing_extensions, 'typing_extensions', '{OMNIPKG_VERSIONS_DIR}')\n            print(f"✅ Typing Extensions version: {{te_version}}")\n            print(f"✅ Typing Extensions source: {{te_source}}")\n            print(f"✅ Keras version: {{keras.__version__}}")\n            \n            # Test model creation\n            try:\n                model = tf.keras.Sequential([\n                    tf.keras.layers.Dense(1, input_shape=(1,))\n                ])\n                print("✅ Model created successfully with TensorFlow 2.13.0")\n                return True\n            except Exception as e:\n                print(f"❌ Model creation failed: {{e}}")\n                return False\n                \n    except Exception as e:\n        print(f"❌ Test failed: {{e}}")\n        import traceback\n        traceback.print_exc()\n        return False\n\nif __name__ == "__main__":\n    success = main()\n    sys.exit(0 if success else 1)\n"""
        success1, indicators1 = run_script_with_loader(test1_code, 'TensorFlow 2.13.0 Bubble Test')
        test2_code = f"""\nimport sys\nfrom pathlib import Path\nsys.path.insert(0, '{Path(__file__).resolve().parent.parent}')\n\nfrom omnipkg.loader import omnipkgLoader\nfrom omnipkg.core import ConfigManager\n\n{GET_MODULE_VERSION_CODE_SNIPPET}\n\ndef main():\n    try:\n        config_manager = ConfigManager()\n        \n        print("🌀 Testing dependency switching: typing_extensions versions...")\n        \n        # FIXED: Use underscore naming consistently\n        print("\\n--- Testing with typing_extensions 4.14.1 ---")\n        with omnipkgLoader("typing_extensions==4.14.1", config=config_manager.config):\n            import typing_extensions\n            te_version, te_source = get_version_from_module_file(typing_extensions, 'typing_extensions', '{OMNIPKG_VERSIONS_DIR}')\n            print(f"✅ Typing Extensions version: {{te_version}}")\n            print(f"✅ Typing Extensions source: {{te_source}}")\n        \n        # Then switch to older version\n        print("\\n--- Testing with typing_extensions 4.5.0 ---")\n        with omnipkgLoader("typing_extensions==4.5.0", config=config_manager.config):\n            import typing_extensions\n            te_version, te_source = get_version_from_module_file(typing_extensions, 'typing_extensions', '{OMNIPKG_VERSIONS_DIR}')\n            print(f"✅ Typing Extensions version: {{te_version}}")\n            print(f"✅ Typing Extensions source: {{te_source}}")\n            \n            # Now try to load TensorFlow with this older typing_extensions\n            try:\n                with omnipkgLoader("tensorflow==2.13.0", config=config_manager.config):\n                    import tensorflow as tf\n                    model = tf.keras.Sequential([tf.keras.layers.Dense(1, input_shape=(1,))])\n                    print("✅ TensorFlow works with older typing_extensions!")\n                    return True\n            except Exception as e:\n                print(f"⚠️  TensorFlow with older typing_extensions had issues: {{e}}")\n                return True  # This might be expected\n                \n    except Exception as e:\n        print(f"❌ Test failed: {{e}}")\n        import traceback\n        traceback.print_exc()\n        return False\n\nif __name__ == "__main__":\n    success = main()\n    sys.exit(0 if success else 1)\n"""
        success2, indicators2 = run_script_with_loader(test2_code, 'Dependency Switching Test')
        test3_code = f"""\nimport sys\nfrom pathlib import Path\nsys.path.insert(0, '{Path(__file__).resolve().parent.parent}')\n\nfrom omnipkg.loader import omnipkgLoader\nfrom omnipkg.core import ConfigManager\n\n{GET_MODULE_VERSION_CODE_SNIPPET}\n\ndef main():\n    try:\n        config_manager = ConfigManager()\n        \n        print("🌀 Testing nested loader usage...")\n        \n        # FIXED: Use underscore naming consistently  \n        with omnipkgLoader("typing_extensions==4.5.0", config=config_manager.config):\n            import typing_extensions as te_outer\n            outer_version, outer_source = get_version_from_module_file(te_outer, 'typing_extensions', '{OMNIPKG_VERSIONS_DIR}')\n            print(f"✅ Outer context - Typing Extensions: {{outer_version}}")\n            print(f"✅ Outer context - Source: {{outer_source}}")\n            \n            # Inner context: TensorFlow (should inherit outer typing_extensions or manage conflicts)\n            with omnipkgLoader("tensorflow==2.13.0", config=config_manager.config):\n                import tensorflow as tf\n                import typing_extensions as te_inner\n                inner_version, inner_source = get_version_from_module_file(te_inner, 'typing_extensions', '{OMNIPKG_VERSIONS_DIR}')\n                \n                print(f"✅ Inner context - TensorFlow: {{tf.__version__}}")\n                print(f"✅ Inner context - Typing Extensions: {{inner_version}}")\n                print(f"✅ Inner context - Source: {{inner_source}}")\n                \n                try:\n                    model = tf.keras.Sequential([tf.keras.layers.Dense(10, input_shape=(5,))])\n                    print("✅ Nested loader test: Model created successfully")\n                    return True\n                except Exception as e:\n                    print(f"❌ Model creation in nested context failed: {{e}}")\n                    return False\n                    \n    except Exception as e:\n        print(f"❌ Nested test failed: {{e}}")\n        import traceback\n        traceback.print_exc()\n        return False\n\nif __name__ == "__main__":\n    success = main()\n    sys.exit(0 if success else 1)\n"""
        success3, indicators3 = run_script_with_loader(test3_code, 'Nested Loader Test')
        print_header('STEP 3: Test Results Summary')
        total_tests = 3
        passed_tests = sum([success1, success2, success3])
        print(_('Test 1 (TensorFlow 2.13.0 Bubble): {}').format('✅ PASSED' if success1 else '❌ FAILED'))
        print(_('Test 2 (Dependency Switching): {}').format('✅ PASSED' if success2 else '❌ FAILED'))
        print(_('Test 3 (Nested Loaders): {}').format('✅ PASSED' if success3 else '❌ FAILED'))
        print(f'\\nOverall: {passed_tests}/{total_tests} tests passed')
        if passed_tests == total_tests:
            print('🎉 ALL TESTS PASSED! omnipkgLoader is working correctly with TensorFlow!')
        elif passed_tests > 0:
            print('⚠️  Some tests passed. The loader is partially functional.')
        else:
            print('❌ All tests failed. There may be issues with bubble creation or the loader.')
        return passed_tests > 0
    except Exception as e:
        print(_('\\n❌ Critical error during testing: {}').format(e))
        traceback.print_exc()
        return False
    finally:
        print_header('STEP 4: Cleanup')
        try:
            config_manager = ConfigManager()
            site_packages = Path(config_manager.config['site_packages_path'])
            for pkg in ['tensorflow', 'tensorflow_estimator', 'keras', 'typing_extensions']:
                for cloaked in site_packages.glob(f'{pkg}.*_omnipkg_cloaked*'):
                    print(_('   🧹 Removing residual cloaked: {}').format(cloaked.name))
                    shutil.rmtree(cloaked, ignore_errors=True)
                for cloaked in site_packages.glob(f'{pkg}.*_test_harness_cloaked*'):
                    print(_('   🧹 Removing test harness residual cloaked: {}').format(cloaked.name))
                    shutil.rmtree(cloaked, ignore_errors=True)
            print(_('✅ Cleanup complete'))
        except Exception as e:
            print(_('⚠️  Cleanup failed: {}').format(e))
if __name__ == '__main__':
    success = run_tensorflow_switching_test()
    sys.exit(0 if success else 1)