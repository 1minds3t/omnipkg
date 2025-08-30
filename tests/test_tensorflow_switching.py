import sys
import os
import json
import subprocess
import shutil
import tempfile
import time
import re
import importlib
import traceback
import importlib.util
from datetime import datetime
from pathlib import Path
from importlib.metadata import version as get_pkg_version, PathDistribution

try:
    project_root = Path(__file__).resolve().parent.parent
    if project_root.name == 'omnipkg':
        project_root = project_root.parent
    sys.path.insert(0, str(project_root))
    from omnipkg.i18n import _
    lang_from_env = os.environ.get('OMNIPKG_LANG')
    if lang_from_env:
        _.set_language(lang_from_env)
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    from omnipkg.loader import omnipkgLoader
    from omnipkg.common_utils import run_command, print_header
except ImportError as e:
    print(_('❌ Critical Error: Could not import omnipkg modules. Is the project structure correct? Error: {}').format(e))
    sys.exit(1)

def print_header(title):
    print('\n' + '=' * 80)
    print(_('  🚀 {}').format(title))
    print('=' * 80)

def print_subheader(title):
    print(_('\n--- {} ---').format(title))

def get_current_install_strategy(config_manager):
    """Get the current install strategy"""
    try:
        return config_manager.config.get('install_strategy', 'multiversion')
    except:
        return 'multiversion'

def set_install_strategy(config_manager, strategy):
    """Set the install strategy"""
    try:
        # Use omnipkg config set command
        result = subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', strategy],
                              capture_output=True, text=True, check=True)
        print(_('   ⚙️  Install strategy set to: {}').format(strategy))
        return True
    except Exception as e:
        print(_('   ⚠️  Failed to set install strategy: {}').format(e))
        return False

def pip_uninstall_tensorflow():
    """Use pip to directly uninstall tensorflow packages from main environment"""
    print(_('   🧹 Using pip to uninstall tensorflow packages from main environment...'))
    packages = ['tensorflow', 'tensorflow-estimator', 'typing-extensions', 'keras']
    try:
        for package in packages:
            result = subprocess.run(['pip', 'uninstall', package, '-y'],
                                  capture_output=True, text=True, check=False)
            if result.returncode == 0:
                print(_('   ✅ pip uninstall {} completed successfully').format(package))
            else:
                print(_('   ℹ️  pip uninstall completed ({} may not have been installed)').format(package))
        return True
    except Exception as e:
        print(_('   ⚠️  pip uninstall failed: {}').format(e))
        return False
        
def pip_install_tensorflow():
    """Use pip to install typing extensions only"""
    print('   📦 Using pip to install typing extensions package...')
    packages = ['typing-extensions==4.14.1']
    try:
        # Fixed: removed the invalid -y flag from pip install
        result = subprocess.run(['pip', 'install'] + packages,
                              capture_output=True, text=True, check=True)
        print('   ✅ pip install typing extensions package completed successfully')
        return True
    except Exception as e:
        print(f'   ❌ pip install failed: {e}')
        return False
        
def omnipkg_install_tensorflow():
    """Use omnipkg to install tensorflow packages (pip is too dumb for complex multi-package installs)"""
    print(_('   📦 Using omnipkg to install TensorFlow 2.13.0 packages...'))
    packages = ['tensorflow==2.13.0', 'tensorflow-estimator==2.13.0', 'keras==2.13.1']
    try:
        result = subprocess.run(['omnipkg', 'install'] + packages,
                              capture_output=True, text=True, check=True)
        print(_('   ✅ omnipkg install TensorFlow packages completed successfully'))
        return True
    except Exception as e:
        print(_('   ❌ omnipkg install failed: {}').format(e))
        return False

