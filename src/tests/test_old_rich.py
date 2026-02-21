# src/tests/test_old_rich.py
"""
DEMO FILE — intentionally fails during normal collection unless run via omnipkg auto-heal.
Shows version conflict + healing in action.
"""

import sys

# Bail out immediately and silently if not running under pytest
# (prevents crash when someone does plain `python src/tests/test_old_rich.py` or no [dev])
try:
    import pytest
except ImportError:
    sys.exit(0)

# ────────────────────────────────────────────────
# NOW safe: pytest exists → we can skip the module cleanly
# This runs VERY early — before any rich/omnipkg imports or asserts
# ────────────────────────────────────────────────

if "omnipkg" not in sys.modules:
    pytest.skip(
        "This is an intentional demo of omnipkg healing (rich version mismatch).\n"
        "Normal pytest collection fails here ON PURPOSE.\n"
        "Run with:  8pkg run src/tests/test_old_rich.py   (or omnipkg run ...)\n"
        "to activate bubble → heal rich → see green success.",
        allow_module_level=True
    )

# ────────────────────────────────────────────────
# Only reached when omnipkg loader is active → proceed with intentional check
# ────────────────────────────────────────────────

import rich
import importlib.metadata
from omnipkg.i18n import _
from rich import print as rich_print
from omnipkg.common_utils import safe_print

try:
    rich_version = rich.__version__
except AttributeError:
    rich_version = importlib.metadata.version("rich")

assert rich_version == "13.4.2", _(
    "Incorrect rich version! Expected 13.4.2, got {}"
).format(rich_version)

safe_print(_("✅ Successfully imported rich version: {}").format(rich_version))

rich_print(
    "[bold green]This script is running with the correct, older version of rich![/bold green]"
)

# Optional: minimal test item so file doesn't look completely empty when collected
def test_omnipkg_healing_demo():
    assert True  # placeholder