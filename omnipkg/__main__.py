import sys
from .cli import main
from .config_manager import ConfigManager
from .i18n import setup_i18n
from omnipkg.i18n import _

# Initialize the config manager
config_manager = ConfigManager()

# Use the language from the config to set up i18n
# This is the crucial step. It must be done before anything else is printed.
_ = setup_i18n(config_manager.get('language', 'en'))

# This runs the main function and ensures the script exits with the correct status code.
if __name__ == "__main__":
    sys.exit(main())

