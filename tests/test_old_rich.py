# test_old_rich.py
import rich
import importlib.metadata

# Get the rich version using the modern approach
try:
    rich_version = rich.__version__
except AttributeError:
    # Fallback to importlib.metadata if __version__ is not available
    rich_version = importlib.metadata.version('rich')

# This assertion will fail if the environment has rich 13.7.1
# but will pass inside the bubble for 13.4.2.
assert rich_version == '13.4.2', f"Incorrect rich version! Expected 13.4.2, got {rich_version}"
print(f"✅ Successfully imported rich version: {rich_version}")
rich.print("[bold green]This script is running with the correct, older version of rich![/bold green]")