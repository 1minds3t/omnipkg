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
import re
import textwrap
import time
from pathlib import Path
import os
import select # We can import it, but we check the OS before using it

# THE FIX: HAS_SELECT is only True on non-Windows (POSIX) systems.
HAS_SELECT = (os.name == 'posix')

try:
    # Assuming the file is now in a 'utils' subdirectory
    from omnipkg.utils.flask_port_finder import auto_patch_flask_port
except ImportError:
    # Fallback if the structure is different
    try:
        from omnipkg.flask_port_finder import auto_patch_flask_port
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

# CHANGED: Added 'original_script_path_for_analysis' to the signature
def analyze_runtime_failure_and_heal(stderr: str, cmd_args: list, original_script_path_for_analysis: Path, config_manager: ConfigManager, is_context_aware_run: bool):
    """Analyzes stderr for a wide range of errors and triggers the correct healing.
    Uses the original script's path for context-aware analysis.
    """
    healing_plan = set()
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
                return heal_with_package_reinstall(target_package, original_script_path_for_analysis, cmd_args[1:], config_manager)
            else:
                # Try to extract the problematic package from the traceback
                # Look for the last package import in the traceback that's not a built-in
                import_matches = re.findall(r"File \".*?/site-packages/(\w+)/", stderr)
                if import_matches:
                    problematic_package = import_matches[-1]  # Last package in the chain
                    safe_print(f"   - The issue appears to be with '{problematic_package}' package")
                    safe_print(f"   - This package likely has ABI incompatibilities with current dependencies")
                    safe_print(f"🚀 Auto-healing by reinstalling '{problematic_package}' to rebuild against current environment...")
                    return heal_with_package_reinstall(problematic_package, original_script_path_for_analysis, cmd_args[1:], config_manager)
                else:
                    safe_print(f"   - Could not identify the specific problematic package")
                    safe_print(f"❌ Auto-healing aborted. Manual intervention may be required.")
                    return 1, None

    # Pattern 2: Handle explicit version conflicts from requirements.
    conflict_patterns = [
        (r"AssertionError: Incorrect ([\w\-]+) version! Expected ([\d\.]+)", 1, 2, "Runtime version assertion"),
        (r"requires ([\w\-]+)==([\d\.]+), but you have", 1, 2, "Import-time dependency conflict"),
        (r"VersionConflict:.*?Requirement\.parse\('([\w\-]+)==([\d\.]+)'\)", 1, 2, "Setuptools VersionConflict")
    ]
    for regex, pkg_group, ver_group, description in conflict_patterns:
        match = re.search(regex, stderr)
        if match:
            pkg_name = match.group(pkg_group).lower()
            expected_version = match.group(ver_group)
            failed_spec = f"{pkg_name}=={expected_version}"
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
                safe_print(f"\n🔍 Deep dependency conflict detected (AttributeError).")
                safe_print(f"   - The root cause appears to be the '{culprit_package}' package or its dependencies.")
                safe_print(f"🚀 Auto-healing by creating an isolated bubble for '{culprit_package}'...")
                healing_plan.add(culprit_package)

        # If the smart approach fails, use the simpler regex as a fallback.
        fallback_match = re.search(r"AttributeError: module '([\w\-\.]+)' has no attribute", stderr)
        if fallback_match:
            pkg_name_to_upgrade = fallback_match.group(1)
            safe_print(f"\n🔍 Dependency conflict detected (AttributeError). Using fallback.")
            safe_print(f"   - The package '{pkg_name_to_upgrade}' may be outdated.")
            safe_print(_("🚀 Auto-healing by attempting to upgrade the package..."))
            return heal_with_missing_package(
                pkg_name_to_upgrade, Path(cmd_args[0]), cmd_args[1:], original_script_path_for_analysis, config_manager, is_context_aware_run
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
                    script_path=original_script_path_for_analysis, # <--- THIS IS THE FIX
                    script_args=cmd_args[1:],
                    config_manager=config_manager,
                    original_script_path_for_analysis=original_script_path_for_analysis,
                    heal_type='local_context_run',
                    is_context_aware_run=True
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
        (r"Please make sure you have `(\w+)` installed", 1, "Missing required package"),
        (r"Please make sure you have `(\w+)` installed", 1, "Missing required package"),
        (r"No module named '(\w+)'", 1, "Missing module import"),
        (r"Recommended: pip install (\w+)", 1, "Missing recommended package"),
        (r"No module named '(\w+)'", 1, "Missing module import"),
        (r"ModuleNotFoundError: No module named '([\w\.]+)'", 1, "Module not found"),
        (r"ImportError: cannot import name '(\w+)'", 1, "Import error"),
        (r"requires (\w+) to be installed", 1, "Dependency requirement")
            ]
    
    for regex, pkg_group, description in missing_package_patterns:
        match = re.search(regex, stderr)
        if match:
            pkg_name = match.group(pkg_group).lower()
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
        # Convert set to list for consistent ordering
        specs_to_heal = sorted(list(healing_plan))
        safe_print(f"\n🔍 Comprehensive Healing Plan Compiled: {specs_to_heal}")
        safe_print("   - This plan addresses all detected issues in a single operation.")
        return heal_with_bubble(specs_to_heal, original_script_path_for_analysis, cmd_args[1:], config_manager)
    
    # Final fallback if no patterns match.
    safe_print(_("❌ Script failed with an unhandled runtime error that could not be auto-healed."))
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
    if '.' in module_name:
        base_module = module_name.split('.')[0]
        if base_module in module_to_package:
            return module_to_package[base_module]
        # If no base mapping, a good heuristic is to replace dots with hyphens.
        return module_name.replace('.', '-')

    # Step 3: Final fallback. If it's a simple name with no mapping (like 'pygame'),
    # assume the package name is the same as the module name. This fixes the bug.
    return module_name
    
    # If no mapping found, assume module name == package name
    if base_module in module_to_package:
        safe_print(f"INFO: Found direct mapping for '{base_module}'. Package is '{module_to_package[base_module]}'")
        return module_to_package[base_module]

    # --- STEP 3: HEURISTIC FOR REFACTORED LIBRARIES (THE QISKIT FIX) ---
    # This is the new, powerful logic.
    import_match = re.search(r"cannot import name \'(\w+)\' from \'([\w\.]+)\'", error_message)
    if import_match:
        name_to_import = import_match.group(1)
        module_it_failed_on = import_match.group(2).split('.')[0]
        
        # Construct a guess like "qiskit-aer"
        heuristic_package_name = f"{module_it_failed_on}-{name_to_import.lower()}"
        safe_print(f"INFO: Applying refactor heuristic. Guessing package is '{heuristic_package_name}'")
        return heuristic_package_name

    # --- STEP 4: HEURISTIC FOR NAMESPACE PACKAGES (YOUR DOTTED-NAME LOGIC) ---
    # e.g., 'google.cloud.storage' -> 'google-cloud-storage'
    if '.' in full_module_path:
        namespace_package_name = full_module_path.replace('.', '-')
        safe_print(f"INFO: Applying namespace heuristic. Guessing package is '{namespace_package_name}'")
        return namespace_package_name

    # --- STEP 5: FINAL FALLBACK ---
    # The simplest case: if no other rules match, assume the module name is the package name.
    safe_print(f"INFO: No specific rule matched. Falling back to base module name '{base_module}' as the package name.")
    return base_module


