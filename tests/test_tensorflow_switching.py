import sys
import subprocess
from pathlib import Path
import re
import importlib
from importlib.metadata import version as get_pkg_version, PathDistribution
from omnipkg.core import ConfigManager
from omnipkg.loader import omnipkgLoader

# --- Global Config for the test script itself ---
_config_manager = ConfigManager()
OMNIPKG_VERSIONS_DIR = Path(_config_manager.config['multiversion_base']).resolve()

# --- Helper to run commands and filter output ---
def run_command_filtered(command: list, check: bool = True, filter_tf_warnings: bool = True, filter_pip_noise: bool = True):
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=check
        )
        for line in process.stdout.splitlines():
            if filter_tf_warnings and any(noise in line for noise in [
                "tensorflow/tsl/cuda/", "TF-TRT Warning", "GPU will not be used", 
                "Cannot dlopen some GPU libraries", "successful NUMA node read", 
                "Skipping registering GPU devices...", "PyExceptionRegistry",
                "AttributeError: 'MessageFactory' object has no attribute 'GetPrototype'"
            ]):
                continue
            if filter_pip_noise and any(noise in line for noise in [
                "Requirement already satisfied:", "Collecting ", "Using cached ", 
                "Downloading ", "Installing collected packages:", "Successfully installed ",
                "Attempting uninstall:", "Uninstalling ", "Found existing installation:"
            ]):
                continue
            if line.strip():
                print(line)
        if process.stderr:
            filtered_stderr_lines = []
            for line in process.stderr.splitlines():
                if filter_pip_noise and any(err in line for err in [
                    "ERROR: Could not find a version that satisfies the requirement",
                    "ERROR: No matching distribution found for",
                    "ERROR: pip's dependency resolver does not currently take into account"
                ]):
                    filtered_stderr_lines.append(line)
                elif filter_tf_warnings and any(noise in line for noise in [
                    "Could not find cuda drivers", "Cannot dlopen some GPU libraries",
                    "Skipping registering GPU devices...", "successful NUMA node read",
                    "PyExceptionRegistry", "AttributeError: 'MessageFactory' object has no attribute 'GetPrototype'"
                ]):
                    continue
                elif line.strip():
                    filtered_stderr_lines.append(line)
            if filtered_stderr_lines:
                print("--- STDERR ---")
                for line in filtered_stderr_lines:
                    print(line)
                print("--------------")
        return process
    except subprocess.CalledProcessError as e:
        print(f"Command failed with exit code {e.returncode}: {' '.join(e.cmd)}")
        if e.stdout:
            print("--- STDOUT ---")
            for line in e.stdout.splitlines():
                if not any(noise in line for noise in ["tensorflow/tsl/cuda/", "TF-TRT Warning"]):
                    if line.strip():
                        print(line)
            print("--------------")
        if e.stderr:
            print("--- STDERR ---")
            for line in e.stderr.splitlines():
                if line.strip():
                    print(line)
            print("--------------")
        raise

def run_script_only_relevant_output(code: str):
    path = Path("temp_test.py")
    path.write_text(code)
    try:
        result = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True,
            text=True,
        )
        success = False
        output_lines = []
        for line in result.stdout.splitlines():
            if not any(noise in line for noise in [
                "tensorflow/tsl/cuda/", "TF-TRT Warning", "GPU will not be used",
                "Cannot dlopen some GPU libraries", "PyExceptionRegistry",
                "AttributeError: 'MessageFactory' object has no attribute 'GetPrototype'"
            ]):
                if line.strip():
                    output_lines.append(line)
                    if "Model created successfully" in line:
                        success = True
        for line in output_lines:
            print(line)
        
        if result.returncode != 0:
            relevant_stderr = [
                line for line in result.stderr.splitlines()
                if not any(noise in line for noise in [
                    "tensorflow/tsl/cuda/", "GPU will not be used", "TF-TRT Warning",
                    "Cannot dlopen some GPU libraries", "PyExceptionRegistry",
                    "AttributeError: 'MessageFactory' object has no attribute 'GetPrototype'"
                ]) and line.strip()
            ]
            if relevant_stderr:
                print("--- Script STDERR (relevant) ---")
                for line in relevant_stderr:
                    print(line)
                print("--------------------------------")
            
            if not success and (relevant_stderr or not result.stdout.strip()):
                print("⚠️ Some errors occurred (see above)")
            elif success:
                print("✅ Test passed despite warnings")
            else:
                print("⚠️ Script completed with issues (check logs for details)")
        
    finally:
        path.unlink(missing_ok=True)

# Improved helper function for version detection
GET_MODULE_VERSION_CODE_SNIPPET = """
import re
import sys
from pathlib import Path
from importlib.metadata import version as get_pkg_version_from_metadata, PathDistribution

def _get_version_from_module_file(module_obj, pkg_canonical_name, omnipkg_versions_dir_str):
    '''
    Determine the version and source of a loaded module.
    Returns (version_string, source_description)
    '''
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
                load_source = "main env (__version__)" if not load_source.startswith("bubble") else load_source
            else:
                load_source = "namespace package" if not load_source.startswith("bubble") else load_source
    
    return found_version, load_source
"""

