from __future__ import annotations  # Python 3.6+ compatibility
# omnipkg/commands/run.py
try:
    from ..common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
import sys
import os
import subprocess
import tempfile
import json
import shutil
import re
import textwrap
import time
import importlib.metadata
from pathlib import Path
import os
import select # We can import it, but we check the OS before using it

# THE FIX: HAS_SELECT is only True on non-Windows (POSIX) systems.
HAS_SELECT = (os.name == 'posix')

try:
    from omnipkg.utils.flask_port_finder import auto_patch_flask_port
except ImportError:
    auto_patch_flask_port = None

# --- PROJECT PATH SETUP ---
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from omnipkg.i18n import _
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
from omnipkg.common_utils import sync_context_to_runtime
from omnipkg.utils.ai_import_healer import heal_code_string
# Global variable to store initial run timing for comparison
_initial_run_time_ns = None

def analyze_runtime_failure_and_heal(stderr: str, cmd_args: list, original_script_path_for_analysis: Path, config_manager: ConfigManager, is_context_aware_run: bool, cli_owner_spec: str = None, omnipkg_instance=None, verbose=False):
    """Analyzes stderr for errors. Accepts omnipkg_instance to avoid re-init."""
    healing_plan = set()
    
    # Fix 3: Use passed instance
    if not omnipkg_instance:
        omnipkg_instance = OmnipkgCore(config_manager)

    if cli_owner_spec:
        healing_plan.add(cli_owner_spec)
        safe_print(f"   📦 Added CLI owner package: {cli_owner_spec}")
        
    # Pattern 1: Prioritize specific, known issues like NumPy 2.0 incompatibility.
    numpy_patterns = [
        r"A module that was compiled using NumPy 1\.x cannot be run in[\s\S]*?NumPy 2\.0",
        r"numpy\.dtype size changed, may indicate binary incompatibility",
        r"AttributeError: _ARRAY_API not found",
        r"ImportError: numpy\.core\._multiarray_umath failed to import"
    ]
    if any(re.search(p, stderr, re.MULTILINE) for p in numpy_patterns):
        healing_plan.add("numpy==1.26.4")

    # Pattern 1.5: Deep ABI incompatibility issues that require package reinstallation
    abi_incompatibility_patterns = [
        # TensorFlow-specific ABI issues with undefined symbols
        (r"tensorflow\.python\.framework\.errors_impl\.NotFoundError:.*?undefined symbol.*?tensorflow", "tensorflow", "TensorFlow ABI incompatibility"),
        # Generic undefined symbol issues in compiled packages
        (r"ImportError:.*?undefined symbol.*?(_ZN\w+)", None, "Generic ABI incompatibility"),
        # Other common ABI issues
        (r"OSError:.*?cannot open shared object file.*?No such file or directory", None, "Missing shared library"),
        (r"ImportError:.*?DLL load failed.*?The specified module could not be found", None, "Windows DLL load failure")
    ]
    
    for regex, target_package, description in abi_incompatibility_patterns:
        match = re.search(regex, stderr, re.MULTILINE | re.DOTALL)
        if match:
            safe_print(f"\n🔍 {description} detected. This requires package reinstallation...")
            
            if target_package:
                # We know the specific problematic package
                safe_print(f"   - The issue is with '{target_package}' package")
                safe_print(f"   - This package was likely compiled against incompatible dependencies")
                safe_print(f"🚀 Auto-healing by reinstalling '{target_package}' to rebuild against current environment...")
                return heal_with_package_reinstall(target_package, original_script_path_for_analysis, cmd_args[1:], config_manager, omnipkg_instance=omnipkg_instance)
            else:
                # Try to extract the problematic package from the traceback
                # Look for the last package import in the traceback that's not a built-in
                import_matches = re.findall(r"File \".*?/site-packages/(\w+)/", stderr)
                if import_matches:
                    problematic_package = import_matches[-1]  # Last package in the chain
                    safe_print(f"   - The issue appears to be with '{problematic_package}' package")
                    safe_print(f"   - This package likely has ABI incompatibilities with current dependencies")
                    safe_print(f"🚀 Auto-healing by reinstalling '{problematic_package}' to rebuild against current environment...")
                    return heal_with_package_reinstall(problematic_package, original_script_path_for_analysis, cmd_args[1:], config_manager, omnipkg_instance=omnipkg_instance)
                else:
                    safe_print(f"   - Could not identify the specific problematic package")
                    safe_print(f"❌ Auto-healing aborted. Manual intervention may be required.")
                    return 1, None

    # Pattern 2: Handle explicit version conflicts from requirements.
    conflict_patterns = [
        (r"AssertionError: Incorrect ([\w\-]+) version! Expected ([\d\.]+)", 1, 2, "Runtime version assertion"),
        (r"requires ([\w\-]+)==([\d\.]+), but you have", 1, 2, "Import-time dependency conflict"),
        (r"assert\s+([\w\.]+)(?:\.__version__)?\s*==\s*['\"]([\d\.]+)['\"]", 1, 2, "Source code assertion"),
        (r"VersionConflict:.*?Requirement\.parse\('([\w\-]+)==([\d\.]+)'\)", 1, 2, "Setuptools VersionConflict")
    ]
    for regex, pkg_group, ver_group, description in conflict_patterns:
        match = re.search(regex, stderr)
        if match:
            raw_pkg_name = match.group(pkg_group)
            
            # 1. Strip common attribute artifacts (.version, .__version__)
            clean_pkg_name = raw_pkg_name.replace(".__version__", "").replace(".version", "")
            
            # 2. Strip common variable name suffixes (e.g. rich_version -> rich)
            if clean_pkg_name.endswith("_version"):
                clean_pkg_name = clean_pkg_name[:-8]
            elif clean_pkg_name.endswith("_ver"):
                clean_pkg_name = clean_pkg_name[:-4]
            
            # 🛑 STDLIB CHECK
            if is_stdlib_module(clean_pkg_name):
                if verbose:
                    safe_print(f"   ⚠️  Ignoring version assertion for standard library module '{clean_pkg_name}'.")
                continue

            # 3. Convert to real PyPI name
            pkg_name = convert_module_to_package_name(clean_pkg_name).lower()
            
            expected_version = match.group(ver_group)
            failed_spec = f"{pkg_name}=={expected_version}"
            
            if failed_spec not in healing_plan:
                safe_print(f"\n🔍 {description} failed. Auto-healing with omnipkg bubbles...")
                safe_print(_("   - Conflict identified for: {}").format(failed_spec))
                healing_plan.add(failed_spec)

    # Pattern 3: Heuristically handle AttributeErrors, which often indicate an outdated dependency.
    if "AttributeError:" in stderr:
        # Find the last 'from X import Y' statement in the traceback.
        importer_matches = re.findall(r"from ([\w\.]+) import", stderr)
        
        if importer_matches:
            # The culprit is the last package that was being imported, e.g., 'googletrans'
            culprit_package = importer_matches[-1].split('.')[0]
            
            # Perform a self-contained check to see if this culprit is a local module.
            script_dir = original_script_path_for_analysis.parent
            is_local_module = (script_dir / culprit_package).is_dir() or \
                            (script_dir / f"{culprit_package}.py").is_file()

            if not is_local_module:
                # 🛑 STDLIB CHECK: Don't heal AttributeErrors in stdlib (e.g. sys.foo)
                if is_stdlib_module(culprit_package):
                    safe_print(f"\n❌ AttributeError in standard library module '{culprit_package}' detected.")
                    safe_print(f"   This indicates a code error, not a missing package.")
                    return 1, None

                safe_print(f"\n🔍 Deep dependency conflict detected (AttributeError).")
                safe_print(f"   - The root cause appears to be the '{culprit_package}' package or its dependencies.")
                safe_print(f"🚀 Auto-healing by creating an isolated bubble for '{culprit_package}'...")
                healing_plan.add(culprit_package)

        # If the smart approach fails, use the simpler regex as a fallback.
        fallback_match = re.search(r"AttributeError: module '([\w\-\.]+)' has no attribute", stderr)
        if fallback_match:
            pkg_name_to_upgrade = fallback_match.group(1)
            
            # 🛑 STDLIB CHECK
            if is_stdlib_module(pkg_name_to_upgrade):
                safe_print(f"\n❌ AttributeError in standard library module '{pkg_name_to_upgrade}' detected.")
                safe_print(f"   This indicates a code error, not a missing package.")
                return 1, None

            safe_print(f"\n🔍 Dependency conflict detected (AttributeError). Using fallback.")
            safe_print(f"   - The package '{pkg_name_to_upgrade}' may be outdated.")
            safe_print(_("🚀 Auto-healing by attempting to upgrade the package..."))
            return heal_with_missing_package(
                pkg_name_to_upgrade, Path(cmd_args[0]), cmd_args[1:], 
                original_script_path_for_analysis, config_manager, 
                is_context_aware_run, omnipkg_instance=omnipkg_instance
            )

    # Pattern 4: Handle missing modules, intelligently distinguishing between local and PyPI packages.
    missing_module_patterns = [
        (r"ModuleNotFoundError: No module named '([\w\-\.]+)'", 1, "Missing module"),
        (r"ImportError: No module named ([\w\-\.]+)", 1, "Missing module (ImportError)")
    ]
    for regex, pkg_group, description in missing_module_patterns:
        match = re.search(regex, stderr)
        if match:
            full_module_name = match.group(pkg_group)
            top_level_module = full_module_name.split('.')[0]
            
            # 🛑 STDLIB CHECK: Don't try to install 'sys', 'math', etc.
            if is_stdlib_module(top_level_module):
                safe_print(f"\n❌ Missing standard library module '{top_level_module}'.")
                safe_print(f"   This indicates a corrupted Python environment or invalid import.")
                return 1, None

            script_dir = original_script_path_for_analysis.parent
            
            # First, check if it's a local module. This is the highest priority.
            potential_local_path_dir = script_dir / top_level_module
            potential_local_path_file = script_dir / f"{top_level_module}.py"
            
            if potential_local_path_dir.is_dir() or potential_local_path_file.is_file():
                safe_print(f"\n🔍 {description} detected - This appears to be a LOCAL IMPORT.")
                safe_print(f"   - The script failed to import '{full_module_name}'.")
                safe_print(f"   - A local module '{top_level_module}' was found in the project directory.")
                safe_print(_("🚀 Attempting a context-aware re-run..."))
                # Re-run, but this time inject the local project path into PYTHONPATH.
                return _run_script_with_healing(
                    script_path=original_script_path_for_analysis,
                    script_args=cmd_args[1:],
                    config_manager=config_manager,
                    original_script_path_for_analysis=original_script_path_for_analysis,
                    heal_type='local_context_run',
                    is_context_aware_run=True,
                    omnipkg_instance=omnipkg_instance
                )
            # If not a simple local module, check if it's a local installable project.
            parent_dir = script_dir.parent
            potential_parent_module_dir = parent_dir / top_level_module
            potential_setup_py = parent_dir / "setup.py"
            potential_pyproject_toml = parent_dir / "pyproject.toml"
            if (potential_parent_module_dir.is_dir() and (potential_setup_py.exists() or potential_pyproject_toml.exists())):
                safe_print(f"\n🔍 {description} detected - this appears to be a PROJECT PACKAGE.")
                safe_print("\n💡 This is likely a package that needs to be installed in editable mode.")
                safe_print(f"   1. Try installing with: pip install -e {parent_dir}")
                safe_print("\n❌ Auto-healing aborted. Please install the local project package manually.")
                return 1, None
            
            # Finally, if it's not local, assume it's a missing PyPI package.
            safe_print(f"\n🔍 {description} detected. Auto-healing by installing missing package...")
            pkg_name = convert_module_to_package_name(top_level_module)
            return heal_with_missing_package(pkg_name, Path(cmd_args[0]), cmd_args[1:], original_script_path_for_analysis, config_manager, is_context_aware_run)

    # Pattern 5: Handle missing required packages (e.g., tokenizer dependencies)
    missing_package_patterns = [
        (r"No module named '(\w+)'", 1, "Missing module import"),
        (r"Please make sure you have `(\w+)` installed", 1, "Missing required package"),
        (r"Please make sure you have `(\w+)` installed", 1, "Missing required package"),
        (r"Recommended: pip install (\w+)", 1, "Missing recommended package"),
        (r"No module named '(\w+)'", 1, "Missing module import"),
        (r"ModuleNotFoundError: No module named '([\w\.]+)'", 1, "Module not found"),
        (r"ImportError: cannot import name '[\w]+' from '([\w\.]+)'", 1, "Import error from module"),
        (r"requires (\w+) to be installed", 1, "Dependency requirement")
            ]
    
    for regex, pkg_group, description in missing_package_patterns:
        match = re.search(regex, stderr)
        if match:
            pkg_name = match.group(pkg_group).lower()
            
            # NEW: If it's a dotted module path, extract the base package
            if '.' in pkg_name:
                pkg_name = pkg_name.split('.')[0]

            if is_stdlib_module(pkg_name):
                continue
            
            # Handle special cases where package names differ from import names
            package_map = {
                'sentencepiece': 'sentencepiece',
                'sklearn': 'scikit-learn',
                'cv2': 'opencv-python',
                'PIL': 'Pillow',
            }
            pkg_name = package_map.get(pkg_name, pkg_name)
            failed_spec = pkg_name
            safe_print(f"\n🔍 {description} detected. Auto-healing with omnipkg bubbles...")
            safe_print(_("   - Installing missing package: {}").format(failed_spec))
            healing_plan.add(failed_spec)
    
    if healing_plan:
        # 1. Clean up conflicting specs
        versioned_pkgs = set()
        for spec in healing_plan:
            if '==' in spec or '<' in spec or '>' in spec:
                pkg = re.split(r'[<>=!]', spec)[0]
                versioned_pkgs.add(pkg)
        
        cleaned_plan = set()
        for spec in healing_plan:
            is_generic = not any(op in spec for op in ['==', '<', '>', '!', '~'])
            if is_generic:
                if spec in versioned_pkgs:
                    continue
            cleaned_plan.add(spec)
            
        specs_to_heal = sorted(list(cleaned_plan))
        safe_print(f"\n🔍 Comprehensive Healing Plan Compiled: {specs_to_heal}")
        
        final_specs = []
        original_strategy = omnipkg_instance.config.get('install_strategy')
        omnipkg_instance.config['install_strategy'] = 'stable-main'
        
        try:
            for spec in specs_to_heal:
                if '==' not in spec:
                    # 1. RESOLVE LATEST VERSION FROM PYPI FIRST
                    safe_print(f"🔍 Resolving latest version for '{spec}'...")
                    latest_ver = omnipkg_instance._get_latest_version_from_pypi(spec)
                    
                    if latest_ver:
                        target_spec = f"{spec}=={latest_ver}"
                        
                        # 2. CHECK IF THIS SPECIFIC VERSION IS ALREADY INSTALLED
                        # Check Bubble Cache
                        safe_name = spec.lower().replace("-", "_")
                        bubble_path = omnipkg_instance.multiversion_base / f'{safe_name}-{latest_ver}'
                        
                        if bubble_path.is_dir():
                            safe_print(f"   🚀 INSTANT HIT: Found existing bubble {target_spec} in KB")
                            final_specs.append(target_spec)
                            continue

                        # Check Main Env (Active Version)
                        active_ver = omnipkg_instance.get_active_version(spec)
                        if active_ver == latest_ver:
                             safe_print(f"   ✅ Main environment already has {target_spec}")
                             final_specs.append(target_spec)
                             continue
                        
                        # 3. IF NOT FOUND, INSTALL IT
                        safe_print(f"🛠️  Installing bubble for {target_spec}...")
                        omnipkg_instance.smart_install([target_spec])
                        final_specs.append(target_spec)

                    else:
                        # Fallback if PyPI resolution fails completely
                        safe_print(f"⚠️  Could not resolve latest version for {spec}. Trying generic install.")
                        final_specs.append(spec)
                        if not (omnipkg_instance.multiversion_base / spec.replace('==', '-')).exists():
                             omnipkg_instance.smart_install([spec])

                else:
                    # Logic for explicitly versioned specs (e.g. numpy==1.26.4)
                    final_specs.append(spec)
                    pkg_name = spec.split('==')[0]
                    pkg_ver = spec.split('==')[1]
                    safe_name = pkg_name.lower().replace("-", "_")
                    bubble_path = omnipkg_instance.multiversion_base / f'{safe_name}-{pkg_ver}'
                    
                    if not bubble_path.is_dir():
                         safe_print(f"🛠️  Installing bubble for {spec}...")
                         omnipkg_instance.smart_install([spec])

        finally:
            if original_strategy:
                omnipkg_instance.config['install_strategy'] = original_strategy
        
        # --- EXECUTION LOGIC ---
        
        # 1. If it's a CLI tool (e.g., httpie)
        if cli_owner_spec:
            command = cmd_args[0] if cmd_args else None
            if command and final_specs:
                safe_print(f"♻️  Loading: {final_specs}")
                return run_cli_with_healing_wrapper(final_specs, command, cmd_args[1:], config_manager)
        
        # 2. If it's a script run (e.g., omnipkg run script.py)
        if final_specs:
            return heal_with_bubble(final_specs, original_script_path_for_analysis, cmd_args[1:], config_manager, omnipkg_instance)

    # === FALLBACK ===
    safe_print("❌ Error could not be auto-healed or no healing plan found.")
    return 1, None