# CHANGED: Added 'original_script_path_for_analysis' to the signature
def heal_with_missing_package(pkg_name: str, temp_script_path: Path, temp_script_args: list, original_script_path_for_analysis: Path, config_manager, is_context_aware_run: bool):
    """Installs/upgrades a package and re-runs the script, preserving run context."""
    safe_print(_("🚀 Auto-installing/upgrading missing package... (This may take a moment)"))
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
    
# CHANGED: Added 'original_script_path_for_analysis' to the signature
def _run_script_with_healing(script_path, script_args, config_manager, original_script_path_for_analysis, heal_type='execution', is_context_aware_run=False):
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
        return analyze_runtime_failure_and_heal(output, [str(script_path)] + script_args, original_script_path_for_analysis, config_manager, is_context_aware_run)
    
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

def heal_with_bubble(required_specs, original_script_path, original_script_args, config_manager):
    """
    Ensures the required bubble exists with intelligent dependency detection,
    auto-installs/resolves if missing, then re-runs the script inside it.
    
    NOW ACCEPTS: Either a single string like "numpy==1.26.4" 
                 OR a list like ["numpy==1.26.4", "pandas==2.0.0"]
    """
    omnipkg_instance = OmnipkgCore(config_manager)
    
    # NEW: Convert single string to list for uniform handling
    if isinstance(required_specs, str):
        required_specs = [required_specs]
    
    # NEW: We'll process each spec and collect the final resolved versions
    final_specs = []
    
    # NEW: Loop through all specs (or just one if it was a string)
    for required_spec in required_specs:  # ← FIX 1: Loop through the list
        
        # Step 1: Resolve the primary failing package and determine final_spec
        if '==' not in required_spec:
            pkg_name = required_spec
            safe_print(_("💡 Missing bubble detected: {} (version not specified)").format(pkg_name))
            safe_print(_("🚀 Auto-resolving and installing latest compatible version... (This may take a moment)"))
            
            # Original auto-resolution logic preserved
            return_code = omnipkg_instance.smart_install([pkg_name])

            if return_code != 0:
                safe_print(_("\n❌ Auto-install failed for {}.").format(pkg_name))
                return 1, None

            # Get the resolved version (original logic)
            latest_version = omnipkg_instance._get_active_version_from_environment(pkg_name)
            if not latest_version:
                safe_print(_("\n❌ FATAL: Could not determine installed version for {} after install. Aborting.").format(pkg_name))
                return 1, None

            # Construct the final spec
            final_spec = f"{pkg_name}=={latest_version}"
            safe_print(_("\n✅ Resolved and installed: {}").format(final_spec))
            final_specs.append(final_spec)  # ← FIX 2: Add to list
        
        else:
            # Version was provided - original logic preserved
            final_spec = required_spec
            pkg_name, pkg_version = final_spec.split('==', 1)
            bubble_dir_name = f'{pkg_name.lower().replace("-", "_")}-{pkg_version}'
            bubble_path = Path(config_manager.config['multiversion_base']) / bubble_dir_name
            
            if not bubble_path.is_dir():
                safe_print(_("💡 Missing bubble detected: {}").format(final_spec))
                safe_print(_("🚀 Auto-installing bubble... (This may take a moment)"))
                if omnipkg_instance.smart_install([final_spec]) != 0:
                    safe_print(_("\n❌ Auto-install failed for {}.").format(final_spec))
                    return 1, None
                safe_print(_("\n✅ Bubble installed successfully: {}").format(final_spec))
            
            final_specs.append(final_spec)  # ← FIX 3: Add to list

    # Step 2: NOW add the intelligence - scan for additional dependencies
    # This is NEW functionality that enhances without breaking existing behavior
    safe_print(_("\n🔍 Analyzing script for additional dependencies to complete the bubble..."))
    additional_packages = set()
    
    # ← FIX 4: Get all package names from final_specs to avoid duplicates
    already_handled = {spec.split('==')[0].lower() for spec in final_specs}
    
    try:
        code = original_script_path.read_text()
        # Find all top-level imports
        imports = re.findall(r'^(?:import|from)\s+([a-zA-Z0-9_]+)', code, re.MULTILINE)
        
        for imp in imports:
            # Skip standard library and already-handled packages
            if imp in {'sys', 'os', 'json', 'csv', 're', 'math', 'datetime', 'pathlib', 
                    'collections', 'itertools', 'functools', 'typing', 'argparse'}:
                continue
            
            # Convert module name to package name using your existing utility
            try:
                pkg_name_found = convert_module_to_package_name(imp)
                # Don't re-add packages we already installed
                if pkg_name_found and pkg_name_found.lower() not in already_handled:  # ← FIX 5: Check against all handled packages
                    additional_packages.add(pkg_name_found)
            except Exception:
                # If conversion fails, try the import name directly
                if imp.lower() not in already_handled:  # ← FIX 6: Check here too
                    additional_packages.add(imp)
        if 'omnipkg' in additional_packages:
            safe_print("   ℹ️  Ignoring 'omnipkg' as an additional dependency to prevent recursion.")
            additional_packages.remove('omnipkg')
        
        if additional_packages:
            additional_list = sorted(list(additional_packages))
            safe_print(_("   📦 Found additional dependencies: {}").format(', '.join(additional_list)))
            safe_print(_("   🚀 Installing additional packages into bubble..."))
            
            # Install additional packages into the same bubble
            # Use the bubble context to ensure they go into the same environment
            for extra_pkg in additional_list:
                install_result = omnipkg_instance.smart_install([extra_pkg])
                if install_result != 0:
                    safe_print(_("   ⚠️  Warning: Could not install {} - script may fail if it's needed").format(extra_pkg))
                else:
                    safe_print(_("   ✅ Added: {}").format(extra_pkg))
                    # ← FIX 7: Optionally add to final_specs if you want ALL packages in nested loaders
                    # (This depends on your requirements - comment out if you don't want this)
                    # latest_version = omnipkg_instance._get_active_version_from_environment(extra_pkg)
                    # if latest_version:
                    #     final_specs.append(f"{extra_pkg}=={latest_version}")
        else:
            safe_print(_("   ℹ️  No additional third-party dependencies detected"))
            
    except Exception as e:
        safe_print(_("   ⚠️  Warning: Could not fully analyze script dependencies: {}").format(str(e)))
        safe_print(_("   ℹ️  Proceeding with primary package only"))

    # Step 3: Execute with the bubble using the 'overlay' mode
    safe_print(_("\n✅ Bubble ready with all detected dependencies"))
    safe_print(_("✅ Activating bubble with: {}").format(final_specs))  # ← FIX 8: Show the list
    return run_with_healing_wrapper(final_specs, original_script_path, original_script_args, config_manager, isolation_mode='overlay')
    
