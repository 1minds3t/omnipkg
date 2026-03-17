from __future__ import annotations
from omnipkg.common_utils import safe_print
try:
    from .common_utils import safe_print
except ImportError:
    pass

import sys
import os

from .cli import main
from .core import ConfigManager
from .i18n import _

# Initialize the config manager
config_manager = ConfigManager()

# Language priority: OMNIPKG_LANG env var > config file > default (en)
language = os.environ.get("OMNIPKG_LANG") or config_manager.config.get("language", "en")

# Set the language in the translator
_.set_language(language)

# ⚠️ CRITICAL: Set in os.environ so subprocesses inherit it!
os.environ["OMNIPKG_LANG"] = language  # ← ADD THIS LINE IF MISSING!

# This runs the main function and ensures the script exits with the correct status code.
if __name__ == "__main__":
    sys.exit(main())