def heal_with_package_reinstall(package_name: str, script_path: Path, script_args: list, config_manager: ConfigManager):
    """Reinstalls a package completely to fix ABI/compilation issues.
    This is more aggressive than bubbling and is used when packages have been
    compiled against incompatible dependencies.
    """
    safe_print(f"🔄 Starting package reinstallation for '{package_name}'...")
    
    try:
        # Step 1: Uninstall the problematic package completely
        safe_print(f"🗑️  Uninstalling '{package_name}' completely...")
        uninstall_result = subprocess.run([
            sys.executable, '-m', 'pip', 'uninstall', package_name, '-y'
        ], capture_output=True, text=True, timeout=300)
        
        if uninstall_result.returncode == 0:
            safe_print(f"✅ Successfully uninstalled '{package_name}'")
        else:
            safe_print(f"⚠️  Uninstall had issues, but continuing: {uninstall_result.stderr}")
        
        # Step 2: Clear pip cache to ensure fresh download
        safe_print(f"🧹 Clearing pip cache for '{package_name}'...")
        cache_clear_result = subprocess.run([
            sys.executable, '-m', 'pip', 'cache', 'remove', package_name
        ], capture_output=True, text=True, timeout=60)
        
        if cache_clear_result.returncode == 0:
            safe_print(f"✅ Successfully cleared cache for '{package_name}'")
        else:
            safe_print(f"⚠️  Cache clear had issues, but continuing...")
        
        # Step 3: Reinstall the package
        safe_print(f"📦 Reinstalling '{package_name}' with fresh compilation...")
        install_result = subprocess.run([
            sys.executable, '-m', 'pip', 'install', package_name, '--no-cache-dir', '--force-reinstall'
        ], capture_output=True, text=True, timeout=600)
        
        if install_result.returncode != 0:
            safe_print(f"❌ Failed to reinstall '{package_name}': {install_result.stderr}")
            return 1, None
        
        safe_print(f"✅ Successfully reinstalled '{package_name}'")
        
        # Step 4: Re-run the original script
        safe_print(f"🚀 Re-running script after '{package_name}' reinstallation...")
        return _run_script_with_healing(
            script_path=script_path,
            script_args=script_args,
            config_manager=config_manager,
            original_script_path_for_analysis=script_path,
            heal_type=f'package_reinstall_{package_name}',
            is_context_aware_run=False
        )
        
    except subprocess.TimeoutExpired:
        safe_print(f"❌ Package reinstallation timed out for '{package_name}'")
        return 1, None
    except Exception as e:
        safe_print(f"❌ Unexpected error during package reinstallation: {e}")
        return 1, None
    
