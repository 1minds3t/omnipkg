
# omnipkg/commands/run.py
try:
    from .common_utils import safe_print
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
try:
    import select
    HAS_SELECT = True
except ImportError:
    # This will be the case on Windows, which is fine.
    HAS_SELECT = False

# --- PROJECT PATH SETUP ---
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from omnipkg.i18n import _
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
from omnipkg.common_utils import sync_context_to_runtime

# Global variable to store initial run timing for comparison
_initial_run_time_ns = None

# CHANGED: Added 'original_script_path_for_analysis' to the signature
def analyze_runtime_failure_and_heal(stderr: str, cmd_args: list, original_script_path_for_analysis: Path, config_manager: ConfigManager, is_context_aware_run: bool):
    """Analyzes stderr for a wide range of errors and triggers the correct healing.
    Uses the original script's path for context-aware analysis.
    """
    # Pattern 1: Prioritize specific, known issues like NumPy 2.0 incompatibility.
    numpy_patterns = [
        r"A module that was compiled using NumPy 1\.x cannot be run in[\s\S]*?NumPy 2\.0",
        r"numpy\.dtype size changed, may indicate binary incompatibility"
    ]
    for pattern in numpy_patterns:
        if re.search(pattern, stderr, re.MULTILINE):
            print(f"\n🔍 NumPy 2.0 compatibility issue detected. Auto-healing with numpy downgrade...")
            print("   - Downgrading to numpy<2.0 for compatibility")
            return heal_with_bubble("numpy==1.26.4", original_script_path_for_analysis, cmd_args[1:], config_manager)

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
            print(f"\n🔍 {description} failed. Auto-healing with omnipkg bubbles...")
            print(_("   - Conflict identified for: {}").format(failed_spec))
            return heal_with_bubble(failed_spec, original_script_path_for_analysis, cmd_args[1:], config_manager)

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
                print(f"\n🔍 Deep dependency conflict detected (AttributeError).")
                print(f"   - The root cause appears to be the '{culprit_package}' package or its dependencies.")
                print(f"🚀 Auto-healing by creating an isolated bubble for '{culprit_package}'...")
                return heal_with_bubble(culprit_package, original_script_path_for_analysis, cmd_args[1:], config_manager)

        # If the smart approach fails, use the simpler regex as a fallback.
        fallback_match = re.search(r"AttributeError: module '([\w\-\.]+)' has no attribute", stderr)
        if fallback_match:
            pkg_name_to_upgrade = fallback_match.group(1)
            print(f"\n🔍 Dependency conflict detected (AttributeError). Using fallback.")
            print(f"   - The package '{pkg_name_to_upgrade}' may be outdated.")
            print(_("🚀 Auto-healing by attempting to upgrade the package..."))
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
                print(f"\n🔍 {description} detected - This appears to be a LOCAL IMPORT.")
                print(f"   - The script failed to import '{full_module_name}'.")
                print(f"   - A local module '{top_level_module}' was found in the project directory.")
                print(_("🚀 Attempting a context-aware re-run..."))
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
                print(f"\n🔍 {description} detected - this appears to be a PROJECT PACKAGE.")
                print("\n💡 This is likely a package that needs to be installed in editable mode.")
                print(f"   1. Try installing with: pip install -e {parent_dir}")
                print("\n❌ Auto-healing aborted. Please install the local project package manually.")
                return 1, None
            
            # Finally, if it's not local, assume it's a missing PyPI package.
            print(f"\n🔍 {description} detected. Auto-healing by installing missing package...")
            pkg_name = convert_module_to_package_name(top_level_module)
            return heal_with_missing_package(pkg_name, Path(cmd_args[0]), cmd_args[1:], original_script_path_for_analysis, config_manager, is_context_aware_run)

    # Final fallback if no patterns match.
    print(_("❌ Script failed with an unhandled runtime error that could not be auto-healed."))
    return 1, None