def execute_run_command(cmd_args: list, config_manager: ConfigManager, verbose: bool = False):

    """
    Handles the 'omnipkg run' command by healing and patching the script first,
    then proceeding with execution.
    """
    from omnipkg.i18n import _, SUPPORTED_LANGUAGES # Make sure to import the object
    lang = config_manager.config.get('language')
    if lang:
        _.set_language(lang)
    # --- END OF THE FIX ---

    if not cmd_args:
        safe_print(_('❌ Error: No script specified to run.'))
        return 1
    
    source_script_path = Path(cmd_args[0]).resolve()
    script_args = cmd_args[1:]
    
    if not source_script_path.exists():
        safe_print(_("❌ Error: Script not found at '{}'").format(source_script_path))
        return 1

    temp_script_path = None
    try:
        # --- THIS IS THE CORRECTED LOGIC PIPELINE ---
        
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
        safe_print(_("🚀 Attempting to run script with uv, forcing use of current environment..."))
        
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
        
        try:
            # Read output with timeout to detect if script becomes interactive            
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
                        line = process.stdout.readline()
                        if line:
                            safe_print(line, end='', flush=True)
                            output_lines.append(line)
                            
                            # Detect interactive prompts
                            if any(prompt in line.lower() for prompt in [
                                'enter your choice:', 'please choose', 'select an option',
                                'type a number:', 'input:', '(y/n)', 'continue?'
                            ]):
                                interactive_detected = True
                                break
                        else:
                            break
                    else:
                        time.sleep(0.01)
                else:
                    # Fallback for systems without select
                    try:
                        line = process.stdout.readline()
                        if line:
                            safe_print(line, end='', flush=True)
                            output_lines.append(line)
                            
                            if any(prompt in line.lower() for prompt in [
                                'enter your choice:', 'please choose', 'select an option',
                                'type a number:', 'input:', '(y/n)', 'continue?'
                            ]):
                                interactive_detected = True
                                break
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
                    _initial_run_time_ns = end_time_ns - start_time_ns
                    
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
                            _print_performance_comparison(_initial_run_time_ns, heal_stats)
                        
                        return exit_code
                    else:
                        # Success in interactive mode
                        safe_print("\n✅ Interactive script completed successfully via uv.")
                        safe_print("🤖 [AI-INFO] Script executed successfully without errors.")
                        safe_print("⏱️  UV run completed in: {:.3f} ms ({:,} ns)".format(
                            _initial_run_time_ns / 1_000_000, _initial_run_time_ns))
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
                _initial_run_time_ns = end_time_ns - start_time_ns
                
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
            
            safe_print("⏱️  UV run completed in: {:.3f} ms ({:,} ns)".format(
                _initial_run_time_ns / 1_000_000, _initial_run_time_ns))
            return 0
        
        if has_pytest_failure:
            safe_print("\n❌ Tests FAILED - AI agents should debug and fix the code!")
            safe_print("🤖 [AI-INFO] Script execution resulted in test failures. Code needs debugging.")
        
        if return_code == 0:
            safe_print("\n🔍 UV succeeded but detected healable errors in output...")
        else:
            safe_print("⏱️  UV run failed in: {:.3f} ms ({:,} ns)".format(
                _initial_run_time_ns / 1_000_000, _initial_run_time_ns))
            safe_print("🤖 [AI-INFO] Script execution failed with errors. Attempting to heal...")
        
        # CHANGED: Pass the safe_cmd_args for re-execution, but the SOURCE script path for analysis.
        exit_code, heal_stats = analyze_runtime_failure_and_heal(
            full_output, safe_cmd_args, source_script_path, config_manager, is_context_aware_run=False)
        
        if heal_stats:
            _print_performance_comparison(_initial_run_time_ns, heal_stats)
        
        return exit_code
        
    finally:
        if temp_script_path and temp_script_path.exists():
            temp_script_path.unlink()