def is_stdlib_module(module_name: str) -> bool:
    """
    Determines if a module name belongs to the Python Standard Library.
    """
    if not module_name:
        return False
        
    base_name = module_name.split('.')[0]
    
    # 1. Use the definitive source in Python 3.10+
    if sys.version_info >= (3, 10):
        if base_name in sys.stdlib_module_names:
            return True
            
    # 2. Check built-in modules (sys, gc, etc.)
    if base_name in sys.builtin_module_names:
        return True
        
    # 3. Fallback hardcoded list for older Pythons or edge cases
    # (Common stdlibs that users might accidentally try to install)
    COMMON_STDLIBS = {
        'os', 'sys', 're', 'json', 'math', 'random', 'datetime', 'subprocess',
        'pathlib', 'typing', 'collections', 'itertools', 'functools', 'io',
        'pickle', 'copy', 'enum', 'dataclasses', 'abc', 'contextlib', 'argparse',
        'shutil', 'threading', 'multiprocessing', 'asyncio', 'socket', 'ssl',
        'sqlite3', 'csv', 'time', 'logging', 'warnings', 'traceback', 'inspect',
        'ast', 'platform', 'urllib', 'http', 'email', 'xml', 'html', 'unittest',
        'venv', 'pydoc', 'pdb', 'profile', 'cProfile', 'timeit'
    }
    
    return base_name in COMMON_STDLIBS

def convert_module_to_package_name(module_name: str) -> str:
    """
    Convert a module name to its likely PyPI package name.
    Handles common cases where module names differ from package names.
    """
    # Common module -> package mappings
    module_to_package = {
        'yaml': 'pyyaml',
        'cv2': 'opencv-python',
        'PIL': 'pillow',
        'sklearn': 'scikit-learn',
        'bs4': 'beautifulsoup4',
        'requests_oauthlib': 'requests-oauthlib',
        'google.auth': 'google-auth',
        'google.cloud': 'google-cloud-core',
        'jwt': 'pyjwt',
        'absl': 'absl-py', # <--- ADD THIS LINE
        'dateutil': 'python-dateutil',
        'magic': 'python-magic',
        'psutil': 'psutil',
        'lxml': 'lxml',
        'numpy': 'numpy',
        'pandas': 'pandas',
        'matplotlib': 'matplotlib',
        'seaborn': 'seaborn',
        'plotly': 'plotly',
        'dash': 'dash',
        'flask': 'flask',
        'django': 'django',
        'fastapi': 'fastapi',
        'uvicorn': 'uvicorn',
        'gunicorn': 'gunicorn',
        'celery': 'celery',
        'redis': 'redis',
        'pymongo': 'pymongo',
        'sqlalchemy': 'sqlalchemy',
        'alembic': 'alembic',
        'psycopg2': 'psycopg2-binary',
        'mysqlclient': 'mysqlclient',
        'pytest': 'pytest',
        'black': 'black',
        'flake8': 'flake8',
        'mypy': 'mypy',
        'isort': 'isort',
        'pre_commit': 'pre-commit',
        'click': 'click',
        'typer': 'typer',
        'rich': 'rich',
        'colorama': 'colorama',
        'tqdm': 'tqdm',
        'joblib': 'joblib',
        'multiprocess': 'multiprocess',
        'dask': 'dask',
        'scipy': 'scipy',
        'sympy': 'sympy',
        'networkx': 'networkx',
        'igraph': 'python-igraph',
        'graph_tool': 'graph-tool',
        'tensorflow': 'tensorflow',
        'torch': 'torch',
        'torchvision': 'torchvision',
        'transformers': 'transformers',
        'datasets': 'datasets',
        'accelerate': 'accelerate',
        'wandb': 'wandb',
        'mlflow': 'mlflow',
        'optuna': 'optuna',
        'hyperopt': 'hyperopt',
        'xgboost': 'xgboost',
        'lightgbm': 'lightgbm',
        'catboost': 'catboost',
        'shap': 'shap',
        'lime': 'lime',
        'eli5': 'eli5',
        'boto3': 'boto3',
        'botocore': 'botocore',
        'azure': 'azure',
        'google': 'google-cloud',
        'openai': 'openai',
        'anthropic': 'anthropic',
        'langchain': 'langchain',
        'llama_index': 'llama-index',
        'chromadb': 'chromadb',
        'pinecone': 'pinecone-client',
        'weaviate': 'weaviate-client',
        'faiss': 'faiss-cpu',
        'annoy': 'annoy',
        'hnswlib': 'hnswlib',
        'streamlit': 'streamlit',
        'gradio': 'gradio',
        'jupyterlab': 'jupyterlab',
        'notebook': 'notebook',
        'ipython': 'ipython',
        'ipykernel': 'ipykernel',
        'ipywidgets': 'ipywidgets',
        'voila': 'voila',
        'papermill': 'papermill',
        'nbconvert': 'nbconvert',
        'sphinx': 'sphinx',
        'mkdocs': 'mkdocs',
        'docutils': 'docutils',
        'jinja2': 'jinja2',
        'mako': 'mako',
        'pydantic': 'pydantic',
        'attrs': 'attrs',
        'marshmallow': 'marshmallow',
        'cerberus': 'cerberus',
        'schema': 'schema',
        'jsonschema': 'jsonschema',
        'toml': 'toml',
        'tomli': 'tomli',
        'configparser': 'configparser',
        'dotenv': 'python-dotenv',
        'decouple': 'python-decouple',
        'environs': 'environs',
        'click_log': 'click-log',
        'loguru': 'loguru',
        'structlog': 'structlog',
        'sentry_sdk': 'sentry-sdk',
        'rollbar': 'rollbar',
        'bugsnag': 'bugsnag',
        'newrelic': 'newrelic',
        'datadog': 'datadog',
        'prometheus_client': 'prometheus-client',
        'statsd': 'statsd',
        'influxdb': 'influxdb',
        'elasticsearch': 'elasticsearch',
        'kafka': 'kafka-python',
        'pika': 'pika',
        'kombu': 'kombu',
        'amqp': 'amqp',
        'paramiko': 'paramiko',
        'fabric': 'fabric',
        'invoke': 'invoke',
        'ansible': 'ansible',
        'docker': 'docker',
        'kubernetes': 'kubernetes',
        'terraform': 'python-terraform',
        'pulumi': 'pulumi',
        'cloudformation': 'troposphere',
        'boto': 'boto',
        'moto': 'moto',
        'localstack': 'localstack',
        'pytest_mock': 'pytest-mock',
        'pytest_cov': 'pytest-cov',
        'pytest_xdist': 'pytest-xdist',
        'pytest_html': 'pytest-html',
        'pytest_json_report': 'pytest-json-report',
        'coverage': 'coverage',
        'codecov': 'codecov',
        'bandit': 'bandit',
        'safety': 'safety',
        'pip_audit': 'pip-audit',
        'semgrep': 'semgrep',
        'vulture': 'vulture',
        'radon': 'radon',
        'xenon': 'xenon',
        'mccabe': 'mccabe',
        'pylint': 'pylint',
        'pycodestyle': 'pycodestyle',
        'pydocstyle': 'pydocstyle',
        'pyflakes': 'pyflakes',
        'autopep8': 'autopep8',
        'yapf': 'yapf',
        'rope': 'rope',
        'jedi': 'jedi',
        'parso': 'parso',
        'pygments': 'pygments',
        'colorlog': 'colorlog',
        'termcolor': 'termcolor',
        'blessed': 'blessed',
        'asciimatics': 'asciimatics',
        'urwid': 'urwid',
        'npyscreen': 'npyscreen',
        'textual': 'textual',
        'prompt_toolkit': 'prompt-toolkit',
        'inquirer': 'inquirer',
        'questionary': 'questionary',
        'pick': 'pick',
        'halo': 'halo',
        'yaspin': 'yaspin',
        'alive_progress': 'alive-progress',
        'progress': 'progress',
        'enlighten': 'enlighten',
        'fire': 'fire',
        'argparse': 'argparse',  # Built-in, but sometimes needs backport
        'configargparse': 'configargparse',
        'plac': 'plac',
        'docopt': 'docopt',
        'cliff': 'cliff',
        'cement': 'cement',
        'cleo': 'cleo',
        'baker': 'baker',
        'begins': 'begins',
        'delegator': 'delegator.py',
        'sh': 'sh',
        'pexpect': 'pexpect',
        'ptyprocess': 'ptyprocess',
        'winpty': 'pywinpty',
        'coloredlogs': 'coloredlogs',
        'humanfriendly': 'humanfriendly',
        'tabulate': 'tabulate',
        'prettytable': 'prettytable',
        'texttable': 'texttable',
        'terminaltables': 'terminaltables',
        'rich_table': 'rich',
        'asciitable': 'asciitable',
        'csvkit': 'csvkit',
        'xlrd': 'xlrd',
        'xlwt': 'xlwt',
        'xlsxwriter': 'xlsxwriter',
        'openpyxl': 'openpyxl',
        'xlwings': 'xlwings',
        'pandas_datareader': 'pandas-datareader',
        'yfinance': 'yfinance',
        'alpha_vantage': 'alpha-vantage',
        'quandl': 'quandl',
        'fredapi': 'fredapi',
        'investpy': 'investpy',
        'ccxt': 'ccxt',
        'binance': 'python-binance',
        'coinbase': 'coinbase',
        'kraken': 'krakenex',
        'bittrex': 'python-bittrex',
        'poloniex': 'poloniex',
        'gdax': 'gdax',
        'gemini': 'gemini-python',
        'blockchain': 'blockchain',
        'web3': 'web3',
        'eth_account': 'eth-account',
        'eth_hash': 'eth-hash',
        'eth_typing': 'eth-typing',
        'eth_utils': 'eth-utils',
        'solcx': 'py-solc-x',
        'vyper': 'vyper',
        'brownie': 'eth-brownie',
        'ape': 'eth-ape',
        'hardhat': 'hardhat',
        'truffle': 'truffle',
        'ganache': 'ganache-cli',
        'infura': 'web3[infura]',
    'alchemy': 'web3[alchemy]',
    'moralis': 'moralis',
    'thegraph': 'thegraph',
    'qiskit': 'qiskit-aer',
    'qiskit-ibm': 'qiskit-ibm',
    'qiskit-ignis': 'qiskit-ignis',
    'qiskit-terra': 'qiskit-terra',
    'qiskit-nature': 'qiskit-nature',
    'qiskit-finance': 'qiskit-finance',
    'qiskit-machine-learning': 'qiskit-machine-learning',
    'cirq': 'cirq',
    'pennylane': 'pennylane',
    'braket': 'amazon-braket-sdk',
    'dwave': 'dwave-ocean-sdk',
        'ocean': 'ocean-sdk',
        'pyquil': 'pyquil',
        'forest': 'forest-sdk',
        'qsharp': 'qsharp',
        'iqsharp': 'iqsharp',
        'qiskit': 'qiskit',
        'pytorch': 'torch',
        'tensorflow': 'tensorflow',
        'jax': 'jax',
        'flax': 'flax',
        'haiku': 'dm-haiku',
        'optax': 'optax',
        'chex': 'chex',
        'dm_control': 'dm-control',
        'rlax': 'rlax',
        'acme': 'acme',
        'trax': 'trax',
        'alpa': 'alpa',
        't5x': 't5x',
        'bigscience': 'bigscience',
        'transformers': 'transformers',
        'datasets': 'datasets',
        'peft': 'peft',
        'bitsandbytes': 'bitsandbytes',
        'accelerate': 'accelerate',
        'deepspeed': 'deepspeed',
        'fairseq': 'fairseq',
        'sentencepiece': 'sentencepiece',   
        'chainlink': 'chainlink',
        'uniswap': 'uniswap-python',
        'compound': 'compound-python',
        'aave': 'aave-python',
        'maker': 'maker-python',
        'curve': 'curve-python',
        'yearn': 'yearn-python',
        'synthetix': 'synthetix-python',
        'balancer': 'balancer-python',
        'sushiswap': 'sushiswap-python',
        'pancakeswap': 'pancakeswap-python',
        'quickswap': 'quickswap-python',
        'honeyswap': 'honeyswap-python',
        'spookyswap': 'spookyswap-python',
        'spiritswap': 'spiritswap-python',
        'traderjoe': 'traderjoe-python',
        'pangolin': 'pangolin-python',
        'lydia': 'lydia-python',
        'elk': 'elk-python',
        'oliveswap': 'oliveswap-python',
        'comethswap': 'comethswap-python',
        'dfyn': 'dfyn-python',
        'polyswap': 'polyswap-python',
        'polydex': 'polydex-python',
        'apeswap': 'apeswap-python',
        'jetswap': 'jetswap-python',
        'mdex': 'mdex-python',
        'biswap': 'biswap-python',
        'babyswap': 'babyswap-python',
        'nomiswap': 'nomiswap-python',
        'cafeswap': 'cafeswap-python',
        'cheeseswap': 'cheeseswap-python',
        'julswap': 'julswap-python',
        'kebabswap': 'kebabswap-python',
        'burgerswap': 'burgerswap-python',
        'goosedefi': 'goosedefi-python',
        'alpaca': 'alpaca-python',
        'autofarm': 'autofarm-python',
        'belt': 'belt-python',
        'bunny': 'bunny-python',
        'cream': 'cream-python',
        'fortress': 'fortress-python',
        'venus': 'venus-python',
        'wault': 'wault-python',
        'acryptos': 'acryptos-python',
        'beefy': 'beefy-python',
        'harvest': 'harvest-python',
        'pickle': 'pickle-python',
        'convex': 'convex-python',
        'ribbon': 'ribbon-python',
        'tokemak': 'tokemak-python',
        'olympus': 'olympus-python',
        'wonderland': 'wonderland-python',
        'klima': 'klima-python',
        'rome': 'rome-python',
        'redacted': 'redacted-python',
        'spell': 'spell-python',
        'mim': 'mim-python',
        'frax': 'frax-python',
        'fei': 'fei-python',
        'terra': 'terra-python',
        'anchor': 'anchor-python',
        'mirror': 'mirror-python',
        'astroport': 'astroport-python',
        'prism': 'prism-python',
        'loop': 'loop-python',
        'mars': 'mars-python',
        'stader': 'stader-python',
        'pylon': 'pylon-python',
        'nebula': 'nebula-python',
        'starterra': 'starterra-python',
        'orion': 'orion-python',
        'valkyrie': 'valkyrie-python',
        'apollo': 'apollo-python',
        'spectrum': 'spectrum-python',
        'eris': 'eris-python',
        'edge': 'edge-python',
        'whitewhale': 'whitewhale-python',
        'backbone': 'backbone-python',
        'luart': 'luart-python',
        'terraswap': 'terraswap-python',
        'phoenix': 'phoenix-python',
        'coinhall': 'coinhall-python',
        'smartstake': 'smartstake-python',
        'extraterrestrial': 'extraterrestrial-python',
        'tfm': 'tfm-python',
        'knowhere': 'knowhere-python',
        'delphi': 'delphi-python',
        'galactic': 'galactic-python',
        'kinetic': 'kinetic-python',
        'reactor': 'reactor-python',
        'protorev': 'protorev-python',
        'white_whale': 'white-whale-python',
        'mars_protocol': 'mars-protocol-python',
        'astro_generator': 'astro-generator-python',
        'apollo_dao': 'apollo-dao-python',
        'eris_protocol': 'eris-protocol-python',
        'backbone_labs': 'backbone-labs-python',
        'luart_io': 'luart-io-python',
        'terraswap_io': 'terraswap-io-python',
        'phoenix_protocol': 'phoenix-protocol-python',
        'coinhall_org': 'coinhall-org-python',
        'smartstake_io': 'smartstake-io-python',
        'extraterrestrial_money': 'extraterrestrial-money-python',
        'tfm_dev': 'tfm-dev-python',
        'knowhere_art': 'knowhere-art-python',
        'delphi_digital': 'delphi-digital-python',
        'galactic_punks': 'galactic-punks-python'
    }
    
    # Check for direct mapping first
    if module_name in module_to_package:
        return module_to_package[module_name]

    # Step 2: If it's a dotted module (e.g., google.cloud.storage),
    # check if the base part has a mapping (e.g., google -> google-cloud-core).
    base_module = module_name.split('.')[0] if '.' in module_name else module_name
    
    if base_module in module_to_package:
        safe_print(f"INFO: Found direct mapping for '{base_module}'. Package is '{module_to_package[base_module]}'")
        return module_to_package[base_module]

    # Step 3: HEURISTIC FOR REFACTORED LIBRARIES (THE QISKIT FIX)
    # Look for patterns like "cannot import name 'aer' from 'qiskit'"
    if error_message:
        import_match = re.search(r"cannot import name \'(\w+)\' from \'([\w\.]+)\'", error_message)
        if import_match:
            name_to_import = import_match.group(1)
            module_it_failed_on = import_match.group(2).split('.')[0]
            
            # Construct a guess like "qiskit-aer"
            heuristic_package_name = f"{module_it_failed_on}-{name_to_import.lower()}"
            safe_print(f"INFO: Applying refactor heuristic. Guessing package is '{heuristic_package_name}'")
            return heuristic_package_name

    # Step 4: HEURISTIC FOR NAMESPACE PACKAGES (DOTTED-NAME LOGIC)
    # e.g., 'google.cloud.storage' -> 'google-cloud-storage'
    if '.' in module_name:
        namespace_package_name = module_name.replace('.', '-')
        safe_print(f"INFO: Applying namespace heuristic. Guessing package is '{namespace_package_name}'")
        return namespace_package_name

    # Step 5: FINAL FALLBACK
    # The simplest case: if no other rules match, assume the module name is the package name.
    safe_print(f"INFO: No specific rule matched. Falling back to module name '{module_name}' as the package name.")
    return module_name


