# warning_silencer.py
"""
Nuclear warning silencer that intercepts stderr and filters annoying messages.
"""
import io
import os
import re
import sys
import threading
import warnings

_original_stderr = sys.stderr
_silencer_installed = False
_silencer_lock = threading.Lock()

# Patterns to NUKE from stderr (case-insensitive regex patterns)
NUKE_PATTERNS = [
    r"The NumPy module was reloaded",
    r"imported a second time",
    r"Could not find cuda drivers on your machine",
    r"GPU will not be used",
    r"TF-TRT Warning: Could not find TensorRT",
    r"successful NUMA node read from SysFS had negative value",
    r"Cannot dlopen some GPU libraries",
    r"Skipping registering GPU devices",
    r"This TensorFlow binary is optimized",
    r"rebuild TensorFlow with the appropriate compiler flags",
    r"A module that was compiled using NumPy 1.x cannot be run in NumPy 2",
]

# Compile patterns for performance

_compiled_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in NUKE_PATTERNS]


class FilteredStderr(io.TextIOBase):
    """
    A stderr wrapper that filters out annoying warnings in real-time.
    """

    def __init__(self, original_stderr):
        self.original_stderr = original_stderr
        self.buffer = []
        self.lock = threading.Lock()

    def write(self, text):
        """Write text, but filter out nuked patterns."""
        if not text:
            return 0

        with self.lock:
            # Check if this line matches any nuke patterns
            should_nuke = any(pattern.search(text) for pattern in _compiled_patterns)

            if should_nuke:
                # Silently discard this line
                return len(text)
            else:
                # Pass through to original stderr
                return self.original_stderr.write(text)

    def flush(self):
        """Flush the original stderr."""
        try:
            self.original_stderr.flush()
        except Exception:
            pass

    def isatty(self):
        """Check if original stderr is a TTY."""
        try:
            return self.original_stderr.isatty()
        except Exception:
            return False

    def fileno(self):
        """Return the file descriptor of original stderr."""
        return self.original_stderr.fileno()


def install_warning_silencer():
    """
    Install the nuclear warning silencer that intercepts stderr.
    """
    global _silencer_installed

    with _silencer_lock:
        if _silencer_installed:
            return

        # Install Python warnings filters first
        _install_python_warning_filters()

        # Install TensorFlow environment variables
        _install_tf_env_silencers()

        # Install stderr interceptor (nuclear option)
        if not isinstance(sys.stderr, FilteredStderr):
            sys.stderr = FilteredStderr(_original_stderr)

        _silencer_installed = True


def _install_python_warning_filters():
    """Install Python warnings filters for common annoyances."""
    # NumPy reload warning
    warnings.filterwarnings("ignore", message=".*NumPy module was reloaded.*", category=UserWarning)

    # NumPy 1.x/2.x compatibility warning
    warnings.filterwarnings(
        "ignore",
        message=".*compiled using NumPy 1.x cannot be run in NumPy 2.*",
        category=UserWarning,
    )

    # Catch-all for UserWarning from specific modules
    warnings.filterwarnings("ignore", category=UserWarning, module="omnipkg.isolation.*")


def _install_tf_env_silencers():
    """Set TensorFlow environment variables to silence warnings."""
    silence_vars = {
        "TF_CPP_MIN_LOG_LEVEL": "3",  # Only show errors
        "TF_ENABLE_ONEDNN_OPTS": "0",  # Disable oneDNN custom operations
        "CUDA_VISIBLE_DEVICES": "",  # Hide CUDA from TF (if no GPU needed)
    }

    for key, value in silence_vars.items():
        if key not in os.environ:
            os.environ[key] = value


def uninstall_warning_silencer():
    """
    Uninstall the warning silencer and restore original stderr.
    """
    global _silencer_installed

    with _silencer_lock:
        if not _silencer_installed:
            return

        # Restore original stderr
        if isinstance(sys.stderr, FilteredStderr):
            sys.stderr = _original_stderr

        _silencer_installed = False


def add_custom_nuke_pattern(pattern: str):
    """
    Add a custom pattern to the nuke list.

    Args:
        pattern: Regex pattern (case-insensitive) to filter from stderr
    """
    global _compiled_patterns
    _compiled_patterns.append(re.compile(pattern, re.IGNORECASE))


# Context manager for temporary silencing
class silence_warnings:
    """
    Context manager to temporarily install warning silencer.

    Usage:
        with silence_warnings():
            import tensorflow as tf
            import numpy as np
    """

    def __enter__(self):
        install_warning_silencer()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Keep silencer installed (don't uninstall)
        # This prevents warnings from leaking after context exit
        pass


# Decorator for silencing warnings in functions
def silenced(func):
    """
    Decorator to silence warnings in a function.

    Usage:
        @silenced
        def my_noisy_function():
            import tensorflow as tf
            return tf.constant([1, 2, 3])
    """

    def wrapper(*args, **kwargs):
        install_warning_silencer()
        return func(*args, **kwargs)

    return wrapper


# Quick utility to silence specific modules
def silence_module_warnings(*module_names):
    """
    Silence warnings from specific modules.

    Args:
        *module_names: Module names to silence (e.g., 'tensorflow', 'numpy')
    """
    for module_name in module_names:
        warnings.filterwarnings("ignore", module=f"{module_name}.*")
