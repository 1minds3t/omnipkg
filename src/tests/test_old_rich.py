# tests/test_old_rich.py (Corrected)

try:
    # This is your project's safe_print for standard, unstyled output
    from omnipkg.common_utils import safe_print
except ImportError:
    # Fallback for different execution contexts
    from omnipkg.common_utils import safe_print

import rich
import importlib.metadata
from omnipkg.i18n import _
# This is the correct way to print styled text with the rich library
from rich import print as rich_print

# --- Script Logic ---

try:
    rich_version = rich.__version__
except AttributeError:
    rich_version = importlib.metadata.version('rich')

# Assert that the correct (older) version is active
assert rich_version == '13.4.2', _('Incorrect rich version! Expected 13.4.2, got {}').format(rich_version)

# Use YOUR safe_print for simple logging
safe_print(_('✅ Successfully imported rich version: {}').format(rich_version))

# Use the IMPORTED rich_print for styled output
rich_print('[bold green]This script is running with the correct, older version of rich![/bold green]')