# CHANGED: Added 'original_script_path_for_analysis' to the signature
def heal_with_missing_package(pkg_name: str, temp_script_path: Path, temp_script_args: list, original_script_path_for_analysis: Path, config_manager, is_context_aware_run: bool, omnipkg_instance=None):
    """Installs/upgrades a package and re-runs the script, preserving run context."""
    safe_print(_("🚀 Auto-installing/upgrading missing package... (This may take a moment)"))
    
    if not omnipkg_instance:
        omnipkg_instance = OmnipkgCore(config_manager)
        
    return_code = omnipkg_instance.smart_install([pkg_name])
    
    if return_code != 0:
        safe_print(_("\n❌ Auto-install failed for {}.").format(pkg_name))
        return 1, None

    safe_print(_("\n✅ Package operation successful for: {}").format(pkg_name))
    safe_print(_("🚀 Re-running script with recursive auto-healing..."))

    # THE CRITICAL FIX: Pass the is_context_aware_run flag to the next run.
    return _run_script_with_healing(
        temp_script_path, temp_script_args, config_manager, 
        original_script_path_for_analysis, heal_type='package_install', 
        is_context_aware_run=is_context_aware_run
    )
    
def _run_script_with_healing(script_path, script_args, config_manager, original_script_path_for_analysis, heal_type='execution', is_context_aware_run=False, omnipkg_instance=None):
    """
    Common function to run a script and automatically heal any failures.
    Can inject the original script's directory into PYTHONPATH for local imports.
    """
    python_exe = config_manager.config.get('python_executable', sys.executable)
    run_cmd = [python_exe] + [str(script_path)] + script_args

    # --- CONTEXT INJECTION LOGIC ---
    # Create a copy of the current environment to modify for the subprocess
    env = os.environ.copy()
    if is_context_aware_run:
        project_dir = original_script_path_for_analysis.parent
        if heal_type == 'local_context_run':
            safe_print(_("   - Injecting project directory into PYTHONPATH: {}").format(project_dir))
        
        # Prepend the project path to ensure it's checked first by Python
        current_python_path = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = f"{project_dir}{os.pathsep}{current_python_path}"

    start_time_ns = time.perf_counter_ns()

    # PHASE 1: Quick test run to detect if script is interactive or has errors
    safe_print("🔍 Testing script for interactivity and errors...")
    
    test_process = subprocess.Popen(
        run_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        cwd=Path.cwd(),
        env=env
    )

    # Give the script a brief moment to start and show any immediate output
    try:
        output, _stderr = test_process.communicate(timeout=2)
        test_return_code = test_process.returncode
    except subprocess.TimeoutExpired:
        # Script is likely interactive or long-running
        test_process.terminate()
        try:
            test_process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            test_process.kill()
            test_process.wait()
        
        safe_print("📱 Interactive script detected - switching to direct mode...")
        
        # PHASE 2: Run interactively for interactive scripts
        try:
            interactive_process = subprocess.Popen(
                run_cmd,
                stdin=None,  # Use parent's stdin directly
                stdout=None,  # Use parent's stdout directly  
                stderr=None,  # Use parent's stderr directly
                cwd=Path.cwd(),
                env=env
            )
            
            return_code = interactive_process.wait()
            end_time_ns = time.perf_counter_ns()
            
            heal_stats = {
                'total_swap_time_ns': end_time_ns - start_time_ns,
                'activation_time_ns': 0,
                'deactivation_time_ns': 0,
                'type': heal_type
            }
            
            # Show success message for interactive scripts
            if return_code == 0:
                if _initial_run_time_ns:
                    safe_print("\n" + "🎯 " + "="*60)
                    safe_print("🚀 SUCCESS! Auto-healing completed.")
                    _print_performance_comparison(_initial_run_time_ns, heal_stats)
                    safe_print("🎮 Interactive script completed successfully...")
                    safe_print("="*68 + "\n")
            
            return return_code, heal_stats
            
        except KeyboardInterrupt:
            safe_print("\n🛑 Interactive process interrupted by user")
            interactive_process.terminate()
            interactive_process.wait()
            return 130, None
    
    # PHASE 2: Handle non-interactive scripts or scripts with errors
    if test_return_code != 0:
        # Script failed, analyze the error
        safe_print(f"❌ Script failed with return code {test_return_code}")
        return analyze_runtime_failure_and_heal(
            output, 
            [str(script_path)] + script_args, 
            original_script_path_for_analysis, 
            config_manager, 
            is_context_aware_run, 
            omnipkg_instance=omnipkg_instance
        )
    
    # Script completed successfully and non-interactively
    end_time_ns = time.perf_counter_ns()
    heal_stats = {
        'total_swap_time_ns': end_time_ns - start_time_ns,
        'activation_time_ns': 0,
        'deactivation_time_ns': 0,
        'type': heal_type
    }

    # Show any output that was captured
    if output:
        safe_print(output, end='')

    if _initial_run_time_ns:
        safe_print("\n" + "🎯 " + "="*60)
        safe_print("🚀 SUCCESS! Auto-healing completed.")
        _print_performance_comparison(_initial_run_time_ns, heal_stats)
        safe_print("✅ Script executed successfully...")
        safe_print("="*68 + "\n")
    else:
        safe_print("\n" + "="*60)
        safe_print("✅ Script executed successfully after auto-healing.")
        safe_print("="*60)

    return test_return_code, heal_stats