# ... (convert_module_to_package_name remains the same) ...
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
    
    # Handle dotted module names (e.g., 'google.auth' -> 'google-auth')
    if '.' in module_name:
        # Try the full dotted name first
        if module_name in module_to_package:
            return module_to_package[module_name]
        # Then try just the first part
        base_module = module_name.split('.')[0]
        if base_module in module_to_package:
            return module_to_package[base_module]
        # Finally, convert dots to hyphens as a fallback
        return module_name.replace('.', '-')
    
    # If no mapping found, assume module name == package name
    return module_name


# CHANGED: Added 'original_script_path_for_analysis' to the signature
def heal_with_missing_package(pkg_name: str, temp_script_path: Path, temp_script_args: list, original_script_path_for_analysis: Path, config_manager, is_context_aware_run: bool):
    """Installs/upgrades a package and re-runs the script, preserving run context."""
    print(_("🚀 Auto-installing/upgrading missing package... (This may take a moment)"))
    omnipkg_instance = OmnipkgCore(config_manager)
    return_code = omnipkg_instance.smart_install([pkg_name])
    
    if return_code != 0:
        print(_("\n❌ Auto-install failed for {}.").format(pkg_name))
        return 1, None

    print(_("\n✅ Package operation successful for: {}").format(pkg_name))
    print(_("🚀 Re-running script with recursive auto-healing..."))

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
            print(_("   - Injecting project directory into PYTHONPATH: {}").format(project_dir))
        
        # Prepend the project path to ensure it's checked first by Python
        current_python_path = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = f"{project_dir}{os.pathsep}{current_python_path}"

    start_time_ns = time.perf_counter_ns()

    # PHASE 1: Quick test run to detect if script is interactive or has errors
    print("🔍 Testing script for interactivity and errors...")
    
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
        
        print("📱 Interactive script detected - switching to direct mode...")
        
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
                    print("\n" + "🎯 " + "="*60)
                    print("🚀 SUCCESS! Auto-healing completed.")
                    _print_performance_comparison(_initial_run_time_ns, heal_stats)
                    print("🎮 Interactive script completed successfully...")
                    print("="*68 + "\n")
            
            return return_code, heal_stats
            
        except KeyboardInterrupt:
            print("\n🛑 Interactive process interrupted by user")
            interactive_process.terminate()
            interactive_process.wait()
            return 130, None
    
    # PHASE 2: Handle non-interactive scripts or scripts with errors
    if test_return_code != 0:
        # Script failed, analyze the error
        print(f"❌ Script failed with return code {test_return_code}")
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
        print(output, end='')

    if _initial_run_time_ns:
        print("\n" + "🎯 " + "="*60)
        print("🚀 SUCCESS! Auto-healing completed.")
        _print_performance_comparison(_initial_run_time_ns, heal_stats)
        print("✅ Script executed successfully...")
        print("="*68 + "\n")
    else:
        print("\n" + "="*60)
        print("✅ Script executed successfully after auto-healing.")
        print("="*60)

    return test_return_code, heal_stats

