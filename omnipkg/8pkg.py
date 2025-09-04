#!/usr/bin/env python3
"""
8pkg - The infinity package manager (alias for omnipkg)
Because 8 sideways = ∞ and we handle infinite package versions!
"""
import sys
from pathlib import Path

# Add the omnipkg module to path if needed
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

# Import and run the main CLI
from cli import main

if __name__ == '__main__':
    sys.exit(main())