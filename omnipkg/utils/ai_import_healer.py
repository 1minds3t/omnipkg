#!/usr/bin/env python3
"""
AI Import Hallucination Healer
================================
Detects and removes the dumbest AI mistakes: placeholder imports.

This intercepts code before execution and removes lines like:
    from your_file_name import calculate
    from my_script import function
    from app import main
    
Because the AI is supposed to run everything in ONE FILE but keeps
hallucinating module imports like a drunk programmer.
"""

import re
import sys
from pathlib import Path
from typing import Tuple, List


class AIImportHealer:
    """Heals AI-generated code that hallucinates placeholder imports."""
    
    # Common placeholder names that AI models hallucinate
    PLACEHOLDER_PATTERNS = [
        r'your_file_name',
        r'your_module',
        r'my_script',
        r'my_module',
        r'your_file',
        r'main_file',
        r'app_file',
        r'module_name',
        r'file_name',
        r'script_name',
        r'code_file',
        r'test_file',
        r'example',
        r'calculator',  # specific to your case
        r'calc',
    ]
    
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.healed_count = 0
        self.removed_lines: List[str] = []
        
    def _build_pattern(self) -> re.Pattern:
        """Build regex pattern to match all placeholder imports."""
        # Match: from <placeholder> import ...
        placeholders = '|'.join(self.PLACEHOLDER_PATTERNS)
        pattern = rf'^\s*from\s+({placeholders})\s+import\s+.*$'
        return re.compile(pattern, re.MULTILINE | re.IGNORECASE)
    
    def _log(self, msg: str):
        """Log message if verbose mode is on."""
        if self.verbose:
            safe_print(f"🔧 {msg}", file=sys.stderr)
    
    def detect_hallucinated_imports(self, code: str) -> List[str]:
        """Find all hallucinated import lines."""
        pattern = self._build_pattern()
        matches = pattern.findall(code)
        return matches
    
    def heal(self, code: str) -> Tuple[str, bool]:
        """
        Remove hallucinated imports from code.
        
        Returns:
            (healed_code, was_healed) tuple
        """
        pattern = self._build_pattern()
        
        # Find all matches first for logging
        matches = list(pattern.finditer(code))
        
        if not matches:
            return code, False
        
        # Log what we're removing
        self._log("🚨 DETECTED AI HALLUCINATION!")
        for match in matches:
            line = match.group(0).strip()
            self.removed_lines.append(line)
            self._log(f"   Removing: {line}")
        
        # Remove the imports
        healed_code = pattern.sub('', code)
        self.healed_count += len(matches)
        
        self._log(f"✅ Healed {len(matches)} hallucinated import(s)")
        
        return healed_code, True
    
    def heal_file(self, filepath: Path) -> bool:
        """
        Heal a file in-place.
        
        Returns:
            True if file was modified, False otherwise
        """
        self._log(f"📄 Scanning: {filepath}")
        
        code = filepath.read_text()
        healed_code, was_healed = self.heal(code)
        
        if was_healed:
            filepath.write_text(healed_code)
            self._log(f"💾 Saved healed code to: {filepath}")
            return True
        else:
            self._log("✨ No hallucinations detected")
            return False
    
    def get_report(self) -> str:
        """Get a summary report of healing operations."""
        if self.healed_count == 0:
            return "✅ No AI hallucinations detected"
        
        report = f"🔧 AI Import Healer Report\n"
        report += f"{'=' * 50}\n"
        report += f"Total hallucinations healed: {self.healed_count}\n"
        report += f"\nRemoved lines:\n"
        for line in self.removed_lines:
            report += f"  ❌ {line}\n"
        return report


def heal_code_string(code: str, verbose: bool = True) -> str:
    """
    Quick function to heal a code string.
    
    Usage:
        healed = heal_code_string(ai_generated_code)
    """
    healer = AIImportHealer(verbose=verbose)
    healed_code, _ = healer.heal(code)
    return healed_code


def heal_file(filepath: str, verbose: bool = True) -> bool:
    """
    Quick function to heal a file.
    
    Usage:
        was_healed = heal_file("/tmp/test.py")
    """
    healer = AIImportHealer(verbose=verbose)
    return healer.heal_file(Path(filepath))


# ============================================================================
# DEMO: Self-healing test example
# ============================================================================

if __name__ == "__main__":
    # Example of broken AI-generated code
    broken_code = """
import pytest
from your_file_name import calculate  # <-- AI HALLUCINATION!

def add(x, y):
    return float(x + y)

def subtract(x, y):
    return float(x - y)

def test_addition():
    assert add(5, 3) == 8

def test_subtraction():
    assert subtract(5, 3) == 2

if __name__ == "__main__":
    pytest.main(["-v", "--tb=short", __file__])
"""
    
    safe_print("=" * 60)
    safe_print("🤖 AI IMPORT HALLUCINATION HEALER - DEMO")
    safe_print("=" * 60)
    safe_print("\n📋 Original (broken) code:")
    safe_print("-" * 60)
    safe_print(broken_code)
    safe_print("-" * 60)
    
    # Heal it!
    healer = AIImportHealer(verbose=True)
    healed_code, was_healed = healer.heal(broken_code)
    
    safe_print("\n" + "=" * 60)
    safe_print("💊 Healed code:")
    safe_print("-" * 60)
    safe_print(healed_code)
    safe_print("-" * 60)
    
    safe_print("\n" + healer.get_report())
    
    # Show usage for omnipkg integration
    safe_print("\n" + "=" * 60)
    safe_print("🔌 OMNIPKG INTEGRATION EXAMPLE:")
    safe_print("=" * 60)
    safe_print("""
# In omnipkg/commands/run.py, add this before execution:

from omnipkg.utils.ai_sanitizers import heal_code_string

def execute_python_code(code: str, ...):
    # ... existing code ...
    
    # Auto-heal AI hallucinations
    code = heal_code_string(code, verbose=True)
    
    # ... continue with execution ...
""")