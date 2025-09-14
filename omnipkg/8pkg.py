#!/usr/bin/env python3
"""
8pkg - The infinity package manager (alias for omnipkg)
Because 8 sideways = ∞ and we handle infinite package versions!
"""
import sys
from pathlib import Path

# FIX: Add the project's root directory to the path, not the current directory.
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# The import should now work correctly because the top-level package is on the path.
from omnipkg.cli import main

if __name__ == '__main_':
    sys.exit(main())
