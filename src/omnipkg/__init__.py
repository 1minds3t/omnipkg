from __future__ import annotations  # Python 3.6+ compatibility

from omnipkg.common_utils import safe_print

try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
# In /home/minds3t/omnipkg/omnipkg/__init__.py

from .i18n import _

"""
omnipkg: Universal package manager

Copyright (c) 2025  1minds3t

This file is part of `omnipkg`.

omnipkg is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

omnipkg is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the License for more details.

You should have received a copy of the GNU Affero General Public License
along with omnipkg. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing options or general inquiries, contact:
📧 omnipkg@proton.me
"""
import sys
from pathlib import Path

try:
    # Prefer importlib.metadata (works in installed packages)
    from importlib.metadata import PackageNotFoundError, metadata, version
except ImportError:  # Python < 3.8 fallback
    from importlib_metadata import PackageNotFoundError, metadata, version

# --- THIS IS THE FIX ---
# This block makes the code compatible with both modern and older Python.
# On Python >= 3.11, it will use the built-in `tomllib`.
# On Python < 3.11, it will use the `tomli` package installed from your pyproject.toml.
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ImportError:
        # If neither is available, create a dummy that will fail gracefully
        tomllib = None

__version__ = "0.0.0"  # fallback default
__dependencies__ = {}

_pkg_name = "omnipkg"

try:
    __version__ = version(_pkg_name)
    pkg_meta = metadata(_pkg_name)
    requires = pkg_meta.get_all("Requires-Dist") or []
    __dependencies__ = {dep.split()[0]: dep for dep in requires}
except PackageNotFoundError:
    # Likely running from source → try pyproject.toml
    if tomllib is not None:  # Only try if we have a TOML parser
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        if pyproject_path.exists():
            with pyproject_path.open("rb") as f:
                pyproject_data = tomllib.load(f)
            __version__ = pyproject_data["project"]["version"]
            __dependencies__ = {
                dep.split()[0]: dep for dep in pyproject_data["project"].get("dependencies", [])
            }

__all__ = [
    "core",
    "cli",
    "loader",
    "activator",
    "demo",
    "package_meta_builder",
    "stress_test",
    "common_utils",
]

# Vendor patched filelock for Python <3.10 (CVE-2025-68146)
import sys
if sys.version_info < (3, 10):
    from omnipkg._vendor import filelock
    sys.modules['filelock'] = filelock