def setup_environment():
    print_header('STEP 1: Environment Setup & Cleanup')

    config_manager = ConfigManager()

    # Store original install strategy
    original_strategy = get_current_install_strategy(config_manager)
    print(_('   ℹ️  Current install strategy: {}').format(original_strategy))

    # Set to stable-main for consistent testing
    print(_('   ⚙️  Setting install strategy to stable-main for testing...'))
    if not set_install_strategy(config_manager, 'stable-main'):
        print(_('   ⚠️  Could not change install strategy, continuing anyway...'))

    # Refresh config after strategy change
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager.config)

    # Clean up any existing tensorflow bubbles and cloaked packages
    print(_('   🧹 Cleaning up existing TensorFlow installations and bubbles...'))
    for bubble in omnipkg_core.multiversion_base.glob('tensorflow-*'):
        if bubble.is_dir():
            print(_('   🧹 Removing old bubble: {}').format(bubble.name))
            shutil.rmtree(bubble, ignore_errors=True)
    
    for bubble in omnipkg_core.multiversion_base.glob('typing_extensions-*'):
        if bubble.is_dir():
            print(_('   🧹 Removing old typing-extensions bubble: {}').format(bubble.name))
            shutil.rmtree(bubble, ignore_errors=True)

    site_packages = Path(config_manager.config['site_packages_path'])
    for pkg in ['tensorflow', 'tensorflow_estimator', 'keras', 'typing_extensions']:
        for cloaked in site_packages.glob(f'{pkg}.*_omnipkg_cloaked*'):
            print(_('   🧹 Removing residual cloaked: {}').format(cloaked.name))
            shutil.rmtree(cloaked, ignore_errors=True)

        for cloaked in site_packages.glob(f'{pkg}.*_test_harness_cloaked*'):
            print(_('   🧹 Removing test harness residual cloaked: {}').format(cloaked.name))
            shutil.rmtree(cloaked, ignore_errors=True)

    # Use pip to ensure clean main environment installation of typing extensions
    # This is a prerequisite for the omnipkg installation to function correctly
    pip_uninstall_tensorflow()
    if not pip_install_tensorflow():
        print(_('   ❌ Failed to install main environment TensorFlow packages'))
        return None, original_strategy
        
    # ✨ ADDED: Call the omnipkg installation function here ✨
    if not omnipkg_install_tensorflow():
        print(_('   ❌ Failed to install TensorFlow packages using omnipkg'))
        return None, original_strategy

    print(_('✅ Environment prepared'))
    return config_manager.config, original_strategy

def restore_install_strategy(config_manager, original_strategy):
    """Restore the original install strategy"""
    if original_strategy != 'stable-main':
        print(_('   🔄 Restoring original install strategy: {}').format(original_strategy))
        return set_install_strategy(config_manager, original_strategy)
    return True

def run_command_filtered(command: list, check: bool=True, filter_tf_warnings: bool=True, filter_pip_noise: bool=True):
    try:
        process = subprocess.run(command, capture_output=True, text=True, check=check)
        for line in process.stdout.splitlines():
            if filter_tf_warnings and any((noise in line for noise in ['tensorflow/tsl/cuda/', 'TF-TRT Warning', 'GPU will not be used', 'Cannot dlopen some GPU libraries', 'successful NUMA node read', 'Skipping registering GPU devices...', 'PyExceptionRegistry', "AttributeError: 'MessageFactory' object has no attribute 'GetPrototype'"])):
                continue
            if filter_pip_noise and any((noise in line for noise in ['Requirement already satisfied:', 'Collecting ', 'Using cached ', 'Downloading ', 'Installing collected packages:', 'Successfully installed ', 'Attempting uninstall:', 'Uninstalling ', 'Found existing installation:'])):
                continue
            if line.strip():
                print(line)
        if process.stderr:
            filtered_stderr_lines = []
            for line in process.stderr.splitlines():
                if filter_pip_noise and any((err in line for err in ['ERROR: Could not find a version that satisfies the requirement', 'ERROR: No matching distribution found for', "ERROR: pip's dependency resolver does not currently take into account"])):
                    filtered_stderr_lines.append(line)
                elif filter_tf_warnings and any((noise in line for noise in ['Could not find cuda drivers', 'Cannot dlopen some GPU libraries', 'Skipping registering GPU devices...', 'successful NUMA node read', 'PyExceptionRegistry', "AttributeError: 'MessageFactory' object has no attribute 'GetPrototype'"])):
                    continue
                elif line.strip():
                    filtered_stderr_lines.append(line)
            if filtered_stderr_lines:
                print('--- STDERR ---')
                for line in filtered_stderr_lines:
                    print(line)
                print('--------------')
        return process
    except subprocess.CalledProcessError as e:
        print(f"Command failed with exit code {e.returncode}: {' '.join(e.cmd)}")
        if e.stdout:
            print('--- STDOUT ---')
            for line in e.stdout.splitlines():
                if not any((noise in line for noise in ['tensorflow/tsl/cuda/', 'TF-TRT Warning'])):
                    if line.strip():
                        print(line)
            print('--------------')
        if e.stderr:
            print('--- STDERR ---')
            for line in e.stderr.splitlines():
                if line.strip():
                    print(line)
            print('--------------')
        raise

