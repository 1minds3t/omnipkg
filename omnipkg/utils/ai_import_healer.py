#!/usr/bin/env python3
from __future__ import annotations  # Python 3.6+ compatibility
"""
AI Import Hallucination Healer
================================
Detects and removes AI-generated placeholder imports while being careful
not to block legitimate imports from real packages or user modules.

This intercepts code before execution and removes lines like:
    from your_file_name import calculate
    from my_script import function
    from placeholder_module import main
    
Safety Features:
- Only removes obvious placeholders (not real package names)
- Checks against known PyPI packages
- Preserves relative imports in real project structures
- Never touches stdlib imports
"""

import re
import sys
from pathlib import Path
from typing import Tuple, List, Set
import warnings


class AIImportHealer:
    """Heals AI-generated code that hallucinates placeholder imports."""
    
    # CONSERVATIVE: Only obvious placeholder patterns
    # These should NEVER be real module names in production code
    OBVIOUS_PLACEHOLDERS = [
        # "Your/My" patterns - clear placeholders
        r'your_file_name',
        r'your_module',
        r'your_file',
        r'your_package',
        r'your_library',
        r'your_app',
        r'your_function',
        r'your_class',
        r'your_custom_\w+',
        r'my_script',
        r'my_module',
        r'my_file',
        r'my_package',
        r'my_library',
        r'my_app',
        r'my_function',
        r'my_class',
        r'my_custom_\w+',
        
        # Meta placeholder names
        r'module_name',
        r'file_name',
        r'script_name',
        r'package_name',
        r'library_name',
        r'project_name',
        
        # Generic "this/that/some" patterns
        r'this_module',
        r'that_module',
        r'some_module',
        r'the_module',
        r'the_actual_module',
        r'actual_module',
        r'real_module',
        r'proper_module',
        r'correct_file',
        
        # Obvious dummy/placeholder words
        r'placeholder',
        r'placeholder_\w+',
        r'dummy_\w+',
        r'temp_\w+',
        r'example_\w+',
        r'sample_\w+',
        r'demo_\w+',
        r'test_module',  # But not 'test' alone - could be pytest!
        
        # Documentation placeholders
        r'foo_module',
        r'bar_module',
        r'baz_module',
        r'spam_module',
        r'eggs_module',
    ]
    
    # CONTEXT-AWARE: Only flag these if they look suspicious
    # These COULD be real modules, so we need additional checks
    SUSPICIOUS_PATTERNS = [
        r'calculator',  # Could be a real package
        r'calc',
        r'example',
        r'sample',
        r'demo',
        r'app',
        r'main',
        r'index',
    ]
    
    # SAFE LIST: Never touch these - they're real packages or stdlib
    STDLIB_AND_COMMON = {
        'sys', 'os', 're', 'math', 'json', 'time', 'datetime', 'pathlib',
        'collections', 'itertools', 'functools', 'typing', 'io', 'subprocess',
        'unittest', 'pytest', 'numpy', 'pandas', 'requests', 'flask', 'django',
        'asyncio', 'aiohttp', 'sqlalchemy', 'redis', 'celery', 'click',
        'rich', 'pytest', 'black', 'mypy', 'pylint', 'packaging',
        # Add omnipkg specific
        'omnipkg', 'importlib', 'setuptools', 'pip', 'wheel', 'build',
    }
    
    def __init__(self, verbose: bool = True, aggressive: bool = False, silent: bool = False):
        self.verbose = verbose
        self.silent = silent  # If True, show nothing at all
        self.aggressive = aggressive  # If True, also flag SUSPICIOUS_PATTERNS
        self.healed_count = 0
        self.removed_lines: List[str] = []
        self.skipped_safe: List[str] = []
        
    def _build_obvious_pattern(self) -> re.Pattern:
        """Build regex for OBVIOUS placeholders only."""
        placeholders = '|'.join(self.OBVIOUS_PLACEHOLDERS)
        pattern = rf'^\s*from\s+({placeholders})\s+import\s+.*$'
        return re.compile(pattern, re.MULTILINE | re.IGNORECASE)
    
    def _build_suspicious_pattern(self) -> re.Pattern:
        """Build regex for suspicious patterns (requires extra validation)."""
        patterns = '|'.join(self.SUSPICIOUS_PATTERNS)
        pattern = rf'^\s*from\s+({patterns})\s+import\s+.*$'
        return re.compile(pattern, re.MULTILINE | re.IGNORECASE)
    
    def _is_safe_import(self, module_name: str, code_context: str) -> bool:
        """
        Determine if an import is safe (shouldn't be removed).
        
        Returns True if:
        - Module is in stdlib or common packages
        - Module appears to be defined elsewhere in the code
        - Module follows a real project structure pattern
        """
        # Check stdlib and common packages
        base_module = module_name.split('.')[0]
        if base_module.lower() in self.STDLIB_AND_COMMON:
            return True
        
        # Check if module is actually defined in the code
        # Look for "class X:", "def X():", or file references
        class_pattern = rf'^class\s+{re.escape(module_name)}'
        func_pattern = rf'^def\s+{re.escape(module_name)}\s*\('
        if re.search(class_pattern, code_context, re.MULTILINE) or \
           re.search(func_pattern, code_context, re.MULTILINE):
            return True
        
        # Check for actual file references in comments
        # e.g., "# See calculator.py" suggests it's a real module
        if re.search(rf'{re.escape(module_name)}\.py', code_context):
            return True
        
        # If module has a real package structure (dots), probably real
        if '.' in module_name and not any(p in module_name for p in ['placeholder', 'example', 'your_', 'my_']):
            return True
        
        return False
    
    def _has_placeholder_indicators(self, line: str) -> bool:
        """
        Check if the import line has obvious placeholder indicators.
        
        Returns True if line contains comments like:
        - # TODO
        - # FIXME  
        - # Replace with...
        - # Placeholder
        """
        comment_indicators = [
            r'#.*todo',
            r'#.*fixme',
            r'#.*replace\s+with',
            r'#.*placeholder',
            r'#.*update\s+this',
            r'#.*change\s+this',
            r'#.*modify',
            r'#.*customize',
        ]
        
        for pattern in comment_indicators:
            if re.search(pattern, line, re.IGNORECASE):
                return True
        return False
    
    def _log(self, msg: str):
        """Log message if verbose mode is on."""
        if self.verbose:
            safe_print(f"🔧 {msg}", file=sys.stderr)
    
    def _summary(self, msg: str):
        """Show summary message unless silent mode is on."""
        if not self.silent:
            safe_print(f"🔧 {msg}", file=sys.stderr)
    
    def detect_hallucinated_imports(self, code: str) -> List[Tuple[str, str, bool]]:
        """
        Find all hallucinated import lines.
        
        Returns:
            List of (line, module_name, is_obvious) tuples
        """
        results = []
        
        # Find obvious placeholders (always flag these)
        obvious_pattern = self._build_obvious_pattern()
        for match in obvious_pattern.finditer(code):
            line = match.group(0)
            module_name = match.group(1)
            results.append((line.strip(), module_name, True))
        
        # Find suspicious patterns (only if aggressive mode or has indicators)
        if self.aggressive:
            suspicious_pattern = self._build_suspicious_pattern()
            for match in suspicious_pattern.finditer(code):
                line = match.group(0)
                module_name = match.group(1)
                
                # Double-check it's not safe
                if not self._is_safe_import(module_name, code):
                    # Only flag if has placeholder indicators OR aggressive mode
                    if self._has_placeholder_indicators(line):
                        results.append((line.strip(), module_name, False))
        
        return results
    
    def heal(self, code: str) -> Tuple[str, bool]:
        """
        Remove hallucinated imports from code.
        
        Returns:
            (healed_code, was_healed) tuple
        """
        # Detect hallucinations
        hallucinations = self.detect_hallucinated_imports(code)
        
        if not hallucinations:
            return code, False
        
        healed_code = code
        
        # Log what we're removing
        self._log("🚨 DETECTED AI HALLUCINATION!")
        
        for line, module_name, is_obvious in hallucinations:
            # Safety check: verify module isn't safe
            if not is_obvious and self._is_safe_import(module_name, code):
                self._log(f"   ⚠️  Skipping (appears safe): {line}")
                self.skipped_safe.append(line)
                continue
            
            # Remove the import
            confidence = "HIGH" if is_obvious else "MEDIUM"
            self._log(f"   Removing [{confidence}]: {line}")
            self.removed_lines.append(line)
            
            # Use word boundaries to avoid partial matches
            pattern = re.compile(r'^\s*' + re.escape(line) + r'\s*$', re.MULTILINE)
            healed_code = pattern.sub('', healed_code)
            self.healed_count += 1
        
        if self.healed_count > 0:
            self._log(f"✅ Healed {self.healed_count} hallucinated import(s)")
            # Show summary even if not verbose (unless silent)
            if not self.verbose:
                self._summary(f"Removed {self.healed_count} AI import hallucination(s)")
        
        if self.skipped_safe:
            self._log(f"ℹ️  Preserved {len(self.skipped_safe)} safe import(s)")
        
        return healed_code, self.healed_count > 0
    
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
            # Create backup
            backup_path = filepath.with_suffix(filepath.suffix + '.bak')
            filepath.rename(backup_path)
            self._log(f"💾 Backup saved: {backup_path}")
            
            # Write healed code
            filepath.write_text(healed_code)
            self._log(f"💾 Saved healed code to: {filepath}")
            
            # Show summary even if not verbose
            if not self.verbose:
                self._summary(f"Healed {filepath.name}: removed {self.healed_count} hallucination(s)")
            
            return True
        else:
            self._log("✨ No hallucinations detected")
            return False
    
    def get_report(self) -> str:
        """Get a summary report of healing operations."""
        if self.healed_count == 0 and not self.skipped_safe:
            return "✅ No AI hallucinations detected"
        
        report = f"🔧 AI Import Healer Report\n"
        report += f"{'=' * 50}\n"
        report += f"Total hallucinations healed: {self.healed_count}\n"
        
        if self.removed_lines:
            report += f"\nRemoved lines:\n"
            for line in self.removed_lines:
                report += f"  ❌ {line}\n"
        
        if self.skipped_safe:
            report += f"\nPreserved safe imports:\n"
            for line in self.skipped_safe:
                report += f"  ✅ {line}\n"
        
        return report