# ... (_print_performance_comparison and run_with_healing_wrapper remain the same) ...
def _print_performance_comparison(initial_ns, heal_stats):
    """Prints the final performance summary comparing UV failure time to omnipkg execution time."""
    if not initial_ns or not heal_stats:
        return
        
    uv_failure_time_ms = initial_ns / 1_000_000
    
    # For package installs, we only compare the final execution time (not install time)
    if heal_stats.get('type') == 'package_install':
        execution_time_ms = heal_stats['total_swap_time_ns'] / 1_000_000
        
        if execution_time_ms <= 0:
            return
            
        speed_ratio = uv_failure_time_ms / execution_time_ms
        speed_percentage = ((uv_failure_time_ms - execution_time_ms) / execution_time_ms) * 100

        safe_print("\n" + "="*70)
        safe_print("🚀 PERFORMANCE COMPARISON: UV vs OMNIPKG")
        safe_print("="*70)
        safe_print(f"UV Failed Run:      {uv_failure_time_ms:>8.3f} ms  ({initial_ns:>12,} ns)")
        safe_print(f"omnipkg Execution:  {execution_time_ms:>8.3f} ms  ({heal_stats['total_swap_time_ns']:>12,} ns)")
        safe_print("-" * 70)
        
    else:
        # Original bubble swapping performance comparison
        omnipkg_time_ms = heal_stats['total_swap_time_ns'] / 1_000_000
        
        if omnipkg_time_ms <= 0:
            return

        speed_ratio = uv_failure_time_ms / omnipkg_time_ms
        speed_percentage = ((uv_failure_time_ms - omnipkg_time_ms) / omnipkg_time_ms) * 100

        safe_print("\n" + "="*70)
        safe_print("🚀 PERFORMANCE COMPARISON: UV vs OMNIPKG")
        safe_print("="*70)
        safe_print(f"UV Failed Run:      {uv_failure_time_ms:>8.3f} ms  ({initial_ns:>12,} ns)")
        safe_print(f"omnipkg Healing:    {omnipkg_time_ms:>8.3f} ms  ({heal_stats['total_swap_time_ns']:>12,} ns)")
        safe_print("-" * 70)
    
    # Common performance display logic
    if speed_ratio >= 1000:
        safe_print(f"🎯 omnipkg is {speed_ratio:>6.0f}x FASTER than UV!")
    elif speed_ratio >= 100:
        safe_print(f"🎯 omnipkg is {speed_ratio:>6.1f}x FASTER than UV!")
    else:
        safe_print(f"🎯 omnipkg is {speed_ratio:>6.2f}x FASTER than UV!")
    
    if speed_percentage >= 10000:
        safe_print(f"💥 That's {speed_percentage:>8.0f}% improvement!")
    elif speed_percentage >= 1000:
        safe_print(f"💥 That's {speed_percentage:>8.1f}% improvement!")
    else:
        safe_print(f"💥 That's {speed_percentage:>8.2f}% improvement!")
    
    safe_print("="*70)
    safe_print("🌟 Same environment, zero downtime, microsecond swapping!")
    safe_print("="*70 + "\n")
 
