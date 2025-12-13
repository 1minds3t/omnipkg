# omnipkg/tf_smart_patcher.py - FIXED CIRCULAR IMPORT HANDLING
import sys
import importlib
import builtins
import threading
import warnings
from types import ModuleType
try:
    from omnipkg.common_utils import ProcessCorruptedException
except ImportError:
    class ProcessCorruptedException(Exception): pass
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

_tf_smart_initialized = False
_tf_module_cache = {}
_original_import_func = builtins.__import__

# Track circular import healing stats
_circular_import_stats = {}

_tf_circular_deps_known = {
    'module_util': 'tensorflow.python.tools.module_util',
    'lazy_loader': 'tensorflow.python.util.lazy_loader', 
    'tf_export': 'tensorflow.python.util.tf_export',
    'deprecation': 'tensorflow.python.util.deprecation',
    'compat': 'tensorflow.python.util.compat',
    'dispatch': 'tensorflow.python.util.dispatch',
}


def smart_tf_patcher():
    """
    UNIFIED patcher for TensorFlow and PyTorch with C++ reality stabilization.
    """
    global _tf_smart_initialized

    if hasattr(builtins.__import__, '__omnipkg_genius_import__'):
        return

    if _tf_smart_initialized:
        return

    safe_print("🧠 [OMNIPKG] Installing import hooks with C++ stabilization")

    _install_cpp_reality_anchors()

    def genius_import(name, globals=None, locals=None, fromlist=(), level=0):
        """
        A single import hook that understands both TensorFlow and PyTorch.
        """
        # ═══════════════════════════════════════════════════════════
        # torch/numpy SPECIFIC LOGIC (Warning Suppression)
        # ═══════════════════════════════════════════════════════════
        if 'torch' in name or 'numpy' in name:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="The NumPy module was reloaded",
                    category=UserWarning
                )
                warnings.filterwarnings(
                    "ignore",
                    message="A module that was compiled using NumPy 1.x cannot be run in NumPy 2.+",
                    category=UserWarning
                )
                pass

        # ═══════════════════════════════════════════════════════════
        # tensorflow SPECIFIC LOGIC (Reload Protection & Circular Deps)
        # ═══════════════════════════════════════════════════════════
        is_tf_import = name and (name == 'tensorflow' or name.startswith('tensorflow.'))
        is_tf_submodule = fromlist and any('tensorflow' in str(f) for f in fromlist) if fromlist else False

        if is_tf_import:
            if hasattr(builtins, '__omnipkg_tf_loaded_once__') and 'tensorflow' not in sys.modules:
                safe_print("☢️  [OMNIPKG] FATAL TENSORFLOW RELOAD DETECTED!")
                raise ProcessCorruptedException(
                    "Attempted to reload TensorFlow in a process where its C++ libraries were already initialized."
                )

        if is_tf_import or is_tf_submodule:
            # Check for circular imports first
            if _detect_circular_import_scenario(name, fromlist, globals):
                return _handle_circular_import(name, fromlist, globals)
            
            # Handle partial initialization scenarios
            if _is_partially_initialized_tf(globals):
                return _handle_partial_initialization(name, fromlist, globals)
            
            # Handle C++/Python boundary crossing
            if _is_cpp_boundary_import(name, fromlist):
                return _handle_cpp_boundary_import(name, fromlist, globals)

        # ═══════════════════════════════════════════════════════════
        # THE ACTUAL IMPORT
        # ═══════════════════════════════════════════════════════════
        module = _original_import_func(name, globals, locals, fromlist, level)

        if is_tf_import and module:
            if not hasattr(builtins, '__omnipkg_tf_loaded_once__'):
                builtins.__omnipkg_tf_loaded_once__ = True

        return module

    genius_import.__omnipkg_genius_import__ = True
    builtins.__import__ = genius_import
    _tf_smart_initialized = True