def heal_code_string(code: str, verbose: bool = True, aggressive: bool = False, silent: bool = False) -> str:
    """
    Quick function to heal a code string.
    
    Args:
        code: Python code to heal
        verbose: Print detailed healing messages
        aggressive: Also check suspicious patterns (less safe)
        silent: Show nothing at all (overrides verbose)
    
    Behavior:
        - verbose=True: Shows detailed logs of what's being removed
        - verbose=False: Shows only summary ("Removed 4 AI import hallucinations")
        - silent=True: Shows absolutely nothing
    
    Usage:
        healed = heal_code_string(ai_generated_code)
        healed = heal_code_string(code, verbose=False)  # Just summary
        healed = heal_code_string(code, silent=True)    # No output
    """
    healer = AIImportHealer(verbose=verbose, aggressive=aggressive, silent=silent)
    healed_code, _ = healer.heal(code)
    return healed_code


def heal_file(filepath: str, verbose: bool = True, aggressive: bool = False, silent: bool = False) -> bool:
    """
    Quick function to heal a file.
    
    Args:
        filepath: Path to Python file
        verbose: Print detailed healing messages
        aggressive: Also check suspicious patterns (less safe)
        silent: Show nothing at all (overrides verbose)
    
    Usage:
        was_healed = heal_file("/tmp/test.py")
        was_healed = heal_file("script.py", verbose=False)  # Just summary
        was_healed = heal_file("script.py", silent=True)    # No output
    """
    healer = AIImportHealer(verbose=verbose, aggressive=aggressive, silent=silent)
    return healer.heal_file(Path(filepath))