# ... (heal_with_bubble and execute_run_command have key changes) ...
def heal_with_bubble(required_spec, original_script_path, original_script_args, config_manager):
    """
    Ensures the required bubble exists, auto-installs/resolves if missing, 
    then re-runs the script inside it.
    """
    omnipkg_instance = OmnipkgCore(config_manager)
    
    # Check if a specific version was requested.
    if '==' not in required_spec:
        pkg_name = required_spec
        print(_("💡 Missing bubble detected: {} (version not specified)").format(pkg_name))
        print(_("🚀 Auto-resolving and installing latest compatible version... (This may take a moment)"))
        
        # YOUR EXISTING CODE THAT DOES THE HEAVY LIFTING
        return_code = omnipkg_instance.smart_install([pkg_name])

        if return_code != 0:
            print(_("\n❌ Auto-install failed for {}.").format(pkg_name))
            return 1, None

        # --- THIS IS THE CRITICAL FIX ---
        # 1. After a successful install, ask your tool for the now-active version.
        latest_version = omnipkg_instance._get_active_version_from_environment(pkg_name)
        if not latest_version:
            print(_("\n❌ FATAL: Could not determine installed version for {} after install. Aborting.").format(pkg_name))
            return 1, None

        # 2. Construct the final spec that the loader needs.
        final_spec = f"{pkg_name}=={latest_version}"
        print(_("\n✅ Resolved and installed: {}").format(final_spec))
    # --- END OF FIX ---
    
    else:
        # If a version was provided, proceed as before.
        final_spec = required_spec
        pkg_name, pkg_version = final_spec.split('==', 1)
        bubble_dir_name = f'{pkg_name.lower().replace("-", "_")}-{pkg_version}'
        bubble_path = Path(config_manager.config['multiversion_base']) / bubble_dir_name
        if not bubble_path.is_dir():
            print(_("💡 Missing bubble detected: {}").format(final_spec))
            print(_("🚀 Auto-installing bubble... (This may take a moment)"))
            if omnipkg_instance.smart_install([final_spec]) != 0:
                print(_("\n❌ Auto-install failed for {}.").format(final_spec))
                return 1, None
            print(_("\n✅ Bubble installed successfully: {}").format(final_spec))

    # At this point, `final_spec` is guaranteed to be in 'package==version' format.
    print(_("✅ Using bubble for: {}").format(final_spec))
    return run_with_healing_wrapper(final_spec, original_script_path, original_script_args, config_manager)
    
