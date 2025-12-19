from omnipkg.common_utils import safe_print

"""
Verification Hooks System

Allows plugging in custom verification logic, special handling, or
time machine integration without bloating the core verification code.

Usage:
    from omnipkg.installation.verification_hooks import register_hook

    @register_hook('pre_verification')
    def my_custom_check(package_name, version, staging_path):
        # Do custom stuff
        return True
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print


class HookType(Enum):
    """Types of verification hooks."""

    PRE_VERIFICATION = "pre_verification"  # Before any testing
    POST_VERIFICATION = "post_verification"  # After all testing
    PRE_GROUP_TEST = "pre_group_test"  # Before testing a group
    POST_GROUP_TEST = "post_group_test"  # After testing a group
    ON_FAILURE = "on_failure"  # When verification fails
    ON_SUCCESS = "on_success"  # When verification succeeds


@dataclass
class HookContext:
    """Context passed to hook functions."""

    package_name: str
    version: str
    staging_path: Path
    parent_omnipkg: Any
    gatherer: Any
    extra: Optional[Dict[str, Any]] = None


# Global hook registry
_HOOKS: Dict[HookType, List[Callable]] = {hook_type: [] for hook_type in HookType}


def register_hook(hook_type: HookType, priority: int = 100):
    """
    Decorator to register a verification hook.

    Args:
        hook_type: When to call this hook
        priority: Lower numbers run first (default 100)

    Example:
        @register_hook(HookType.PRE_VERIFICATION, priority=10)
        def check_license(context: HookContext) -> bool:
            # Return False to abort verification
            return True
    """

    def decorator(func: Callable):
        _HOOKS[hook_type].append((priority, func))
        # Sort by priority
        _HOOKS[hook_type].sort(key=lambda x: x[0])
        return func

    return decorator


def run_hooks(hook_type: HookType, context: HookContext) -> bool:
    """
    Run all registered hooks of a given type.

    Args:
        hook_type: Which hooks to run
        context: Context to pass to hooks

    Returns:
        True if all hooks pass, False if any hook fails
    """
    for priority, hook_func in _HOOKS[hook_type]:
        try:
            result = hook_func(context)
            if result is False:
                safe_print(f"      ⚠️  Hook '{hook_func.__name__}' returned False, aborting")
                return False
        except Exception as e:
            safe_print(f"      ❌ Hook '{hook_func.__name__}' failed: {e}")
            return False

    return True


# ============================================================================
# BUILT-IN HOOKS (Examples)
# ============================================================================


@register_hook(HookType.PRE_VERIFICATION, priority=10)
def check_disk_space(context: HookContext) -> bool:
    """Ensure we have enough disk space before verification."""
    import shutil

    stat = shutil.disk_usage(context.staging_path)
    free_gb = stat.free / (1024**3)

    if free_gb < 0.5:  # Less than 500MB free
        safe_print(f"      ⚠️  WARNING: Low disk space ({free_gb:.1f}GB free)")

    # Always return True - just a warning
    return True


@register_hook(HookType.ON_FAILURE, priority=100)
def log_failure_details(context: HookContext) -> bool:
    """Log detailed failure info for debugging."""
    log_path = Path.home() / ".omnipkg" / "verification_failures.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    import datetime

    with open(log_path, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"Failure: {context.package_name}=={context.version}\n")
        f.write(f"Time: {datetime.datetime.now()}\n")
        f.write(f"Staging: {context.staging_path}\n")
        if context.extra:
            f.write(f"Extra: {context.extra}\n")
        f.write(f"{'='*60}\n")

    return True  # Don't block on logging errors


# ============================================================================
# TIME MACHINE INTEGRATION HOOKS
# ============================================================================


@register_hook(HookType.PRE_VERIFICATION, priority=50)
def time_machine_snapshot(context: HookContext) -> bool:
    """
    Create a time machine snapshot before verification.

    This allows rollback if verification fails catastrophically.
    """
    if not hasattr(context.parent_omnipkg, "time_machine"):
        return True  # Time machine not enabled

    try:
        snapshot_id = context.parent_omnipkg.time_machine.create_snapshot(
            f"pre_verify_{context.package_name}_{context.version}", automatic=True
        )

        if not context.extra:
            context.extra = {}
        context.extra["time_machine_snapshot"] = snapshot_id

        safe_print(f"      📸 Time machine snapshot: {snapshot_id}")
        return True

    except Exception as e:
        safe_print(f"      ⚠️  Time machine snapshot failed: {e}")
        return True  # Don't block verification on snapshot failure


@register_hook(HookType.ON_FAILURE, priority=10)
def time_machine_restore_on_failure(context: HookContext) -> bool:
    """
    Restore time machine snapshot if verification fails.
    """
    if not context.extra or "time_machine_snapshot" not in context.extra:
        return True

    snapshot_id = context.extra["time_machine_snapshot"]

    try:
        safe_print("      🔄 Restoring from time machine snapshot...")
        context.parent_omnipkg.time_machine.restore_snapshot(snapshot_id)
        safe_print("      ✅ Time machine restore successful")
        return True

    except Exception as e:
        safe_print(f"      ❌ Time machine restore failed: {e}")
        return False


# ============================================================================
# CUSTOM VERIFICATION HOOKS
# ============================================================================


@register_hook(HookType.POST_GROUP_TEST, priority=100)
def validate_critical_imports(context: HookContext) -> bool:
    """
    Extra validation for critical packages.

    Some packages might import successfully but be broken.
    This hook can run additional checks.
    """
    critical_packages = {
        "numpy": lambda: __import__("numpy").array([1, 2, 3]),
        "pandas": lambda: __import__("pandas").DataFrame({"a": [1, 2]}),
        "torch": lambda: __import__("torch").tensor([1.0]),
    }

    pkg_name = context.package_name.lower()
    if pkg_name in critical_packages:
        try:
            critical_packages[pkg_name]()
            safe_print(f"      ✅ {pkg_name} critical function test passed")
        except Exception as e:
            safe_print(f"      ❌ {pkg_name} critical function test failed: {e}")
            return False

    return True


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def clear_hooks(hook_type: Optional[HookType] = None):
    """
    Clear registered hooks.

    Args:
        hook_type: Specific type to clear, or None to clear all
    """
    if hook_type:
        _HOOKS[hook_type] = []
    else:
        for ht in HookType:
            _HOOKS[ht] = []


def list_hooks() -> Dict[HookType, List[str]]:
    """
    List all registered hooks.

    Returns:
        Dict mapping hook types to list of function names
    """
    result = {}
    for hook_type, hooks in _HOOKS.items():
        result[hook_type] = [f.__name__ for _, f in hooks]
    return result
