from __future__ import annotations

import subprocess
import re
import requests
from datetime import datetime
from packaging.version import parse as parse_version
import json
import os
import shutil
import tempfile
import sys
from omnipkg.i18n import _
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print


def get_release_date(package_name, version):
    """
    Uses the working curl|jq command to get the release date.
    """
    command = _('curl -s "https://pypi.org/pypi/{}/json" | jq -r \'.releases."{}"[0].upload_time_iso_8601\'').format(package_name, version)
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(_('Error fetching release date: {}').format(e.stderr))
        return None

def get_dependency_names_from_real_install(package_name, version):
    """
    Create a temp venv, install package, freeze deps, then remove venv.
    Returns clean dependency names without versions.
    """
    print(_('Automating real install in a temporary environment...'))
    temp_dir = tempfile.mkdtemp()
    venv_path = os.path.join(temp_dir, 'venv')
    venv_python = os.path.join(venv_path, 'bin', 'python')
    try:
        subprocess.run(['python3', '-m', 'venv', venv_path], check=True)
        subprocess.run([venv_python, '-m', 'pip', 'install', f'{package_name}=={version}'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        freeze_result = subprocess.run([venv_python, '-m', 'pip', 'freeze'], check=True, capture_output=True, text=True)
        dep_names = []
        for line in freeze_result.stdout.splitlines():
            name = line.split('==')[0].strip()
            if name.lower() != package_name.lower():
                dep_names.append(name)
        print('Success! Got dependencies from a real install.')
        return dep_names
    except subprocess.CalledProcessError as e:
        print(_('Error during real install automation: {}').format(e))
        return []
    finally:
        shutil.rmtree(temp_dir)
        print(_('Cleaned up temporary environment.'))

def find_historical_versions(dependencies, cutoff_date):
    """
    Finds the latest version for each dependency before a given date using PyPI API.
    """
    pypi_url = 'https://pypi.org/pypi/'
    historical_versions = {}
    try:
        cutoff_datetime = datetime.fromisoformat(cutoff_date.replace('Z', '+00:00'))
    except ValueError:
        print(_('Error: Invalid date format: {}').format(cutoff_date))
        return {}
    print(_('\nFinding historical versions before: {}').format(cutoff_datetime))
    for dep_name in dependencies:
        print(_('  Processing {}...').format(dep_name))
        try:
            dep_url = f'{pypi_url}{dep_name}/json'
            response = requests.get(dep_url, timeout=10)
            response.raise_for_status()
            dep_data = response.json()
            latest_valid_version = None
            latest_valid_datetime = None
            for dep_version, releases in dep_data.get('releases', {}).items():
                if not releases:
                    continue
                for release in releases:
                    upload_time_str = release.get('upload_time_iso_8601')
                    if not upload_time_str:
                        continue
                    try:
                        upload_time = datetime.fromisoformat(upload_time_str.replace('Z', '+00:00'))
                    except ValueError:
                        continue
                    if upload_time <= cutoff_datetime:
                        if latest_valid_version is None or parse_version(dep_version) > parse_version(latest_valid_version):
                            latest_valid_version = dep_version
                            latest_valid_datetime = upload_time
            if latest_valid_version:
                historical_versions[dep_name] = latest_valid_version
                safe_print(_('    ✓ Found {}=={} (released {})').format(dep_name, latest_valid_version, latest_valid_datetime))
            else:
                safe_print(f"    ✗ No compatible version found for '{dep_name}' before {cutoff_date}")
        except requests.exceptions.RequestException as e:
            safe_print(f'    ✗ Error fetching metadata for {dep_name}: {e}')
        except Exception as e:
            safe_print(_('    ✗ Unexpected error processing {}: {}').format(dep_name, e))
    return historical_versions

def find_compatible_versions(historical_versions, target_pkg, target_ver, cutoff_date):
    """
    Find newer compatible versions for packages that fail to build with historical versions.
    """
    safe_print(_('\n🔧 Finding build-compatible versions...'))
    compatibility_overrides = {'MarkupSafe': '1.1.1', 'Jinja2': '2.11.3', 'itsdangerous': '1.1.0'}
    updated_versions = {}
    for pkg_name, historical_version in historical_versions.items():
        if pkg_name in compatibility_overrides:
            new_version = compatibility_overrides[pkg_name]
            safe_print(_('  🔄 {}: {} → {} (compatibility fix)').format(pkg_name, historical_version, new_version))
            updated_versions[pkg_name] = new_version
        else:
            updated_versions[pkg_name] = historical_version
    return updated_versions

def execute_historical_install(target_pkg, target_ver, historical_versions, auto_install=False):
    """
    Execute the actual installation of the target package with historical dependencies.
    """
    print('\n' + '=' * 80)
    safe_print(_('📋 INITIAL DEPENDENCY RESOLUTION:'))
    print('=' * 80)
    for pkg, ver in historical_versions.items():
        print(_('  {}=={}').format(pkg, ver))
    print(_('  {}=={}').format(target_pkg, target_ver))
    compatible_versions = find_compatible_versions(historical_versions, target_pkg, target_ver, None)
    install_packages = []
    for pkg_name, version in compatible_versions.items():
        install_packages.append(f'"{pkg_name}=={version}"')
    install_packages.append(f'"{target_pkg}=={target_ver}"')
    install_command = ['pip', 'install'] + [pkg.strip('"') for pkg in install_packages]
    command_str = 'pip install ' + ' '.join(install_packages)
    safe_print('\n🚀 FINAL INSTALL COMMAND (with compatibility fixes):')
    print('-' * 80)
    print(command_str)
    print('-' * 80)
    if not auto_install:
        safe_print(_('\n❓ Do you want to execute this installation? (y/N): '), end='')
        response = input().strip().lower()
        if response not in ['y', 'yes']:
            safe_print(_('❌ Installation cancelled by user.'))
            return False
    safe_print(_('\n🔧 Executing installation...'))
    print('=' * 80)
    strategies = [('Standard installation', install_command), ('With --no-build-isolation', install_command + ['--no-build-isolation']), ('Force reinstall', install_command + ['--force-reinstall', '--no-deps'])]
    for strategy_name, cmd in strategies:
        safe_print(_('\n🎯 Trying: {}').format(strategy_name))
        print('-' * 40)
        try:
            result = subprocess.run(cmd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, universal_newlines=True)
            print(result.stdout)
            print('=' * 80)
            safe_print(_('✅ INSTALLATION COMPLETED SUCCESSFULLY! (using {})').format(strategy_name))
            print('=' * 80)
            verify_installation(target_pkg, target_ver, compatible_versions)
            return True
        except subprocess.CalledProcessError as e:
            safe_print(_('❌ {} failed:').format(strategy_name))
            if hasattr(e, 'stdout') and e.stdout:
                error_lines = e.stdout.strip().split('\n')
                for line in error_lines[-10:]:
                    print(_('  {}').format(line))
            print()
            continue
    print('=' * 80)
    safe_print(_('❌ ALL INSTALLATION STRATEGIES FAILED!'))
    print('=' * 80)
    safe_print(_('💡 Suggestions:'))
    print(_('  1. Try installing in a fresh Python 3.7 or 3.8 environment'))
    print(_("  2. Use older setuptools: pip install 'setuptools<58'"))
    print('  3. Install packages individually with --no-deps')
    return False

def verify_installation(target_pkg, target_ver, historical_versions):
    """
    Verify that the packages were installed with the correct versions.
    """
    safe_print(_('\n🔍 Verifying installation...'))
    print('-' * 50)
    check_package_version(target_pkg, target_ver)
    for pkg_name, expected_version in historical_versions.items():
        check_package_version(pkg_name, expected_version)
    print('-' * 50)

def check_package_version(package_name, expected_version):
    """
    Check if a specific package is installed with the expected version.
    """
    try:
        result = subprocess.run(['pip', 'show', package_name], check=True, capture_output=True, text=True)
        for line in result.stdout.split('\n'):
            if line.startswith('Version:'):
                installed_version = line.split(':', 1)[1].strip()
                if installed_version == expected_version:
                    safe_print(_('  ✓ {}=={} (correct)').format(package_name, installed_version))
                else:
                    safe_print(_('  ⚠️  {}=={} (expected {})').format(package_name, installed_version, expected_version))
                return
        safe_print(_('  ❓ {}: Could not determine version').format(package_name))
    except subprocess.CalledProcessError:
        safe_print(_('  ❌ {}: Not found or not installed').format(package_name))

def main():
    if len(sys.argv) < 2:
        print("Usage: python deptimemachine.py <package>==<version> [--auto-install]")
        return 1
    
    pkg_spec = sys.argv[1]
    if '==' not in pkg_spec:
        print("Error: Please provide a package with a specific version (e.g., 'flask-login==0.4.1')")
        return 1

    target_pkg, target_ver = pkg_spec.split('==', 1)
    auto_install = '--auto-install' in sys.argv
    target_pkg = 'flask-login'
    target_ver = '0.4.1'
    auto_install = False
    safe_print(_('🎯 Target: {}=={}').format(target_pkg, target_ver))
    safe_print(f'\n📅 Step 1: Getting release date for {target_pkg}=={target_ver}')
    release_date = get_release_date(target_pkg, target_ver)
    if not release_date:
        safe_print(_('❌ Failed to get release date. Exiting.'))
        return 1
    safe_print(_('✓ Successfully retrieved release date: {}').format(release_date))
    safe_print(_('\n📦 Step 2: Getting actual dependencies via real install'))
    dep_names = get_dependency_names_from_real_install(target_pkg, target_ver)
    if not dep_names:
        safe_print(_('ℹ️ No dependencies found to process.'))
        final_command = _('pip install "{}=={}"').format(target_pkg, target_ver)
        safe_print(_('\n🚀 Install command:\n{}').format(final_command))
        if not auto_install:
            safe_print(_('\n❓ Do you want to install the target package? (y/N): '), end='')
            response = input().strip().lower()
            if response not in ['y', 'yes']:
                safe_print(_('❌ Installation cancelled by user.'))
                return 0
        try:
            subprocess.run(['pip', 'install', f'{target_pkg}=={target_ver}'], check=True)
            safe_print(_('✅ Target package installed successfully!'))
        except subprocess.CalledProcessError as e:
            safe_print(_('❌ Failed to install target package: {}').format(e))
            return 1
        return 0
    safe_print(_('✓ Discovered dependencies: {}').format(dep_names))
    safe_print(_('\n🔍 Step 3: Finding historical versions'))
    historical_versions = find_historical_versions(dep_names, release_date)
    if historical_versions:
        success = execute_historical_install(target_pkg, target_ver, historical_versions, auto_install)
        if success:
            safe_print(_('\n🎉 Complete! {}=={} and all historical dependencies are now installed.').format(target_pkg, target_ver))
            safe_print(_('📦 The packages are installed in your current Python environment and can be found'))
            print(_('   in your site-packages directory, accessible to your Python interpreter.'))
            return 0
        else:
            return 1
    else:
        safe_print(_('\n❌ Could not resolve any dependencies. Exiting.'))
        return 1
if __name__ == '__main__':
    try:
        exit(main())
    except KeyboardInterrupt:
        safe_print(_('\n\n⏹️  Installation interrupted by user.'))
        exit(1)
    except Exception as e:
        safe_print(_('\n💥 Unexpected error: {}').format(e))
        exit(1)