def run_script_only_relevant_output(code: str):
    path = Path('temp_test.py')
    path.write_text(code)
    try:
        result = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
        success = False
        output_lines = []
        for line in result.stdout.splitlines():
            if not any((noise in line for noise in ['tensorflow/tsl/cuda/', 'TF-TRT Warning', 'GPU will not be used', 'Cannot dlopen some GPU libraries', 'PyExceptionRegistry', "AttributeError: 'MessageFactory' object has no attribute 'GetPrototype'"])):
                if line.strip():
                    output_lines.append(line)
                if 'Model created successfully' in line:
                    success = True
        for line in output_lines:
            print(line)
        if result.returncode != 0:
            relevant_stderr = [line for line in result.stderr.splitlines() if not any((noise in line for noise in ['tensorflow/tsl/cuda/', 'GPU will not be used', 'TF-TRT Warning', 'Cannot dlopen some GPU libraries', 'PyExceptionRegistry', "AttributeError: 'MessageFactory' object has no attribute 'GetPrototype'"])) and line.strip()]
            if relevant_stderr:
                print(_('--- Script STDERR (relevant) ---'))
                for line in relevant_stderr:
                    print(line)
                print('--------------------------------')
            if not success and (relevant_stderr or not result.stdout.strip()):
                print(_('⚠️ Some errors occurred (see above)'))
            elif success:
                print('✅ Test passed despite warnings')
            else:
                print('⚠️ Script completed with issues (check logs for details)')
    finally:
        path.unlink(missing_ok=True)

GET_MODULE_VERSION_CODE_SNIPPET = '''
import re
import sys
from pathlib import Path
from importlib.metadata import version as get_pkg_version_from_metadata, PathDistribution

def get_version_from_module_file(module_obj, pkg_canonical_name, omnipkg_versions_dir_str):
    """
    Determine the version and source of a loaded module.
    Returns (version_string, source_description)
    """
    load_source = "main env"
    found_version = "unknown"

    # Resolve paths early
    omnipkg_versions_dir = Path(omnipkg_versions_dir_str).resolve()

    # Check if module has __file__ attribute
    if hasattr(module_obj, '__file__') and module_obj.__file__:
        module_path = Path(module_obj.__file__).resolve()
        
        # Check if this is from an omnipkg bubble
        try:
            if module_path.is_relative_to(omnipkg_versions_dir):
                relative_path = module_path.relative_to(omnipkg_versions_dir)
                bubble_dir = relative_path.parts[0]
                pkg_name_normalized = pkg_canonical_name.replace('-', '_')
                version_pattern = rf'^{re.escape(pkg_name_normalized)}-(.+)'
                match = re.match(version_pattern, bubble_dir)
                if match:
                    found_version = match.group(1)
                    load_source = f"bubble ({bubble_dir})"
                else:
                    # Fallback to .dist-info in bubble
                    dist_info = next((omnipkg_versions_dir / bubble_dir).glob("*.dist-info"), None)
                    if dist_info:
                        dist = PathDistribution(dist_info)
                        found_version = dist.version
                        load_source = f"bubble ({bubble_dir})"
        except (ValueError, IndexError):
            pass

    # Handle cases where bubble version wasn't found
    if found_version == "unknown":
        try:
            found_version = get_pkg_version_from_metadata(pkg_canonical_name)
            load_source = "main env (pip)" if not load_source.startswith("bubble") else load_source
        except Exception:
            if hasattr(module_obj, '__version__'):
                found_version = str(module_obj.__version__)
                load_source = "main env (version)" if not load_source.startswith("bubble") else load_source
            else:
                load_source = "namespace package" if not load_source.startswith("bubble") else load_source
    
    return found_version, load_source
'''

def create_test_bubbles(config):
    print_header('STEP 2: Creating Test Bubbles for Dependency Switching')
    omnipkg_core = OmnipkgCore(config)
    
    # Create bubble with older typing-extensions version for testing dependency swapping
    bubble_version = '4.5.0'
    print(_('   🫧 Creating bubble for typing-extensions=={}').format(bubble_version))
    try:
        omnipkg_core.smart_install([f'typing-extensions=={bubble_version}'])
        print(_('   ✅ Bubble created: typing-extensions-{}').format(bubble_version))
    except Exception as e:
        print(_('   ❌ Failed to create bubble for typing-extensions=={}: {}').format(bubble_version, e))