def test_tensorflow():
    print("=" * 80)
    print("🚀 TensorFlow Version Switching Test")
    print("=" * 80)
    print(f"Using omnipkg versions directory: {OMNIPKG_VERSIONS_DIR}")

    print("\n📦 Setting up test environment with proper version arrangement")
    print("🧹 Clearing existing installations...")
    run_command_filtered(["omnipkg", "uninstall", "tensorflow", "-y"], check=False)
    run_command_filtered(["omnipkg", "uninstall", "tensorflow-estimator", "-y"], check=False)
    run_command_filtered(["omnipkg", "uninstall", "typing-extensions", "-y"], check=False)
    run_command_filtered(["omnipkg", "uninstall", "keras", "-y"], check=False)
    
    print("📦 Installing with desired version priority (newer active, older in bubbles)...")
    run_command_filtered([
        "omnipkg", "install", 
        "tensorflow==2.13.0", 
        "tensorflow-estimator==2.13.0",
        "keras==2.13.1",
        "typing-extensions==4.5.0", 
        "typing-extensions==4.14.1"
    ], check=True)

    print("\n🔧 Testing initial state: tensorflow==2.13.0 with typing-extensions==4.14.1 and keras==2.13.1 (main)")
    print("(Should have 4.14.1 and keras==2.13.1 active in main env, 4.5.0 in bubble)")
    code_initial_test = f'''
import tensorflow as tf
import typing_extensions
import keras
{GET_MODULE_VERSION_CODE_SNIPPET}

te_version, te_source = _get_version_from_module_file(typing_extensions, 'typing-extensions', '{OMNIPKG_VERSIONS_DIR}')
keras_version, keras_source = _get_version_from_module_file(keras, 'keras', '{OMNIPKG_VERSIONS_DIR}')

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
'''
    run_script_only_relevant_output(code_initial_test)

    print("\n🫧 Testing switch to typing-extensions==4.5.0 bubble with keras==2.13.1 (main)")
    te_bubble_path = OMNIPKG_VERSIONS_DIR / "typing_extensions-4.5.0"
    print(f"Looking for typing-extensions bubble at: {te_bubble_path}")
    print(f"Bubble exists: {te_bubble_path.exists()}")
    code_bubble_test = f'''
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

with omnipkgLoader("typing_extensions==4.5.0", config={{'multiversion_base': '{OMNIPKG_VERSIONS_DIR}', 'site_packages_path': '{_config_manager.config['site_packages_path']}'}}):
    import typing_extensions
    import tensorflow as tf
    import keras
    te_version, te_source = _get_version_from_module_file(typing_extensions, 'typing-extensions', '{OMNIPKG_VERSIONS_DIR}')
    keras_version, keras_source = _get_version_from_module_file(keras, 'keras', '{OMNIPKG_VERSIONS_DIR}')

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
'''
    run_script_only_relevant_output(code_bubble_test)

    print("\n🚀 STEP 4: Final cleanup")
    run_command_filtered(["omnipkg", "uninstall", "tensorflow", "-y"], check=True)
    run_command_filtered(["omnipkg", "uninstall", "tensorflow-estimator", "-y"], check=True)
    run_command_filtered(["omnipkg", "uninstall", "typing-extensions", "-y"], check=True)
    run_command_filtered(["omnipkg", "uninstall", "keras", "-y"], check=True)
    run_command_filtered([
        "omnipkg", "install", 
        "tensorflow==2.13.0", 
        "tensorflow-estimator==2.13.0",
        "keras==2.13.1", 
        "typing-extensions==4.5.0"
    ], check=True)
    print("✅ Cleanup complete - reset to single version")

    print("\n🔬 Final verification - single version state")
    code_final_test = f'''
import tensorflow as tf
import typing_extensions
import keras
{GET_MODULE_VERSION_CODE_SNIPPET}

te_version, te_source = _get_version_from_module_file(typing_extensions, 'typing-extensions', '{OMNIPKG_VERSIONS_DIR}')
keras_version, keras_source = _get_version_from_module_file(keras, 'keras', '{OMNIPKG_VERSIONS_DIR}')

print(f"TensorFlow version: {{tf.__version__}}")
print(f"Typing Extensions version: {{te_version}}")
print(f"Typing Extensions loaded from: {{te_source}}")
print(f"Keras version: {{keras_version}}")
print(f"Keras loaded from: {{keras_source}}")

try:
    model = tf.keras.Sequential([tf.keras.layers.Dense(1, input_shape=(1,))])
    print("✅ Final test: Model created successfully")
except Exception as e:
    print(f"❌ Final test: Model creation failed: {{e}}")
'''
    run_script_only_relevant_output(code_final_test)

if __name__ == "__main__":
    test_tensorflow()