def heal_with_bubble(required_specs, original_script_path, original_script_args, config_manager, omnipkg_instance=None, verbose=False):
    """
    Ensures bubble exists. Accepts omnipkg_instance to avoid re-init.
    """
    if not omnipkg_instance:
        omnipkg_instance = OmnipkgCore(config_manager)
    
    if isinstance(required_specs, str):
        required_specs = [required_specs]
    
    final_specs = []
    
    for required_spec in required_specs:
        
        # --- CASE A: Unversioned Spec (e.g. "urllib3") ---
        if '==' not in required_spec:
            pkg_name = required_spec
            
            # 1. Resolve to LATEST version (Safety First)
            # We don't want just *any* random old bubble. We want the latest stable.
            latest_ver = None
            try:
                if verbose:
                    safe_print(_("🔍 Resolving latest version for '{}'...").format(pkg_name))
                
                # Use core logic to find what the "latest" version truly is
                latest_ver = omnipkg_instance._get_latest_version_from_pypi(pkg_name)
            except Exception:
                pass

            bubble_found = False
            
            # 2. Check if we ALREADY have a bubble for this specific LATEST version
            if latest_ver:
                safe_name = pkg_name.lower().replace("-", "_")
                potential_bubble = omnipkg_instance.multiversion_base / f'{safe_name}-{latest_ver}'
                
                if potential_bubble.is_dir():
                    safe_print(f"   🚀 CACHE HIT: Bubble exists for {pkg_name}=={latest_ver}")
                    final_spec = f"{pkg_name}=={latest_ver}"
                    final_specs.append(final_spec)
                    bubble_found = True
            
            # 3. If no bubble found (or resolution failed), fall back to smart_install
            if not bubble_found:
                safe_print(_("   Bubble missing for latest version. Auto-installing..."))
                return_code = omnipkg_instance.smart_install([pkg_name])

                if return_code != 0:
                    safe_print(_("\n❌ Auto-install failed for {}.").format(pkg_name))
                    return 1, None

                # Get the version we just installed (it's now active/resolved)
                latest_version = omnipkg_instance.get_active_version(pkg_name)
                if not latest_version:
                    return 1, None

                final_spec = f"{pkg_name}=={latest_version}"
                final_specs.append(final_spec)

        # --- CASE B: Versioned Spec (e.g. "numpy==1.26.4") ---
        else:
            final_spec = required_spec
            pkg_name, pkg_version = final_spec.split('==', 1)
            
            # Check filesystem directly (fastest)
            bubble_dir_name = f'{pkg_name.lower().replace("-", "_")}-{pkg_version}'
            bubble_path = Path(config_manager.config['multiversion_base']) / bubble_dir_name
            
            if not bubble_path.is_dir():
                safe_print(_("💡 Missing bubble detected: {}").format(final_spec))
                safe_print(_("🚀 Auto-installing bubble..."))
                if omnipkg_instance.smart_install([final_spec]) != 0:
                    safe_print(_("\n❌ Auto-install failed for {}.").format(final_spec))
                    return 1, None
            
            final_specs.append(final_spec)

    # Step 2: Analyze script dependencies (Existing logic preserved)
    safe_print(_("\n🔍 Analyzing script for additional dependencies..."))
    additional_packages = set()
    already_handled = {spec.split('==')[0].lower() for spec in final_specs}

    try:
        if original_script_path.exists():
            code = original_script_path.read_text(encoding='utf-8', errors='ignore')
            imports = re.findall(r'^\s*(?:import|from)\s+([a-zA-Z0-9_]+)', code, re.MULTILINE)
            
            # FIXED LINE BELOW
            for imp in imports:
                if is_stdlib_module(imp): continue
                try:
                    pkg_name_found = convert_module_to_package_name(imp)
                    if pkg_name_found and pkg_name_found.lower() not in already_handled:
                        additional_packages.add(pkg_name_found)
                except Exception:
                    pass
            
            if 'omnipkg' in additional_packages:
                additional_packages.remove('omnipkg')
            
            if additional_packages:
                additional_list = sorted(list(additional_packages))
                safe_print(_("   📦 Found additional dependencies: {}").format(', '.join(additional_list)))
                # Silent install attempt for dependencies
                omnipkg_instance.smart_install(additional_list)
    except Exception:
        pass

    # Step 3: Execute
    safe_print(_("\n✅ Bubble ready. Activating: {}").format(final_specs))
    return run_with_healing_wrapper(final_specs, original_script_path, original_script_args, config_manager, isolation_mode='overlay', verbose=verbose)
    
# ------------------------------------------------------------------------------
# NEW: Dispatcher and CLI Handling Logic
# ------------------------------------------------------------------------------
def execute_run_command(cmd_args: list, config_manager: ConfigManager, verbose: bool = False, omnipkg_core=None):
    from omnipkg.i18n import _
    
    if not cmd_args:
        safe_print(_('❌ Error: No script or command specified to run.'))
        return 1
    
    # 1. Reuse provided Core or Initialize if missing
    if omnipkg_core is None:
        omnipkg_core = OmnipkgCore(config_manager)

    target = cmd_args[0]
    target_path = Path(target)

    # BRANCH 0: Python Inline Command
    if target in ['python', 'python3', 'python.exe'] and '-c' in cmd_args:
        try:
            c_index = cmd_args.index('-c')
            if c_index + 1 < len(cmd_args):
                code_string = cmd_args[c_index + 1]
                script_args = cmd_args[c_index + 2:] 
                safe_print("✨ Detected inline Python code. Converting to virtual script...")
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                    f.write(code_string)
                    temp_source_path = Path(f.name)
                try:
                    # ✅ Pass omnipkg_core to logic
                    return _run_script_logic(temp_source_path, script_args, config_manager, verbose, omnipkg_core=omnipkg_core)
                finally:
                    if temp_source_path.exists():
                        temp_source_path.unlink()
        except ValueError:
            pass

    # --- BRANCH 0.5: Python stdin mode (python < script or python <<EOF) ---
    # NEW BRANCH TO ADD!
    elif target in ['python', 'python3', 'python.exe'] and len(cmd_args) == 1:
        # Stdin mode - capture input and write to temp file
        safe_print("🐍 Detected Python stdin mode. Reading from stdin...")
        
        # Read ALL stdin content
        stdin_content = sys.stdin.read()
        
        if not stdin_content.strip():
            safe_print("❌ No input provided via stdin")
            return 1
        
        # Write to temp file
        temp_script_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(stdin_content)
                temp_script_path = Path(f.name)
            
            safe_print(f"📝 Saved stdin to temporary script: {temp_script_path}")
            
            # Now delegate to script logic (which handles healing properly)
            return _run_script_logic(temp_script_path, [], config_manager, verbose)
            
        finally:
            if temp_script_path and temp_script_path.exists():
                temp_script_path.unlink()


    # BRANCH 1: Python Script
    if (target_path.exists() and target_path.is_file()) or target.endswith('.py'):
        # ✅ Pass omnipkg_core to logic
        return _run_script_logic(target_path.resolve(), cmd_args[1:], config_manager, verbose, omnipkg_core=omnipkg_core)
    
    # BRANCH 2: CLI Command
    elif shutil.which(target) or omnipkg_core.get_package_for_command(target):
        # ✅ Pass omnipkg_core to logic
        return _handle_cli_execution(target, cmd_args[1:], config_manager, omnipkg_core)
    
    else:
        safe_print(_("❌ Error: Target '{}' is neither a valid script file nor a recognized command.").format(target))
        return 1