def run_with_healing_wrapper(required_specs, original_script_path, original_script_args, config_manager, isolation_mode='strict'):
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
        safe_print(f"   Config site-packages: {config_site_packages}")
    
    # From site module
    for path in site.getsitepackages():
        if path not in site_packages_paths:
            site_packages_paths.append(path)
            safe_print(f"   Site getsitepackages: {path}")
    
    # From site.USER_SITE
    if hasattr(site, 'USER_SITE') and site.USER_SITE:
        if site.USER_SITE not in site_packages_paths:
            site_packages_paths.append(site.USER_SITE)
            safe_print(f"   Site USER_SITE: {site.USER_SITE}")
    
    # Check current sys.path for site-packages
    for path in sys.path:
        if 'site-packages' in path and path not in site_packages_paths:
            site_packages_paths.append(path)
            safe_print(f"   Current sys.path site-packages: {path}")
    
    # Check each site-packages path for packaging module
    packaging_locations = []
    for sp_path in site_packages_paths:
        if os.path.exists(sp_path):
            packaging_path = os.path.join(sp_path, 'packaging')
            packaging_init = os.path.join(sp_path, 'packaging', '__init__.py')
            if os.path.exists(packaging_path) and os.path.exists(packaging_init):
                packaging_locations.append(sp_path)
                safe_print(f"   📦 packaging found in: {sp_path}")
        else:
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
        # Sanitize the spec to create a valid Python variable name
        pkg_name = re.sub(r'[^a-zA-Z0-9_]', '_', spec.split('==')[0])
        # THE FIX: Add force_activation=True because this is a healing action
        nested_loaders_str += f"{indentation}with omnipkgLoader('{spec}', config=config, isolation_mode='{isolation_mode}', force_activation=True) as loader_{pkg_name}:\n"
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
# DEBUGGING: Show initial state
safe_print("🔍 WRAPPER SUBPROCESS DEBUGGING:")
safe_print(f"   Python executable: {{sys.executable}}")
safe_print(f"   Initial sys.path length: {{len(sys.path)}}")
safe_print(f"   Working directory: {{os.getcwd()}}")