# ============================================================================
# DEMO: Self-healing test example
# ============================================================================

if __name__ == "__main__":
    print = lambda *args, **kwargs: __builtins__.print(*args, **kwargs)  # Safe print for demo
    
    # Example 1: Obvious hallucination (WILL be removed)
    obvious_hallucination = """
import pytest
from your_file_name import calculate  # <-- OBVIOUS AI HALLUCINATION!
from my_module import process_data    # <-- ANOTHER HALLUCINATION!

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
    
    # Example 2: Legitimate import (will NOT be removed)
    legitimate_code = """
import sys
from pathlib import Path
from rich import print  # Real package, won't be touched
from omnipkg import Omnipkg  # Real package, won't be touched
from calculator import add  # Might be a real local module

def main():
    print("Hello from omnipkg!")
"""
    
    # Example 3: Suspicious with context (will NOT be removed)
    suspicious_but_safe = """
# This is calculator.py - a real module in this project
from calculator import add, subtract
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

def multiply(x, y):
    return add(x, y) * 2  # Uses real calculator module
"""
    
    print("=" * 60)
    safe_print("🤖 AI IMPORT HALLUCINATION HEALER - IMPROVED DEMO")
    print("=" * 60)
    
    # Test 1: Obvious hallucinations
    safe_print("\n📋 Test 1: OBVIOUS hallucinations (WILL be removed)")
    print("-" * 60)
    print(obvious_hallucination)
    print("-" * 60)
    
    healer1 = AIImportHealer(verbose=True, aggressive=False)
    healed1, was_healed1 = healer1.heal(obvious_hallucination)
    
    safe_print("\n💊 Healed code:")
    print("-" * 60)
    print(healed1)
    print("-" * 60)
    print("\n" + healer1.get_report())
    
    # Test 2: Legitimate imports
    print("\n" + "=" * 60)
    safe_print("📋 Test 2: LEGITIMATE imports (will NOT be removed)")
    print("-" * 60)
    print(legitimate_code)
    print("-" * 60)
    
    healer2 = AIImportHealer(verbose=True, aggressive=False)
    healed2, was_healed2 = healer2.heal(legitimate_code)
    
    safe_print("\n💊 Result:")
    print("-" * 60)
    print(healed2)
    print("-" * 60)
    print("\n" + healer2.get_report())
    
    # Test 3: Suspicious but contextually safe
    print("\n" + "=" * 60)
    safe_print("📋 Test 3: SUSPICIOUS but contextually safe")
    print("-" * 60)
    print(suspicious_but_safe)
    print("-" * 60)
    
    healer3 = AIImportHealer(verbose=True, aggressive=False)
    healed3, was_healed3 = healer3.heal(suspicious_but_safe)
    
    safe_print("\n💊 Result:")
    print("-" * 60)
    print(healed3)
    print("-" * 60)
    print("\n" + healer3.get_report())
    
    # Show usage examples
    print("\n" + "=" * 60)
    safe_print("🔌 OMNIPKG INTEGRATION EXAMPLES:")
    print("=" * 60)
    print("""
# Conservative mode (default) - only removes OBVIOUS placeholders:
from omnipkg.utils.ai_sanitizers import heal_code_string

def execute_python_code(code: str, ...):
    # Auto-heal obvious AI hallucinations
    code = heal_code_string(code, verbose=True, aggressive=False)
    # ... continue with execution ...

# Aggressive mode - also checks suspicious patterns:
code = heal_code_string(code, verbose=True, aggressive=True)

# Silent mode - no output:
code = heal_code_string(code, verbose=False, aggressive=False)
""")