#!/usr/bin/env python
"""
Minimal setup.py bridge for Python 3.7 compatibility.
Python 3.7's pip doesn't support PEP 660 editable installs from pyproject.toml alone.
This file bridges to pyproject.toml for metadata while supporting legacy editable installs.
"""

from setuptools import setup

# All configuration is in pyproject.toml
# This file exists only for Python 3.7 pip compatibility
setup()