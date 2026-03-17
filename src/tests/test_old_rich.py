# src/tests/test_old_rich.py
"""
DEMO FILE — run via: 8pkg run src/tests/test_old_rich.py
Shows omnipkg auto-healing a rich version conflict.
"""
import sys
import importlib.metadata
import rich

try:
    from omnipkg.i18n import _
    from omnipkg.common_utils import safe_print
except ImportError:
    _ = lambda s: s
    safe_print = print

def _get_rich_version():
    try:
        return rich.__version__
    except AttributeError:
        return importlib.metadata.version("rich")

def test_omnipkg_healing_demo():
    """Run via: 8pkg run src/tests/test_old_rich.py"""
    rich_version = _get_rich_version()
    assert rich_version == "13.4.2", _(
        "Incorrect rich version! Expected 13.4.2, got {}"
    ).format(rich_version)
    safe_print(_("✅ Successfully imported rich version: {}").format(rich_version))
    from rich import print as rich_print
    rich_print("[bold green]Running with the correct older rich==13.4.2![/bold green]")

# Direct run via 8pkg run
if "pytest" not in sys.modules:
    rich_version = _get_rich_version()
    assert rich_version == "13.4.2", _(
        "Incorrect rich version! Expected 13.4.2, got {}"
    ).format(rich_version)
    safe_print(_("✅ Successfully imported rich version: {}").format(rich_version))
    from rich import print as rich_print
    rich_print("[bold green]Running with the correct older rich==13.4.2![/bold green]")