def _handle_cli_execution(command, args, config_manager, omnipkg_core):
    """Enhanced CLI execution with unified healing analysis and performance stats."""
    
    # 1. Try to run the command
    error_output = ""
    start_time_ns = time.perf_counter_ns()
    
    if shutil.which(command):
        try:
            result = subprocess.run(
                [command] + args, 
                capture_output=True, 
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            # If success, just print output and exit
            if result.returncode == 0:
                print(result.stdout, end='')
                return 0
            
            # If failed, capture output for analysis
            print(result.stdout, end='')
            print(result.stderr, end='', file=sys.stderr)
            error_output = result.stderr + "\n" + result.stdout
            safe_print(f"\n⚠️  Command '{command}' failed (exit code {result.returncode}). Starting Auto-Healer...")
            
        except Exception as e:
            safe_print(f"\n⚠️  Execution failed: {e}")
            return 1
    else:
        # Command not found in PATH
        safe_print(f"🔍 Command '{command}' not found in PATH. Checking Knowledge Base...")

    # Stop the timer for the failed run
    end_time_ns = time.perf_counter_ns()
    initial_failure_duration_ns = end_time_ns - start_time_ns

    # 2. Identify owner package
    owning_package = omnipkg_core.get_package_for_command(command)
    if not owning_package:
        safe_print(f"❌ Unknown command '{command}'.")
        return 1

    # 3. Build initial healing plan with owner package
    active_version = omnipkg_core.get_active_version(owning_package)
    owner_spec = f"{owning_package}=={active_version}" if active_version else owning_package
    
    # 4. Analyze error, heal, and re-run
    if error_output:
        safe_print(f"🔍 Analyzing error output to build comprehensive healing plan...")
        
        # Unpack the tuple properly
        exit_code, heal_stats = analyze_runtime_failure_and_heal(
            error_output,
            [command] + args,
            Path.cwd(),
            config_manager,
            is_context_aware_run=False,
            cli_owner_spec=owner_spec,
            verbose=False # <--- PASS DEFAULT FALSE OR FETCH FROM SOMEWHERE IF NEEDED
        )
        
        # Print performance stats if available
        if heal_stats:
            # DETECT SHELL HERE
            shell_name = _detect_shell_name()
            _print_performance_comparison(initial_failure_duration_ns, heal_stats, runner_name=shell_name)
            
        return exit_code
    else:
        safe_print(f"❌ Command failed but no error output to analyze.")
        return 1

def _run_script_logic(source_script_path: Path, script_args: list, config_manager: ConfigManager, verbose: bool = False, omnipkg_core=None):
    """Handles execution of Python scripts."""
    from omnipkg.i18n import _
    
    if not omnipkg_core:
        omnipkg_core = OmnipkgCore(config_manager)

    if not source_script_path.exists():
        safe_print(_("❌ Error: Script not found at '{}'").format(source_script_path))
        return 1

    temp_script_path = None
    try:
        # 1. Read the original code ONCE.
        code_str = source_script_path.read_text(encoding='utf-8')
        
        # 2. Heal it for AI import hallucinations.
        healed_code = heal_code_string(code_str, verbose=verbose)
        
        # 3. Patch the HEALED code for Flask port conflicts.
        if auto_patch_flask_port:
            final_code = auto_patch_flask_port(healed_code)
        else:
            final_code = healed_code

        # 4. Write the FINAL, fully processed code to a single temp file.
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as temp_script:
            temp_script_path = Path(temp_script.name)
            temp_script.write(final_code)
        
        # These are the arguments for EXECUTION, using the temp script
        safe_cmd_args = [str(temp_script_path)] + script_args
        safe_print(_("🔄 Syncing omnipkg context..."))
        sync_context_to_runtime()
        safe_print(_("✅ Context synchronized."))
        
        python_exe = config_manager.config.get('python_executable', sys.executable)
        safe_print(_("🚀 Running script with uv, forcing use of current environment (this may take a moment)..."), flush=True)
        
        # Set environment to suppress warnings
        env = os.environ.copy()
        env['PYTHONWARNINGS'] = 'ignore::DeprecationWarning:pkg_resources,ignore::UserWarning:pkg_resources'
        
        initial_cmd = ['uv', 'run', '--no-project', '--python', python_exe, '--'] + safe_cmd_args
        start_time_ns = time.perf_counter_ns()
        
        # First attempt: Try with output capture for error detection
        process = subprocess.Popen(
            initial_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            stdin=subprocess.PIPE,
            text=True, 
            encoding='utf-8', 
            cwd=Path.cwd(), 
            bufsize=0,
            universal_newlines=True,
            env=env
        )
        
        output_lines = []
        interactive_detected = False
        current_line = ""  # ADD THIS to accumulate characters into a line

        try:
            while True:
                # Check if process has terminated
                if process.poll() is not None:
                    # Process finished, read remaining output
                    remaining = process.stdout.read()
                    if remaining:
                        safe_print(remaining, end='', flush=True)
                        output_lines.append(remaining)
                    break
                
                # Use select to check if there's data available (Unix-like systems)
                if HAS_SELECT:
                    ready, _unused1, _unused2 = select.select([process.stdout], [], [], 0.1)
                    if ready:
                        char = process.stdout.read(1)  # Read 1 character at a time
                        if char:
                            safe_print(char, end='', flush=True)
                            current_line += char  # Accumulate into current line
                            
                            # Check for newline - complete line
                            if char == '\n':
                                output_lines.append(current_line)
                                
                                # Detect interactive prompts on complete lines
                                if any(prompt in current_line.lower() for prompt in [
                                    'enter your choice:', 'please choose', 'select an option',
                                    'type a number:', 'input:', '(y/n)', 'continue?'
                                ]):
                                    interactive_detected = True
                                    break
                                
                                current_line = ""  # Reset for next line
                        else:
                            break
                    else:
                        time.sleep(0.01)
                else:
                    # Fallback for systems without select
                    try:
                        char = process.stdout.read(1)
                        if char:
                            safe_print(char, end='', flush=True)
                            current_line += char
                            
                            if char == '\n':
                                output_lines.append(current_line)
                                
                                if any(prompt in current_line.lower() for prompt in [
                                    'enter your choice:', 'please choose', 'select an option',
                                    'type a number:', 'input:', '(y/n)', 'continue?'
                                ]):
                                    interactive_detected = True
                                    break
                                
                                current_line = ""
                        else:
                            break
                    except:
                        break
            
            if interactive_detected:
                safe_print(_("\n🎮 Interactive script detected! Switching to direct mode..."))
                # Terminate the captured process
                process.terminate()
                process.wait()
                
                # Re-run with direct stdin/stdout for interactive mode
                safe_print(_("🚀 Re-launching script with full interactive support..."))
                direct_process = subprocess.Popen(
                    initial_cmd,
                    stdin=sys.stdin,
                    stdout=sys.stdout, 
                    stderr=sys.stderr,
                    cwd=Path.cwd(),
                    env=env
                )
                
                try:
                    return_code = direct_process.wait()
                    end_time_ns = time.perf_counter_ns()
                    
                    # ✅ FIX: Don't return immediately - check for errors and heal!
                    if return_code != 0:
                        safe_print(f"\n❌ Interactive script exited with code: {return_code}")
                        safe_print("🤖 [AI-INFO] Script execution failed. Attempting to heal...")
                        
                        # Since we can't capture output in interactive mode, we need to re-run
                        # in capture mode to get the error output for healing
                        safe_print(_("🔍 Re-running in capture mode to analyze errors..."))
                        capture_process = subprocess.Popen(
                            initial_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            stdin=subprocess.PIPE,
                            text=True,
                            encoding='utf-8',
                            cwd=Path.cwd(),
                            env=env
                        )
                        full_output, _ = capture_process.communicate()
                        
                        # Analyze and heal
                        exit_code, heal_stats = analyze_runtime_failure_and_heal(
                            full_output, safe_cmd_args, source_script_path, config_manager, 
                            is_context_aware_run=False)
                        
                        if heal_stats:
                            _print_performance_comparison(end_time_ns - start_time_ns, heal_stats)
                        
                        return exit_code
                    else:
                        # Success in interactive mode
                        safe_print("\n✅ Interactive script completed successfully via uv.")
                        safe_print("🤖 [AI-INFO] Script executed successfully without errors.")
                        return 0
                        
                except KeyboardInterrupt:
                    safe_print("\n🛑 Process interrupted by user")
                    direct_process.terminate()
                    direct_process.wait()
                    return 130
            else:
                # Non-interactive, continue with normal flow
                return_code = process.wait()
                end_time_ns = time.perf_counter_ns()
                full_output = "".join(output_lines)
                
        except KeyboardInterrupt:
            safe_print("\n🛑 Process interrupted by user")
            process.terminate()
            process.wait()
            return 130
        
        # Filter out pkg_resources warnings from output
        filtered_lines = []
        skip_next = False
        for line in full_output.split('\n'):
            if 'pkg_resources' in line or 'UserWarning' in line:
                skip_next = True
                continue
            if skip_next and line.strip().startswith('from pkg_resources'):
                skip_next = False
                continue
            skip_next = False
            filtered_lines.append(line)
        full_output = '\n'.join(filtered_lines)
        
        # Check for pytest failures - CRITICAL for AI agents to know they need to fix code
        has_pytest_failure = False
        if "FAILED" in full_output and ("test session starts" in full_output or "short test summary info" in full_output):
            has_pytest_failure = True
            # Force non-zero exit code if pytest detected failures but process returned 0
            if return_code == 0:
                return_code = 1
                safe_print("\n🔍 Pytest failure detected in output. Setting failure status for AI agents.")
        
        # Check for healable errors
        has_healable_error = any(re.search(pattern, full_output, re.MULTILINE) for pattern in [
            r"A module that was compiled using NumPy 1\.x cannot be run in[\s\S]*?NumPy 2\.0",
            r"numpy\.dtype size changed, may indicate binary incompatibility"
        ])
        
        if return_code == 0 and not has_healable_error:
            # Success case - provide clear message for AI and user
            if "test session starts" in full_output and "passed" in full_output.lower():
                # Silent pytest success
                passed_match = re.search(r'(\d+) passed', full_output)
                if passed_match:
                    num_passed = passed_match.group(1)
                    safe_print(f"\n✅ All tests passed! ({num_passed} test(s) successful)")
                    safe_print("🤖 [AI-INFO] Script executed successfully with all tests passing.")
                else:
                    safe_print("\n✅ All tests passed!")
                    safe_print("🤖 [AI-INFO] Script executed successfully with all tests passing.")
            else:
                safe_print("\n✅ Script executed successfully via uv.")
                safe_print("🤖 [AI-INFO] Script executed successfully without errors.")
            
            return 0
        
        if has_pytest_failure:
            safe_print("\n❌ Tests FAILED - AI agents should debug and fix the code!")
            safe_print("🤖 [AI-INFO] Script execution resulted in test failures. Code needs debugging.")
        
        if return_code == 0:
            safe_print("\n🔍 UV succeeded but detected healable errors in output...")
        else:
            safe_print("⏱️  UV run failed in: {:.3f} ms ({:,} ns)".format(
                (end_time_ns - start_time_ns) / 1_000_000, end_time_ns - start_time_ns))
            safe_print("🤖 [AI-INFO] Script execution failed with errors. Attempting to heal...")
        
        # CHANGED: Pass the safe_cmd_args for re-execution, but the SOURCE script path for analysis.
        exit_code, heal_stats = analyze_runtime_failure_and_heal(
            full_output, safe_cmd_args, source_script_path, config_manager, 
            is_context_aware_run=False, omnipkg_instance=omnipkg_core
        )
        
        if heal_stats:
            _print_performance_comparison(end_time_ns - start_time_ns, heal_stats)
        
        return exit_code
        
    finally:
        if temp_script_path and temp_script_path.exists():
            temp_script_path.unlink()

def _print_performance_comparison(initial_ns, heal_stats, runner_name="UV"):
    """Prints the final performance summary comparing Runner failure time to omnipkg execution time."""
    if not initial_ns or not heal_stats:
        return
        
    failure_time_ms = initial_ns / 1_000_000
    
    # Use 'activation_time_ns' for bubbles.
    if heal_stats.get('type') == 'package_install':
        execution_time_ms = heal_stats['total_swap_time_ns'] / 1_000_000
        execution_ns = heal_stats['total_swap_time_ns']
        omni_label = "omnipkg Execution"
    else:
        # Use activation only (Fair comparison)
        ns_val = heal_stats.get('activation_time_ns', heal_stats['total_swap_time_ns'])
        execution_time_ms = ns_val / 1_000_000
        execution_ns = int(ns_val)
        omni_label = "omnipkg Activation"
        
    if execution_time_ms <= 0:
        return

    speed_ratio = failure_time_ms / execution_time_ms
    speed_percentage = ((failure_time_ms - execution_time_ms) / execution_time_ms) * 100

    runner_upper = runner_name.upper()
    runner_label = f"{runner_name} Failed Run"
    
    # --- TABLE ALIGNMENT LOGIC ---
    max_label_width = max(len(runner_label), len(omni_label))
    row_fmt = f"{{:<{max_label_width}}} : {{:>10.3f}} ms  ({{:>15,}} ns)"
    
    safe_print("\n" + "="*70)
    safe_print(f"🚀 PERFORMANCE COMPARISON: {runner_upper} vs OMNIPKG")
    safe_print("="*70)
    
    safe_print(row_fmt.format(runner_label, failure_time_ms, initial_ns))
    safe_print(row_fmt.format(omni_label, execution_time_ms, execution_ns))
    
    safe_print("-" * 70)
    
    # --- SUMMARY ALIGNMENT LOGIC ---
    # 1. Format the numbers based on magnitude (preserving original logic)
    if speed_ratio >= 1000:
        ratio_str = f"{speed_ratio:>10.0f}"
    elif speed_ratio >= 100:
        ratio_str = f"{speed_ratio:>10.1f}"
    else:
        ratio_str = f"{speed_ratio:>10.2f}"

    if speed_percentage >= 10000:
        perc_str = f"{speed_percentage:>10.0f}"
    elif speed_percentage >= 1000:
        perc_str = f"{speed_percentage:>10.1f}"
    else:
        perc_str = f"{speed_percentage:>10.2f}"

    # 2. Print with fixed label width (15 chars covers "🎯 omnipkg is" and padding for "💥 That's")
    summary_label_width = 15
    
    # FIX: Extract the label strings first to avoid backslash in f-string
    label1 = "🎯 omnipkg is"
    label2 = "💥 That's"
    
    safe_print(f"{label1:<{summary_label_width}}{ratio_str}x FASTER than {runner_name}!")
    safe_print(f"{label2:<{summary_label_width}}{perc_str}% improvement!")
    
    safe_print("="*70)
    safe_print("🌟 Same environment, zero downtime, microsecond swapping!")
    safe_print("="*70 + "\n")

def _detect_shell_name():
    """Detects the likely shell name for display purposes."""
    # 1. Check common shell env var (Linux/Mac)
    shell_path = os.environ.get('SHELL')
    if shell_path:
        return Path(shell_path).stem.capitalize() # e.g., /bin/bash -> Bash
        
    # 2. Check ComSpec (Windows CMD)
    comspec = os.environ.get('COMSPEC')
    if comspec:
        return "CMD"
        
    # 3. Check for PowerShell indicators
    if 'PSModulePath' in os.environ:
        return "PowerShell"
        
    return "System"
 
def run_with_healing_wrapper(required_specs, original_script_path, original_script_args, config_manager, isolation_mode='strict', verbose=False):
    """
    Generates and executes the temporary wrapper script. This version creates a
    robust sys.path in the subprocess, enabling it to find both the omnipkg
    source and its installed dependencies like 'packaging'.
    
    NOW ACCEPTS: A list of package specs like ["numpy==1.26.4", "pandas==2.0.0"]
    """
    import site
    import importlib.util
    
    # Convert single string to list for backward compatibility
    if isinstance(required_specs, str):
        required_specs = [required_specs]
    
    if verbose:
        safe_print("\n🔍 PRE-WRAPPER DEBUGGING:")
        safe_print(f"   Current Python executable: {sys.executable}")
        safe_print(f"   Current working directory: {os.getcwd()}")
        safe_print(f"   Project root: {project_root}")
    
    # Check if packaging is available in current process
    try:
        import packaging
        safe_print(f"   ✅ packaging found at: {packaging.__file__}")
    except ImportError as e:
        safe_print(f"   ❌ packaging not available in current process: {e}")
    
    # Get all possible site-packages paths
    site_packages_paths = []
    
    # From config
    config_site_packages = config_manager.config.get('site_packages_path')
    if config_site_packages:
        site_packages_paths.append(config_site_packages)
        if verbose:
            safe_print(f"   Config site-packages: {config_site_packages}")
    
    # From site module
    for path in site.getsitepackages():
        if path not in site_packages_paths:
            site_packages_paths.append(path)
            if verbose:
                safe_print(f"   Site getsitepackages: {path}")
    
    # From site.USER_SITE
    if hasattr(site, 'USER_SITE') and site.USER_SITE:
        if site.USER_SITE not in site_packages_paths:
            site_packages_paths.append(site.USER_SITE)
            if verbose:
                safe_print(f"   Site USER_SITE: {site.USER_SITE}")
    
    # Check current sys.path for site-packages
    for path in sys.path:
        if 'site-packages' in path and path not in site_packages_paths:
            site_packages_paths.append(path)
            if verbose:
                safe_print(f"   Current sys.path site-packages: {path}")
    
    # Check each site-packages path for packaging module
    packaging_locations = []
    for sp_path in site_packages_paths:
        if os.path.exists(sp_path):
            packaging_path = os.path.join(sp_path, 'packaging')
            packaging_init = os.path.join(sp_path, 'packaging', '__init__.py')
            if os.path.exists(packaging_path) and os.path.exists(packaging_init):
                packaging_locations.append(sp_path)
                if verbose:
                    safe_print(f"   📦 packaging found in: {sp_path}")
        else:
            if verbose:
                safe_print(f"   ❌ site-packages path doesn't exist: {sp_path}")
    
    if not packaging_locations:
        safe_print("   ⚠️  WARNING: No packaging module found in any site-packages!")
    
    # Use the first site-packages path that has packaging, or fallback to config
    site_packages_path = packaging_locations[0] if packaging_locations else config_site_packages
    if not site_packages_path and site_packages_paths:
        site_packages_path = site_packages_paths[0]
    
    safe_print(f"   🎯 Selected site-packages for wrapper: {site_packages_path}")

    # Build the nested loaders structure OUTSIDE the wrapper
    nested_loaders_str = ""
    indentation = "    "  # Start with 4 spaces for inside the try block
    for spec in required_specs:
        pkg_name = re.sub(r'[^a-zA-Z0-9_]', '_', spec.split('==')[0])
        nested_loaders_str += f"{indentation}with omnipkgLoader('{spec}', config=config, isolation_mode='{isolation_mode}', force_activation=True) as loader_{pkg_name}:\n"
        # --- ADD THIS LINE ---
        nested_loaders_str += f"{indentation}    loader_instances.append(loader_{pkg_name})\n" 
        # ---------------------
        indentation += "    "
    
    # The code that runs at the deepest nesting level
    run_script_code = textwrap.indent(f"""\
local_project_path = r"{str(original_script_path.parent)}"
if local_project_path not in sys.path:
    sys.path.insert(0, local_project_path)
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
safe_print(f"\\n🚀 Running target script inside the combined bubble + local context...")
sys.argv = [{str(original_script_path)!r}] + {original_script_args!r}
runpy.run_path({str(original_script_path)!r}, run_name="__main__")
""", prefix=indentation)

    full_loader_block = nested_loaders_str + run_script_code

    # Enhanced wrapper content with comprehensive debugging
    wrapper_content = textwrap.dedent(f"""\

import sys, os, runpy, json, re
from pathlib import Path
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

# --- VERBOSITY CONTROL ---
VERBOSE_MODE = {str(verbose)}

def vprint(msg):
    '''Only print if verbose mode is enabled'''
    if VERBOSE_MODE:
        safe_print(msg)

# DEBUGGING: Show initial state (only if verbose)
vprint("🔍 WRAPPER SUBPROCESS DEBUGGING:")
vprint(f"   Python executable: {{sys.executable}}")
vprint(f"   Initial sys.path length: {{len(sys.path)}}")
vprint(f"   Working directory: {{os.getcwd()}}")

# Show first few sys.path entries
if VERBOSE_MODE:
    for i, path in enumerate(sys.path[:5]):
        vprint(f"   sys.path[{{i}}]: {{path}}")
    if len(sys.path) > 5:
        vprint(f"   ... and {{len(sys.path) - 5}} more entries")

# --- COMPLETE SYS.PATH INJECTION ---
project_root_path = r"{project_root}"
main_site_packages = r"{site_packages_path}"

vprint(f"\\n   🔧 Adding project root: {{project_root_path}}")
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)
    vprint(f"      ✅ Added to sys.path[0]")
else:
    vprint(f"      ⚠️  Already in sys.path")

vprint(f"   🔧 Adding site-packages: {{main_site_packages}}")
if main_site_packages and main_site_packages not in sys.path:
    sys.path.insert(1, main_site_packages)
    vprint(f"      ✅ Added to sys.path[1]")
else:
    vprint(f"      ⚠️  Already in sys.path or None")

# Add all potential site-packages paths
additional_paths = {repr(site_packages_paths)}
vprint(f"   🔧 Adding {{len(additional_paths)}} additional paths...")
for add_path in additional_paths:
    if add_path and os.path.exists(add_path) and add_path not in sys.path:
        sys.path.append(add_path)
        vprint(f"      ✅ Added: {{add_path}}")

vprint(f"\\n   📊 Final sys.path length: {{len(sys.path)}}")

# Test critical imports before proceeding
vprint("\\n   🧪 Testing critical imports...")

# Test packaging import
try:
    import packaging
    vprint(f"      ✅ packaging: {{packaging.__file__}}")
except ImportError as e:
    vprint(f"      ❌ packaging failed: {{e}}")
    if VERBOSE_MODE:
        safe_print("      🔍 Searching for packaging in sys.path...")
        for i, path in enumerate(sys.path):
            packaging_path = os.path.join(path, 'packaging')
            if os.path.exists(packaging_path):
                safe_print(f"         Found packaging dir in sys.path[{{i}}]: {{path}}")
                init_file = os.path.join(packaging_path, '__init__.py')
                safe_print(f"         __init__.py exists: {{os.path.exists(init_file)}}")

# Test omnipkg imports
try:
    from omnipkg.loader import omnipkgLoader
    vprint(f"      ✅ omnipkgLoader imported")
except ImportError as e:
    vprint(f"      ❌ omnipkgLoader failed: {{e}}")
    
try:
    from omnipkg.i18n import _
    vprint(f"      ✅ omnipkg.i18n imported")
except ImportError as e:
    vprint(f"      ❌ omnipkg.i18n failed: {{e}}")
# --- END OF PATH INJECTION ---

# With a correct path, these imports will now succeed.
try:
    from omnipkg.loader import omnipkgLoader
    from omnipkg.i18n import _
except ImportError as e:
    # This is a fallback error for debugging if the path injection fails.
    safe_print(f"\\nFATAL: Could not import omnipkg modules after path setup. Error: {{e}}")
    safe_print(f"\\nDEBUG: Final sys.path ({{len(sys.path)}} entries):")
    for i, path in enumerate(sys.path):
        exists = "✅" if os.path.exists(path) else "❌"
        safe_print(f"   [{{i:2d}}] {{exists}} {{path}}")
    
    # Check for omnipkg specifically
    safe_print(f"\\nDEBUG: Checking for omnipkg module...")
    for i, path in enumerate(sys.path):
        omnipkg_path = os.path.join(path, 'omnipkg')
        if os.path.exists(omnipkg_path):
            safe_print(f"   Found omnipkg dir in sys.path[{{i}}]: {{path}}")
            loader_file = os.path.join(omnipkg_path, 'loader.py')
            safe_print(f"   loader.py exists: {{os.path.exists(loader_file)}}")
    sys.exit(1)

lang_from_env = os.environ.get('OMNIPKG_LANG')
if lang_from_env: _.set_language(lang_from_env)

config = json.loads(r'''{json.dumps(config_manager.config)}''')
loader_instances = []

safe_print(f"\\n🌀 omnipkg auto-heal: Wrapping script with loaders for {required_specs!r}...")
safe_print('-' * 60)

try:
{full_loader_block}
except Exception:
    import traceback

    traceback.print_exc()
    sys.exit(1)
finally:
    # --- FIX: AGGREGATE STATS FROM ALL LAYERS ---
    if loader_instances:
        total_activation_ns = 0
        last_stats = None
        
        for loader in loader_instances:
            stats = loader.get_performance_stats()
            if stats:
                # Sum up the setup costs of every layer
                total_activation_ns += stats.get('activation_time_ns', 0)
                last_stats = stats
        
        if last_stats:
            # Report the cumulative time to the parent process
            last_stats['activation_time_ns'] = total_activation_ns
            safe_print(f"OMNIPKG_STATS_JSON:{{json.dumps(last_stats)}}", flush=True)

safe_print('-' * 60)
safe_print(_("✅ Script completed successfully inside omnipkg bubble."))
""")

    temp_script_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(wrapper_content)
            temp_script_path = f.name

        safe_print(f"   💾 Temporary wrapper script: {temp_script_path}")

        heal_command = [config_manager.config.get('python_executable', sys.executable), temp_script_path]
        safe_print(_("\n🚀 Re-running with omnipkg auto-heal..."))

        process = subprocess.Popen(heal_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')

        output_lines = []
        for line in iter(process.stdout.readline, ''):
            if not line.startswith("OMNIPKG_STATS_JSON:"):
                safe_print(line, end='')
            output_lines.append(line)

        return_code = process.wait()
        heal_stats = None

        full_output = "".join(output_lines)
        for line in full_output.splitlines():
            if line.startswith("OMNIPKG_STATS_JSON:"):
                try:
                    stats_json = line.split(":", 1)[1]
                    heal_stats = json.loads(stats_json)
                    break
                except (IndexError, json.JSONDecodeError):
                    continue

        return return_code, heal_stats
    finally:
        if temp_script_path and os.path.exists(temp_script_path):
            os.unlink(temp_script_path)

def run_cli_with_healing_wrapper(required_specs, command, command_args, config_manager):
    """
    Like run_with_healing_wrapper, but for CLI binaries instead of Python scripts.
    Uses the SAME robust nested loader logic.
    """
    import site
    import importlib.util
    
    if isinstance(required_specs, str):
        required_specs = [required_specs]
    
    # Get site-packages (same logic as your script wrapper)
    site_packages_paths = []
    config_site_packages = config_manager.config.get('site_packages_path')
    if config_site_packages:
        site_packages_paths.append(config_site_packages)
    
    for path in site.getsitepackages():
        if path not in site_packages_paths:
            site_packages_paths.append(path)
    
    if hasattr(site, 'USER_SITE') and site.USER_SITE:
        if site.USER_SITE not in site_packages_paths:
            site_packages_paths.append(site.USER_SITE)
    
    packaging_locations = []
    for sp_path in site_packages_paths:
        if os.path.exists(sp_path):
            packaging_path = os.path.join(sp_path, 'packaging')
            if os.path.exists(packaging_path):
                packaging_locations.append(sp_path)
    
    site_packages_path = packaging_locations[0] if packaging_locations else config_site_packages

    # Build nested loaders (SAME as your script version)
    nested_loaders_str = ""
    indentation = "    "
    for spec in required_specs:
        pkg_name = re.sub(r'[^a-zA-Z0-9_]', '_', spec.split('==')[0])
        nested_loaders_str += f"{indentation}with omnipkgLoader('{spec}', config=config, isolation_mode='overlay', force_activation=True) as loader_{pkg_name}:\n"
        # --- ADD THIS LINE ---
        nested_loaders_str += f"{indentation}    loader_instances.append(loader_{pkg_name})\n"
        # ---------------------
        indentation += "    "
    
    # THE KEY DIFFERENCE: Instead of runpy.run_path, we exec the CLI command
    run_cli_code = textwrap.indent(f"""\
try:
    from omnipkg.common_utils import safe_print
except ImportError:
    def safe_print(msg, **kwargs): print(msg, **kwargs)

env = os.environ.copy()
env['PYTHONPATH'] = os.pathsep.join(sys.path)

cmd_path = shutil.which({command!r}) or {command!r}
full_cmd = [cmd_path] + {command_args!r}

safe_print(f"🚀 Re-launching '{{cmd_path}}' in healed environment...")
safe_print("-" * 60)

try:
    sys.stdout.flush()
    sys.stderr.flush()
    result = subprocess.run(full_cmd, env=env)
    sys.exit(result.returncode)
except subprocess.CalledProcessError as e:
    sys.exit(e.returncode)
except Exception as e:
    safe_print(f"❌ Error: {{e}}")
    sys.exit(1)
""", prefix=indentation)

    # SAME wrapper content structure as your script version
    full_loader_block = nested_loaders_str + run_cli_code
    indented_loader_block = textwrap.indent(full_loader_block, '    ')  # ← Do this FIRST

    # SAME wrapper content structure as your script version
    wrapper_content = f"""\
import sys, os, subprocess, shutil, json, re
from pathlib import Path

# Path injection (same as your script wrapper)
project_root_path = r"{project_root}"
main_site_packages = r"{site_packages_path}"

if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)
if main_site_packages and main_site_packages not in sys.path:
    sys.path.insert(1, main_site_packages)

additional_paths = {site_packages_paths!r}
for add_path in additional_paths:
    if add_path and os.path.exists(add_path) and add_path not in sys.path:
        sys.path.append(add_path)

try:
    from omnipkg.common_utils import safe_print
except ImportError:
    def safe_print(msg, **kwargs): print(msg, **kwargs)

try:
    from omnipkg.loader import omnipkgLoader
    from omnipkg.i18n import _
except ImportError as e:
    safe_print(f"FATAL: Could not import omnipkg modules. Error: {{e}}")
    sys.exit(1)

config = json.loads(r'''{json.dumps(config_manager.config)}''')
loader_instances = []

safe_print(f"🌀 omnipkg auto-heal: Wrapping CLI with loaders for {required_specs!r}...")
safe_print('-' * 60)

try:
{indented_loader_block}
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    if loader_instances:
        total_activation_ns = 0
        last_stats = None
        
        for loader in loader_instances:
            stats = loader.get_performance_stats()
            if stats:
                total_activation_ns += stats.get('activation_time_ns', 0)
                last_stats = stats
        
        if last_stats:
            last_stats['activation_time_ns'] = total_activation_ns
            safe_print(f"OMNIPKG_STATS_JSON:{{json.dumps(last_stats)}}", flush=True)

safe_print('-' * 60)
safe_print("✅ CLI command completed successfully inside omnipkg bubble.")
"""

    # Execute wrapper (same as your script version)
    temp_script_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(wrapper_content)
            temp_script_path = f.name

        heal_command = [config_manager.config.get('python_executable', sys.executable), temp_script_path]
        
        process = subprocess.Popen(heal_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')

        output_lines = []
        for line in iter(process.stdout.readline, ''):
            if not line.startswith("OMNIPKG_STATS_JSON:"):
                safe_print(line, end='')
            output_lines.append(line)

        return_code = process.wait()
        heal_stats = None

        full_output = "".join(output_lines)
        for line in full_output.splitlines():
            if line.startswith("OMNIPKG_STATS_JSON:"):
                try:
                    stats_json = line.split(":", 1)[1]
                    heal_stats = json.loads(stats_json)
                except:
                    pass

        return return_code, heal_stats
    finally:
        if temp_script_path and os.path.exists(temp_script_path):
            os.unlink(temp_script_path)