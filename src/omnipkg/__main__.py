from __future__ import annotations

from omnipkg.common_utils import safe_print

try:
    from .common_utils import safe_print
except ImportError:
    pass

import sys

from .cli import main
from .core import ConfigManager  # ← Import from core, not config_manager
from .i18n import _  # ← Import the translator instance, not setup_i18n

# Initialize the config manager
config_manager = ConfigManager()

# Set the language from config
# The _ translator has a set_language method
_.set_language(config_manager.config.get("language", "en"))

# This runs the main function and ensures the script exits with the correct status code.
if __name__ == "__main__":
    sys.exit(main())