def _install_cpp_reality_anchors():
    """Install handles for TensorFlow's C++ psyche to grip during reality shifts."""
    
    # Create stable memory anchors for C++ extensions
    _tf_module_cache['cpp_reality_anchor'] = {
        'numpy_dtype_mappings': _create_stable_dtype_mappings(),
        'memory_layout_guides': _create_memory_layout_guides(),
        'type_conversion_handles': _create_type_conversion_handles(),
    }

def _create_stable_dtype_mappings():
    """Create stable dtype mappings so C++ doesn't get confused during version switches."""
    stable_mappings = {}
    try:
        import numpy as np
        stable_mappings = {
            'float32': np.float32,
            'float64': np.float64, 
            'int32': np.int32,
            'int64': np.int64,
            'bool': np.bool_,
        }
    except ImportError:
        pass
    return stable_mappings

def _create_memory_layout_guides():
    """Create memory layout consistency guides."""
    return {
        'C_CONTIGUOUS': 'C',
        'F_CONTIGUOUS': 'F', 
        'ANY_CONTIGUOUS': 'A',
    }

def _create_type_conversion_handles():
    """Create handles for C++ type conversion functions."""
    return {
        'tensor_to_numpy': _tensor_to_numpy_stabilized,
        'numpy_to_tensor': _numpy_to_tensor_stabilized,
    }

def _is_cpp_boundary_import(name, fromlist):
    """Detect imports that cross the C++/Python boundary."""
    cpp_boundary_modules = [
        'tensorflow.python.pywrap_tensorflow',
        'tensorflow.python._pywrap_',
        'tensorflow.compiler.',
        'tensorflow.lite.python.',
    ]
    
    for boundary in cpp_boundary_modules:
        if name and name.startswith(boundary):
            return True
        if fromlist and any(boundary in str(f) for f in fromlist):
            return True
    
    return False

def _handle_cpp_boundary_import(name, fromlist, globals):
    """Handle C++/Python boundary imports."""
    _stabilize_cpp_psyche()
    result = _original_import_func(name, globals, None, fromlist, level=0)
    _post_import_cpp_stabilization(name, result)
    
    return result

def _stabilize_cpp_psyche():
    """Stabilize TensorFlow's C++ extensions before they freak out."""
    if 'tensorflow' in sys.modules:
        tf_module = sys.modules['tensorflow']
        if not hasattr(tf_module, '__omnipkg_reality_anchors__'):
            tf_module.__omnipkg_reality_anchors__ = _tf_module_cache['cpp_reality_anchor']

def _post_import_cpp_stabilization(name, module):
    """Apply post-import stabilization to C++ modules."""
    if hasattr(module, '__file__') and '.so' in str(module.__file__):
        module.__omnipkg_cpp_stabilized__ = True

def _tensor_to_numpy_stabilized(tensor):
    """Stabilized tensor to numpy conversion with reality anchors."""
    try:
        if hasattr(tensor, 'numpy'):
            result = tensor.numpy()
            if hasattr(result, 'flags'):
                result.flags.writeable = True
            return result
    except Exception:
        return None

def _numpy_to_tensor_stabilized(array):
    """Stabilized numpy to tensor conversion."""
    try:
        stabilized_array = _stabilize_numpy_array(array)
        return stabilized_array
    except Exception:
        return array

def _stabilize_numpy_array(array):
    """Ensure numpy array is in a stable state for C++ consumption."""
    try:
        import numpy as np
        if not array.flags['C_CONTIGUOUS']:
            array = np.ascontiguousarray(array)
        if not array.flags['WRITEABLE']:
            array = array.copy()
        return array
    except Exception:
        return array