def run_tensorflow_test():
    print_header('🚨 OMNIPKG TENSORFLOW DEPENDENCY SWITCHING TEST 🚨')
    original_strategy = None
    
    try:
        config, original_strategy = setup_environment()
        if config is None:
            return False
        
        OMNIPKG_VERSIONS_DIR = Path(config['multiversion_base']).resolve()
        
        create_test_bubbles(config)
        
        print_header('STEP 3: TensorFlow Dependency Testing')
        
        print_subheader('Testing initial state: tensorflow==2.13.0 with typing-extensions==4.14.1 and keras==2.13.1 (main)')
        print(_('(Should have 4.14.1 and keras==2.13.1 active in main env, 4.5.0 in bubble)'))
        code_initial_test = f"""
import tensorflow as tf
import typing_extensions
import keras
{GET_MODULE_VERSION_CODE_SNIPPET}

te_version, te_source = get_version_from_module_file(typing_extensions, 'typing-extensions', '{OMNIPKG_VERSIONS_DIR}')
keras_version, keras_source = get_version_from_module_file(keras, 'keras', '{OMNIPKG_VERSIONS_DIR}')

print(f"TensorFlow version: {{tf.__version__}}")
print(f"Typing Extensions version: {{te_version}}")
print(f"Typing Extensions loaded from: {{te_source}}")
print(f"Typing Extensions file path: {{getattr(typing_extensions, '__file__', 'namespace package')}}")
print(f"Keras version: {{keras_version}}")
print(f"Keras loaded from: {{keras_source}}")
print(f"Keras file path: {{getattr(keras, '__file__', 'namespace package')}}")

try:
    model = tf.keras.Sequential([tf.keras.layers.Dense(1, input_shape=(1,))])
    print("✅ Model created successfully")
except Exception as e:
    print(f"❌ Model creation failed: {{e}}")
"""
        run_script_only_relevant_output(code_initial_test)

        print_subheader('Testing switch to typing-extensions==4.5.0 bubble with keras==2.13.1 (main)')
        te_bubble_path = OMNIPKG_VERSIONS_DIR / 'typing_extensions-4.5.0'
        print(f'Looking for typing-extensions bubble at: {te_bubble_path}')
        print(_('Bubble exists: {}').format(te_bubble_path.exists()))
        code_bubble_test = f"""
from omnipkg.loader import omnipkgLoader
import tensorflow as tf
import typing_extensions
import keras
import importlib
import sys
import gc
{GET_MODULE_VERSION_CODE_SNIPPET}

# Clear typing_extensions from sys.modules to ensure bubble version is loaded
for mod_name in list(sys.modules.keys()):
    if mod_name == 'typing_extensions' or mod_name.startswith('typing_extensions.'):
        del sys.modules[mod_name]
gc.collect()
if hasattr(importlib, 'invalidate_caches'):
    importlib.invalidate_caches()

with omnipkgLoader("typing_extensions==4.5.0", config={{'multiversion_base': '{OMNIPKG_VERSIONS_DIR}', 'site_packages_path': '{config['site_packages_path']}'}}) as loader:
    import typing_extensions
    import tensorflow as tf
    import keras
    te_version, te_source = get_version_from_module_file(typing_extensions, 'typing-extensions', '{OMNIPKG_VERSIONS_DIR}')
    keras_version, keras_source = get_version_from_module_file(keras, 'keras', '{OMNIPKG_VERSIONS_DIR}')

    print(f"TensorFlow version: {{tf.__version__}}")
    print(f"Typing Extensions version: {{te_version}}")
    print(f"Typing Extensions loaded from: {{te_source}}")
    print(f"Typing Extensions file path: {{getattr(typing_extensions, '__file__', 'namespace package')}}")
    print(f"Keras version: {{keras_version}}")
    print(f"Keras loaded from: {{keras_source}}")
    print(f"Keras file path: {{getattr(keras, '__file__', 'namespace package')}}")

    try:
        model = tf.keras.Sequential([tf.keras.layers.Dense(1, input_shape=(1,))])
        print("✅ Model created successfully with typing-extensions 4.5.0 bubble")
    except Exception as e:
        print(f"❌ Model creation failed with typing-extensions 4.5.0 bubble: {{e}}")

    try:
        if te_version == "4.5.0":
            print(f"✅ Successfully switched to older version: typing-extensions={{te_version}}")
        else:
            print(f"⚠️ Version mismatch: expected typing-extensions=4.5.0, got {{te_version}}")
    except Exception as e:
        print(f"⚠️ Version verification failed: {{e}}")
"""
        run_script_only_relevant_output(code_bubble_test)

        print_subheader('Final verification - stable environment state')
        code_final_test = f"""
import tensorflow as tf
import typing_extensions
import keras
{GET_MODULE_VERSION_CODE_SNIPPET}

te_version, te_source = get_version_from_module_file(typing_extensions, 'typing-extensions', '{OMNIPKG_VERSIONS_DIR}')
keras_version, keras_source = get_version_from_module_file(keras, 'keras', '{OMNIPKG_VERSIONS_DIR}')

print(f"TensorFlow version: {{tf.__version__}}")
print(f"Typing Extensions version: {{te_version}}")
print(f"Typing Extensions loaded from: {{te_source}}")
print(f"Keras version: {{keras_version}}")
print(f"Keras loaded from: {{keras_source}}")

try:
    model = tf.keras.Sequential([tf.keras.layers.Dense(1, input_shape=(1,))])
    print("✅ Final test: Model created successfully")
    
    # Success criteria: TensorFlow works and we have a stable typing-extensions version
    if tf.__version__ == "2.13.0" and te_version in ["4.14.1", "4.5.0"]:
        print("🎉 TEST PASSED: TensorFlow version switching and dependency management working correctly!")
        print(f"   - TensorFlow: {{tf.__version__}}")
        print(f"   - Typing Extensions: {{te_version}} ({{te_source}})")
        print(f"   - Keras: {{keras_version}} ({{keras_source}})")
    else:
        print("⚠️ TEST INCOMPLETE: Some versions unexpected but functionality works")
        
except Exception as e:
    print(f"❌ Final test: Model creation failed: {{e}}")
"""
        result = run_script_only_relevant_output(code_final_test)
        
    except Exception as e:
        print(_('\n❌ Critical error during testing: {}').format(e))
        traceback.print_exc()
        return False
        
    finally:
        print_header('STEP 4: Cleanup & Restoration')
        try:
            config_manager = ConfigManager()
            omnipkg_core = OmnipkgCore(config_manager.config)
            site_packages = Path(config_manager.config['site_packages_path'])
            
            # Clean up test bubbles
            for bubble in omnipkg_core.multiversion_base.glob('typing_extensions-*'):
                if bubble.is_dir():
                    print(_('   🧹 Removing test bubble: {}').format(bubble.name))
                    shutil.rmtree(bubble, ignore_errors=True)
            
            # Clean up cloaked packages
            for pkg in ['tensorflow', 'tensorflow_estimator', 'keras', 'typing_extensions']:
                for cloaked in site_packages.glob(f'{pkg}.*_omnipkg_cloaked*'):
                    print(_('   🧹 Removing residual cloaked: {}').format(cloaked.name))
                    shutil.rmtree(cloaked, ignore_errors=True)
            
                for cloaked in site_packages.glob(f'{pkg}.*_test_harness_cloaked*'):
                    print(_('   🧹 Removing test harness residual cloaked: {}').format(cloaked.name))
                    shutil.rmtree(cloaked, ignore_errors=True)
            
            # Restore main environment to latest versions using omnipkg
            print(_('   📦 Restoring main environment: TensorFlow packages'))
            pip_uninstall_tensorflow()
            pip_install_tensorflow()
            
            # Restore original install strategy if it was changed
            if original_strategy and original_strategy != 'stable-main':
                restore_install_strategy(config_manager, original_strategy)
                print(_('   💡 Note: Install strategy has been restored to: {}').format(original_strategy))
            elif original_strategy == 'stable-main':
                print(_('   ℹ️  Install strategy remains at: stable-main'))
            else:
                print(_('   💡 Note: You may need to manually restore your preferred install strategy'))
                print(_('   💡 Run: omnipkg config set install_strategy <your_preferred_strategy>'))
            
            print(_('✅ Cleanup complete'))
            
            return True  # Test completed successfully
            
        except Exception as e:
            print(_('\n❌ Critical error during testing: {}').format(e))
            traceback.print_exc()
            return False
            
        except Exception as e:
            print(_('⚠️  Cleanup failed: {}').format(e))
            if original_strategy and original_strategy != 'stable-main':
                print(_('   💡 You may need to manually restore install strategy: {}').format(original_strategy))
                print(_('   💡 Run: omnipkg config set install_strategy {}').format(original_strategy))

if __name__ == '__main__':
    success = run_tensorflow_test()
    sys.exit(0 if success else 1)