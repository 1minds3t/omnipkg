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
from pathlib import Path
import sys

try:
    # Prefer importlib.metadata (works in installed packages)
    from importlib.metadata import version, metadata, PackageNotFoundError
except ImportError:  # Python < 3.8 fallback
    from importlib_metadata import version, metadata, PackageNotFoundError

import tomli

__version__ = "0.0.0"   # fallback default
__dependencies__ = {}

_pkg_name = "omnipkg"

try:
    __version__ = version(_pkg_name)
    pkg_meta = metadata(_pkg_name)
    requires = pkg_meta.get_all("Requires-Dist") or []
    __dependencies__ = {dep.split()[0]: dep for dep in requires}
except PackageNotFoundError:
    # Likely running from source → try pyproject.toml
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    if pyproject_path.exists():
        with pyproject_path.open("rb") as f:
            pyproject_data = tomli.load(f)
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

# Optional: quick import shortcuts
from .core import *
from .cli import *
from .loader import *