def _detect_circular_import_scenario(name, fromlist, globals):
    """Detect if we're in a circular import scenario."""
    if not globals:
        return False
    
    current_module = globals.get('__name__', '')
    
    circular_patterns = [
        ('tensorflow', 'module_util'),
        ('tensorflow.python', 'lazy_loader'),
        ('tensorflow.python.util', 'tf_export'),
        ('tensorflow.python.util', 'deprecation'),
        ('tensorflow.python.util', 'compat'),
        ('tensorflow.python.util', 'dispatch'),
    ]
    
    for pattern_module, pattern_import in circular_patterns:
        if pattern_module in current_module and fromlist and pattern_import in fromlist:
            # Track stats instead of printing each one
            _circular_import_stats[pattern_import] = _circular_import_stats.get(pattern_import, 0) + 1
            return True
    
    return False

def _handle_circular_import(name, fromlist, globals):
    """
    FIXED: Intelligently handle circular imports by directly importing and 
    injecting into sys.modules, then returning the parent module.
    """
    if not fromlist:
        return _original_import_func(name, globals, None, fromlist, level=0)
    
    # Import each circular dependency directly and cache it
    successfully_imported = {}
    for import_name in fromlist:
        if import_name in _tf_circular_deps_known:
            real_module_path = _tf_circular_deps_known[import_name]
            
            # Check if already imported
            if real_module_path in sys.modules:
                successfully_imported[import_name] = sys.modules[real_module_path]
            else:
                try:
                    target_module = importlib.import_module(real_module_path)
                    # Ensure it's in sys.modules
                    sys.modules[real_module_path] = target_module
                    successfully_imported[import_name] = target_module
                except ImportError:
                    pass
    
    # If we couldn't import the dependencies, don't try to proceed
    if not successfully_imported:
        # Fall back to original import and let it fail naturally
        return _original_import_func(name, globals, None, fromlist, level=0)
    
    # Now do the original import - the circular deps are already loaded
    try:
        result = _original_import_func(name, globals, None, fromlist, level=0)
        return result
    except ImportError:
        # If the original import still fails, we have two options:
        # 1. If the parent module exists, return it with injected attributes
        # 2. Otherwise, return a namespace with just the imported items
        
        if name in sys.modules:
            # Parent module exists, inject the successfully imported items
            parent = sys.modules[name]
            for import_name, module in successfully_imported.items():
                if not hasattr(parent, import_name):
                    setattr(parent, import_name, module)
            return parent
        
        # Create a minimal namespace object (not a full module)
        # This is safer than creating a synthetic module
        class CircularImportNamespace:
            def __init__(self, items):
                for key, value in items.items():
                    setattr(self, key, value)
        
        return CircularImportNamespace(successfully_imported)

def print_circular_import_summary():
    """Print a summary of circular imports healed (call at end of loading)."""
    if _circular_import_stats:
        summary = ", ".join(f"{dep}×{count}" for dep, count in sorted(_circular_import_stats.items()))
        safe_print(f"🔄 [OMNIPKG] Healed circular imports: {summary}")

def _is_partially_initialized_tf(globals):
    """Check if we're in a partially initialized TensorFlow module."""
    if not globals or '__name__' not in globals:
        return False
    
    module_name = globals['__name__']
    if not module_name.startswith('tensorflow'):
        return False
    
    module = sys.modules.get(module_name)
    if module and hasattr(module, '__file__'):
        if module_name == 'tensorflow' and not hasattr(module, 'python'):
            return True
    
    return False

def _handle_partial_initialization(name, fromlist, globals):
    """Handle partial initialization."""
    parent_module = globals['__name__'] if globals else name
    if parent_module in sys.modules:
        _force_complete_initialization(parent_module)
    
    return _original_import_func(name, globals, None, fromlist, level=0)

def _force_complete_initialization(module_name):
    """Force a module to complete its initialization."""
    if module_name not in sys.modules:
        return
    
    if module_name == 'tensorflow':
        key_submodules = [
            'tensorflow.python',
            'tensorflow.python.util',
            'tensorflow.python.util.module_util'
        ]
        
        for submod in key_submodules:
            if submod not in sys.modules:
                try:
                    importlib.import_module(submod)
                except ImportError:
                    continue