def execute_run_command(cmd_args: list, config_manager: ConfigManager):
    """
    Handles the 'omnipkg run' command by creating a sterile copy of the script,
    then proceeding with execution and auto-healing flow.
    """
    if not cmd_args:
        print(_('❌ Error: No script specified to run.'))
        return 1
    
    # This is the user's original script path. We MUST keep track of it.
    source_script_path = Path(cmd_args[0]).resolve()
    script_args = cmd_args[1:]
    
    if not source_script_path.exists():
        print(_("❌ Error: Script not found at '{}'").format(source_script_path))
        return 1
    
    temp_script_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as temp_script:
            temp_script_path = Path(temp_script.name)
            temp_script.write(source_script_path.read_text(encoding='utf-8'))
        
        # These are the arguments for EXECUTION, using the temp script
        safe_cmd_args = [str(temp_script_path)] + script_args
        print(_(" syncing omnipkg context..."))
        sync_context_to_runtime()
        print(_("✅ Context synchronized."))
        
        python_exe = config_manager.config.get('python_executable', sys.executable)
        print(_("🚀 Attempting to run script with uv, forcing use of current environment..."))
        initial_cmd = ['uv', 'run', '--no-project', '--python', python_exe, '--'] + safe_cmd_args
        start_time_ns = time.perf_counter_ns()
        
        # First attempt: Try with output capture for error detection
        process = subprocess.Popen(
            initial_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            stdin=subprocess.PIPE,  # Changed to PIPE initially
            text=True, 
            encoding='utf-8', 
            cwd=Path.cwd(), 
            bufsize=0,  # Unbuffered for real-time output
            universal_newlines=True
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
                        print(remaining, end='', flush=True)
                        output_lines.append(remaining)
                    break
                
                # Use select to check if there's data available (Unix-like systems)
                if hasattr(select, 'select'):
                    ready, _unused1, _unused2 = select.select([process.stdout], [], [], 0.1)
                    if ready:
                        line = process.stdout.readline()
                        if line:
                            print(line, end='', flush=True)
                            output_lines.append(line)
                            
                            # Detect interactive prompts
                            if any(prompt in line.lower() for prompt in [
                                'enter your choice:', 'please choose', 'select an option',
                                'type a number:', 'input:', '(y/n)', 'continue?'
                            ]):
                                interactive_detected = True
                                break
                        else:
                            # EOF reached
                            break
                    else:
                        # No data available, small delay
                        time.sleep(0.01)
                else:
                    # Fallback for systems without select
                    try:
                        line = process.stdout.readline()
                        if line:
                            print(line, end='', flush=True)
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
                print(_("\n🎮 Interactive script detected! Switching to direct mode..."))
                # Terminate the captured process
                process.terminate()
                process.wait()
                
                # Re-run with direct stdin/stdout for interactive mode
                print(_("🚀 Re-launching script with full interactive support..."))
                direct_process = subprocess.Popen(
                    initial_cmd,
                    stdin=sys.stdin,
                    stdout=sys.stdout, 
                    stderr=sys.stderr,
                    cwd=Path.cwd()
                )
                
                try:
                    return_code = direct_process.wait()
                    end_time_ns = time.perf_counter_ns()
                    _initial_run_time_ns = end_time_ns - start_time_ns # CORRECTED
                    
                    if return_code == 0:
                        print("\n✅ Interactive script completed successfully via uv.")
                        print("⏱️  UV run completed in: {:.3f} ms ({:,} ns)".format(
                            _initial_run_time_ns / 1_000_000, _initial_run_time_ns)) # CORRECTED
                        return 0
                    else:
                        print(f"\n❌ Interactive script exited with code: {return_code}")
                        return return_code
                        
                except KeyboardInterrupt:
                    print("\n🛑 Process interrupted by user")
                    direct_process.terminate()
                    direct_process.wait()
                    return 130
            else:
                # Non-interactive, continue with normal flow
                return_code = process.wait()
                end_time_ns = time.perf_counter_ns()
                full_output = "".join(output_lines)
                _initial_run_time_ns = end_time_ns - start_time_ns # CORRECTED
                
        except KeyboardInterrupt:
            print("\n🛑 Process interrupted by user")
            process.terminate()
            process.wait()
            return 130
        
        # Check for healable errors
        has_healable_error = any(re.search(pattern, full_output, re.MULTILINE) for pattern in [
            r"A module that was compiled using NumPy 1\.x cannot be run in[\s\S]*?NumPy 2\.0",
            r"numpy\.dtype size changed, may indicate binary incompatibility"
        ])
        
        if return_code == 0 and not has_healable_error:
            print("\n✅ Script executed successfully via uv.")
            print("⏱️  UV run completed in: {:.3f} ms ({:,} ns)".format(
                _initial_run_time_ns / 1_000_000, _initial_run_time_ns)) # CORRECTED
            return 0
        
        if return_code == 0:
            print("\n🔍 UV succeeded but detected healable errors in output...")
        else:
            print("⏱️  UV run failed in: {:.3f} ms ({:,} ns)".format(
                _initial_run_time_ns / 1_000_000, _initial_run_time_ns)) # CORRECTED
        
        # CHANGED: Pass the safe_cmd_args for re-execution, but the SOURCE script path for analysis.
        exit_code, heal_stats = analyze_runtime_failure_and_heal(
            full_output, safe_cmd_args, source_script_path, config_manager, is_context_aware_run=False)
        
        if heal_stats:
            _print_performance_comparison(_initial_run_time_ns, heal_stats) # CORRECTED
        
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

        print("\n" + "="*70)
        print("🚀 PERFORMANCE COMPARISON: UV vs OMNIPKG")
        print("="*70)
        print(f"UV Failed Run:      {uv_failure_time_ms:>8.3f} ms  ({initial_ns:>12,} ns)")
        print(f"omnipkg Execution:  {execution_time_ms:>8.3f} ms  ({heal_stats['total_swap_time_ns']:>12,} ns)")
        print("-" * 70)
        
    else:
        # Original bubble swapping performance comparison
        omnipkg_time_ms = heal_stats['total_swap_time_ns'] / 1_000_000
        
        if omnipkg_time_ms <= 0:
            return

        speed_ratio = uv_failure_time_ms / omnipkg_time_ms
        speed_percentage = ((uv_failure_time_ms - omnipkg_time_ms) / omnipkg_time_ms) * 100

        print("\n" + "="*70)
        print("🚀 PERFORMANCE COMPARISON: UV vs OMNIPKG")
        print("="*70)
        print(f"UV Failed Run:      {uv_failure_time_ms:>8.3f} ms  ({initial_ns:>12,} ns)")
        print(f"omnipkg Healing:    {omnipkg_time_ms:>8.3f} ms  ({heal_stats['total_swap_time_ns']:>12,} ns)")
        print("-" * 70)
    
    # Common performance display logic
    if speed_ratio >= 1000:
        print(f"🎯 omnipkg is {speed_ratio:>6.0f}x FASTER than UV!")
    elif speed_ratio >= 100:
        print(f"🎯 omnipkg is {speed_ratio:>6.1f}x FASTER than UV!")
    else:
        print(f"🎯 omnipkg is {speed_ratio:>6.2f}x FASTER than UV!")
    
    if speed_percentage >= 10000:
        print(f"💥 That's {speed_percentage:>8.0f}% improvement!")
    elif speed_percentage >= 1000:
        print(f"💥 That's {speed_percentage:>8.1f}% improvement!")
    else:
        print(f"💥 That's {speed_percentage:>8.2f}% improvement!")
    
    print("="*70)
    print("🌟 Same environment, zero downtime, microsecond swapping!")
    print("="*70 + "\n")
    
def run_with_healing_wrapper(required_spec, original_script_path, original_script_args, config_manager):
    """
    Generates and executes the temporary wrapper script. This version creates a
    robust sys.path in the subprocess, enabling it to find both the omnipkg
    source and its installed dependencies like 'packaging'.
    """
    # PRE-DEBUG: Check what we have available in the current process
    import site
    import importlib.util
    
    print("\n🔍 PRE-WRAPPER DEBUGGING:")
    print(f"   Current Python executable: {sys.executable}")
    print(f"   Current working directory: {os.getcwd()}")
    print(f"   Project root: {project_root}")
    
    # Check if packaging is available in current process
    try:
        import packaging
        print(f"   ✅ packaging found at: {packaging.__file__}")
    except ImportError as e:
        print(f"   ❌ packaging not available in current process: {e}")
    
    # Get all possible site-packages paths
    site_packages_paths = []
    
    # From config
    config_site_packages = config_manager.config.get('site_packages_path')
    if config_site_packages:
        site_packages_paths.append(config_site_packages)
        print(f"   Config site-packages: {config_site_packages}")
    
    # From site module
    for path in site.getsitepackages():
        if path not in site_packages_paths:
            site_packages_paths.append(path)
            print(f"   Site getsitepackages: {path}")
    
    # From site.USER_SITE
    if hasattr(site, 'USER_SITE') and site.USER_SITE:
        if site.USER_SITE not in site_packages_paths:
            site_packages_paths.append(site.USER_SITE)
            print(f"   Site USER_SITE: {site.USER_SITE}")
    
    # Check current sys.path for site-packages
    for path in sys.path:
        if 'site-packages' in path and path not in site_packages_paths:
            site_packages_paths.append(path)
            print(f"   Current sys.path site-packages: {path}")
    
    # Check each site-packages path for packaging module
    packaging_locations = []
    for sp_path in site_packages_paths:
        if os.path.exists(sp_path):
            packaging_path = os.path.join(sp_path, 'packaging')
            packaging_init = os.path.join(sp_path, 'packaging', '__init__.py')
            if os.path.exists(packaging_path) and os.path.exists(packaging_init):
                packaging_locations.append(sp_path)
                print(f"   📦 packaging found in: {sp_path}")
        else:
            print(f"   ❌ site-packages path doesn't exist: {sp_path}")
    
    if not packaging_locations:
        print("   ⚠️  WARNING: No packaging module found in any site-packages!")
    
    # Use the first site-packages path that has packaging, or fallback to config
    site_packages_path = packaging_locations[0] if packaging_locations else config_site_packages
    if not site_packages_path and site_packages_paths:
        site_packages_path = site_packages_paths[0]
    
    print(f"   🎯 Selected site-packages for wrapper: {site_packages_path}")

    # Enhanced wrapper content with comprehensive debugging
    wrapper_content = textwrap.dedent(f"""\
        import sys, os, runpy, json
        from pathlib import Path

        # DEBUGGING: Show initial state
        print("🔍 WRAPPER SUBPROCESS DEBUGGING:")
        print(f"   Python executable: {{sys.executable}}")
        print(f"   Initial sys.path length: {{len(sys.path)}}")
        print(f"   Working directory: {{os.getcwd()}}")
        
        # Show first few sys.path entries
        for i, path in enumerate(sys.path[:5]):
            print(f"   sys.path[{{i}}]: {{path}}")
        if len(sys.path) > 5:
            print(f"   ... and {{len(sys.path) - 5}} more entries")

        # --- COMPLETE SYS.PATH INJECTION ---
        project_root_path = r"{project_root}"
        main_site_packages = r"{site_packages_path}"
        
        print(f"\\n   🔧 Adding project root: {{project_root_path}}")
        if project_root_path not in sys.path:
            sys.path.insert(0, project_root_path)
            print(f"      ✅ Added to sys.path[0]")
        else:
            print(f"      ⚠️  Already in sys.path")

        print(f"   🔧 Adding site-packages: {{main_site_packages}}")
        if main_site_packages and main_site_packages not in sys.path:
            sys.path.insert(1, main_site_packages)
            print(f"      ✅ Added to sys.path[1]")
        else:
            print(f"      ⚠️  Already in sys.path or None")
        
        # Add all potential site-packages paths
        additional_paths = {site_packages_paths!r}
        print(f"   🔧 Adding {{len(additional_paths)}} additional paths...")
        for add_path in additional_paths:
            if add_path and os.path.exists(add_path) and add_path not in sys.path:
                sys.path.append(add_path)
                print(f"      ✅ Added: {{add_path}}")

        print(f"\\n   📊 Final sys.path length: {{len(sys.path)}}")
        
        # Test critical imports before proceeding
        print("\\n   🧪 Testing critical imports...")
        
        # Test packaging import
        try:
            import packaging
            print(f"      ✅ packaging: {{packaging.__file__}}")
        except ImportError as e:
            print(f"      ❌ packaging failed: {{e}}")
            print("      🔍 Searching for packaging in sys.path...")
            for i, path in enumerate(sys.path):
                packaging_path = os.path.join(path, 'packaging')
                if os.path.exists(packaging_path):
                    print(f"         Found packaging dir in sys.path[{{i}}]: {{path}}")
                    init_file = os.path.join(packaging_path, '__init__.py')
                    print(f"         __init__.py exists: {{os.path.exists(init_file)}}")
        
        # Test omnipkg imports
        try:
            from omnipkg.loader import omnipkgLoader
            print(f"      ✅ omnipkgLoader imported")
        except ImportError as e:
            print(f"      ❌ omnipkgLoader failed: {{e}}")
            
        try:
            from omnipkg.i18n import _
            print(f"      ✅ omnipkg.i18n imported")
        except ImportError as e:
            print(f"      ❌ omnipkg.i18n failed: {{e}}")
        # --- END OF PATH INJECTION ---

        # With a correct path, these imports will now succeed.
        try:
            from omnipkg.loader import omnipkgLoader
            from omnipkg.i18n import _
        except ImportError as e:
            # This is a fallback error for debugging if the path injection fails.
            print(f"\\nFATAL: Could not import omnipkg modules after path setup. Error: {{e}}")
            print(f"\\nDEBUG: Final sys.path ({{len(sys.path)}} entries):")
            for i, path in enumerate(sys.path):
                exists = "✅" if os.path.exists(path) else "❌"
                print(f"   [{{i:2d}}] {{exists}} {{path}}")
            
            # Check for omnipkg specifically
            print(f"\\nDEBUG: Checking for omnipkg module...")
            for i, path in enumerate(sys.path):
                omnipkg_path = os.path.join(path, 'omnipkg')
                if os.path.exists(omnipkg_path):
                    print(f"   Found omnipkg dir in sys.path[{{i}}]: {{path}}")
                    loader_file = os.path.join(omnipkg_path, 'loader.py')
                    print(f"   loader.py exists: {{os.path.exists(loader_file)}}")
            sys.exit(1)

        lang_from_env = os.environ.get('OMNIPKG_LANG')
        if lang_from_env: _.set_language(lang_from_env)

        config = json.loads(r'''{json.dumps(config_manager.config)}''')
        package_spec = "{required_spec}"
        loader_instance = None
        try:
            print(f"\\n🌀 omnipkg auto-heal: Wrapping script with loader for '{{package_spec}}'...")
            print('-' * 60)
            with omnipkgLoader(package_spec, config=config) as loader:
                loader_instance = loader
                
                # NEW: INJECT THE LOCAL PROJECT PATH INTO THE BUBBLE'S ENVIRONMENT
                local_project_path = r"{str(original_script_path.parent)}"
                if local_project_path not in sys.path:
                    sys.path.insert(0, local_project_path)
            
                print(f"\\n🚀 Running target script inside the combined bubble + local context...")
                sys.argv = [{str(original_script_path)!r}] + {original_script_args!r}
                runpy.run_path({str(original_script_path)!r}, run_name="__main__")
                
                # Debug sys.path after bubble activation
                print(f"\\n🔍 DEBUG: sys.path after bubble activation ({{len(sys.path)}} entries):")
                for i, path in enumerate(sys.path[:8]):  # Show first 8 entries
                    exists = "✅" if os.path.exists(path) else "❌"
                    print(f"   [{{i}}] {{exists}} {{path}}")
                if len(sys.path) > 8:
                    print(f"   ... and {{len(sys.path) - 8}} more entries")
                
                # Test critical imports inside bubble
                print(f"\\n🧪 Testing imports inside bubble...")
                for module_name in ['packaging', 'filelock', 'toml']:
                    try:
                        module = __import__(module_name)
                        print(f"      ✅ {{module_name}}: {{module.__file__}}")
                    except ImportError as e:
                        print(f"      ❌ {{module_name}} failed: {{e}}")
                        # Try to find it in current sys.path
                        for i, path in enumerate(sys.path):
                            if os.path.exists(os.path.join(path, module_name)):
                                print(f"         Found {{module_name}} in sys.path[{{i}}]: {{path}}")
                                break
                            elif os.path.exists(os.path.join(path, f"{{module_name}}.py")):
                                print(f"         Found {{module_name}}.py in sys.path[{{i}}]: {{path}}")
                                break
                
                print(f"\\n🚀 Running target script...")
                sys.argv = [{str(original_script_path)!r}] + {original_script_args!r}
                runpy.run_path({str(original_script_path)!r}, run_name="__main__")
            print('-' * 60)
            print(_("✅ Script completed successfully inside omnipkg bubble."))
        except Exception:
            import traceback
            traceback.print_exc()
            sys.exit(1)
        finally:
            if loader_instance:
                stats = loader_instance.get_performance_stats()
                if stats:
                    print(f"OMNIPKG_STATS_JSON:{{json.dumps(stats)}}", flush=True)
    """)

    temp_script_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(wrapper_content)
            temp_script_path = f.name

        print(f"   💾 Temporary wrapper script: {temp_script_path}")

        heal_command = [config_manager.config.get('python_executable', sys.executable), temp_script_path]
        print(_("\n🚀 Re-running with omnipkg auto-heal..."))

        process = subprocess.Popen(heal_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')

        output_lines = []
        for line in iter(process.stdout.readline, ''):
            if not line.startswith("OMNIPKG_STATS_JSON:"):
                print(line, end='')
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