# Show first few sys.path entries
for i, path in enumerate(sys.path[:5]):
    safe_print(f"   sys.path[{{i}}]: {{path}}")
if len(sys.path) > 5:
    safe_print(f"   ... and {{len(sys.path) - 5}} more entries")

# --- COMPLETE SYS.PATH INJECTION ---
project_root_path = r"{project_root}"
main_site_packages = r"{site_packages_path}"

safe_print(f"\\n   🔧 Adding project root: {{project_root_path}}")
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)
    safe_print(f"      ✅ Added to sys.path[0]")
else:
    safe_print(f"      ⚠️  Already in sys.path")

safe_print(f"   🔧 Adding site-packages: {{main_site_packages}}")
if main_site_packages and main_site_packages not in sys.path:
    sys.path.insert(1, main_site_packages)
    safe_print(f"      ✅ Added to sys.path[1]")
else:
    safe_print(f"      ⚠️  Already in sys.path or None")

# Add all potential site-packages paths
additional_paths = {site_packages_paths!r}
safe_print(f"   🔧 Adding {{len(additional_paths)}} additional paths...")
for add_path in additional_paths:
    if add_path and os.path.exists(add_path) and add_path not in sys.path:
        sys.path.append(add_path)
        safe_print(f"      ✅ Added: {{add_path}}")

safe_print(f"\\n   📊 Final sys.path length: {{len(sys.path)}}")

# Test critical imports before proceeding
safe_print("\\n   🧪 Testing critical imports...")

# Test packaging import
try:
    import packaging
    safe_print(f"      ✅ packaging: {{packaging.__file__}}")
except ImportError as e:
    safe_print(f"      ❌ packaging failed: {{e}}")
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
    safe_print(f"      ✅ omnipkgLoader imported")
except ImportError as e:
    safe_print(f"      ❌ omnipkgLoader failed: {{e}}")
    
try:
    from omnipkg.i18n import _
    safe_print(f"      ✅ omnipkg.i18n imported")
except ImportError as e:
    safe_print(f"      ❌ omnipkg.i18n failed: {{e}}")
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
    # Get stats from the last loader if available
    if loader_instances:
        stats = loader_instances[-1].get_performance_stats()
        if stats:
            safe_print(f"OMNIPKG_STATS_JSON:{{json.dumps(stats)}}", flush=True)

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