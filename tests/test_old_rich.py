import rich
from omnipkg.i18n import _
import importlib.metadata
try:
    rich_version = rich.__version__
except AttributeError:
    rich_version = importlib.metadata.version('rich')
assert rich_version == '13.4.2', _('Incorrect rich version! Expected 13.4.2, got {}').format(rich_version)
print(_('✅ Successfully imported rich version: {}').format(rich_version))
rich.print('[bold green]This script is running with the correct, older version of rich![/bold green]')