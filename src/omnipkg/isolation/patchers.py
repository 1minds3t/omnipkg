import builtins
import importlib

# omnipkg/tf_smart_patcher.py - LAZY LOADING WITH GRACEFUL FALLBACKS
import sys
import threading
import warnings

from omnipkg.common_utils import safe_print
from omnipkg.i18n import _

try:
    from omnipkg.common_utils import ProcessCorruptedException
except ImportError:

    class ProcessCorruptedException(Exception):
        pass


try:
    from .common_utils import safe_print
except ImportError:
    try:
        pass
    except ImportError:
        # Ultimate fallback if safe_print isn't available
        def safe_print(msg):
            try:
                print(msg)
            except:
                pass


_tf_smart_initialized = False
_tf_module_cache = {}
_original_import_func = builtins.__import__
_circular_import_stats = {}
_tf_loaded_pids = set()  # Track which PIDs have loaded TF
_numpy_purge_retry_guard = threading.local()  # Prevents nested purge+retry

_tf_circular_deps_known = {
    "module_util": "tensorflow.python.tools.module_util",
    "lazy_loader": "tensorflow.python.util.lazy_loader",
    "tf_export": "tensorflow.python.util.tf_export",
    "deprecation": "tensorflow.python.util.deprecation",
    "compat": "tensorflow.python.util.compat",
    "dispatch": "tensorflow.python.util.dispatch",
}
_recursion_guard = threading.local()


def _patch_numpy_for_tf_recursion():
    """
    Applies a patch to numpy.issubdtype to prevent infinite recursion
    when used with certain versions of TensorFlow.
    """
    try:
        import numpy.core.numerictypes as nt

        # Check if we've already patched it
        if hasattr(nt.issubdtype, "__omnipkg_healed__"):
            return

        original_issubdtype = nt.issubdtype

        def healed_issubdtype(arg1, arg2):
            """
            A wrapper around the original issubdtype that prevents re-entry.
            """
            if getattr(_recursion_guard, "in_issubdtype_check", False):
                # We are already in this function, so we're in a recursive loop.
                # Break the cycle by returning a safe default.
                return False

            _recursion_guard.in_issubdtype_check = True
            try:
                # Call the original, real function
                return original_issubdtype(arg1, arg2)
            finally:
                # Always release the guard
                _recursion_guard.in_issubdtype_check = False

        healed_issubdtype.__omnipkg_healed__ = True
        nt.issubdtype = healed_issubdtype
        #         safe_print("🩹 [OMNIPKG] Healed NumPy's issubdtype for TensorFlow.")

    except (ImportError, AttributeError) as e:
        # This might happen if NumPy isn't installed or has an unusual structure.
        # It's safe to ignore and proceed without the patch.
        safe_print(_('⚠️  [OMNIPKG] Could not apply NumPy recursion patch: {}').format(e))


def smart_tf_patcher():
    """
    Install a lazy import hook that only activates TF/PyTorch/NumPy handling
    when those packages are actually being imported.
    """
    global _tf_smart_initialized

    if hasattr(builtins.__import__, "__omnipkg_genius_import__"):
        return

    if _tf_smart_initialized:
        return

    def genius_import(name, globals=None, locals=None, fromlist=(), level=0):
        """
        Lazy import hook that only handles TF/PyTorch/NumPy when encountered.
        NOW WITH STANDARD LIBRARY SAFEGUARD to prevent interference with packaging operations.
        """

        # CRITICAL FIX: Ensure globals always has __name__
        # ═══════════════════════════════════════════════════════════
        if globals is None:
            globals = {"__name__": "__main__", "__package__": None}
        elif "__name__" not in globals:
            globals["__name__"] = "__main__"

        # ═══════════════════════════════════════════════════════════
        # CRITICAL SAFEGUARD: Never intercept standard library imports
        # ═══════════════════════════════════════════════════════════
        STDLIB_WHITELIST = {
            "importlib",
            "importlib.metadata",
            "importlib_metadata",
            "pkg_resources",
            "setuptools",
            "pip",
            "distutils",
            "_distutils_hack",
            "packaging",
            "wheel",
            "safetensors",  # <-- ADD THIS to prevent torch-checking safetensors.torch
            "huggingface_hub",  # <-- ADD THIS too for transformers compatibility
            "accelerate",  # <-- ADD THIS for transformers dependencies
        }

        # Check if this is a standard library import that we should ignore
        if name:
            # Direct match or submodule of whitelisted packages
            if name in STDLIB_WHITELIST or any(
                name.startswith(f"{pkg}.") for pkg in STDLIB_WHITELIST
            ):
                return _original_import_func(name, globals, locals, fromlist, level)

        # Also check fromlist for whitelisted packages
        if fromlist:
            for item in fromlist:
                item_str = str(item)
                if any(pkg in item_str for pkg in STDLIB_WHITELIST):
                    return _original_import_func(name, globals, locals, fromlist, level)

        # ═══════════════════════════════════════════════════════════
        # Continue with existing special handling logic
        # ═══════════════════════════════════════════════════════════
        # Be more specific about torch - don't catch safetensors.torch!
        is_torch_or_numpy = name and (
            (name == "torch" or name.startswith("torch.")) or 
            (name == "numpy" or name.startswith("numpy."))
        )
        is_tf_import = name and (name == "tensorflow" or name.startswith("tensorflow."))
        is_tf_submodule = (
            fromlist and any("tensorflow" in str(f) for f in fromlist) if fromlist else False
        )

        needs_special_handling = is_torch_or_numpy or is_tf_import or is_tf_submodule

        if not needs_special_handling:
            return _original_import_func(name, globals, locals, fromlist, level)

        # Patch numpy AND opt_einsum BEFORE TensorFlow imports them
        if is_tf_import and "tensorflow" not in sys.modules:
            _patch_numpy_for_tf_recursion()
            _patch_opt_einsum_for_isolation()


        # ═══════════════════════════════════════════════════════════
        # torch/numpy SPECIFIC LOGIC (Warning Suppression)
        # ═══════════════════════════════════════════════════════════
        if is_torch_or_numpy:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="The NumPy module was reloaded",
                    category=UserWarning,
                )
                warnings.filterwarnings(
                    "ignore",
                    message="A module that was compiled using NumPy 1.x cannot be run in NumPy 2.+",
                    category=UserWarning,
                )
                try:
                    return _original_import_func(name, globals, locals, fromlist, level)
                except RuntimeError as _e:
                    # numpy 2.x: "CPU dispatcher tracer already initialized"
                    # The C dispatcher singleton can't be re-initialized after a
                    # bubble switch. The existing module object is still usable.
                    if "dispatcher" in str(_e).lower():
                        _existing = sys.modules.get(name)
                        if _existing is not None:
                            return _existing
                    raise
                except (ModuleNotFoundError, ImportError) as _mne:
                    # numpy import failures — two cases:
                    # 1. ModuleNotFoundError: stale C ext .so path (bubble was cloaked)
                    # 2. ImportError: "partially initialized module" or "source directory"
                    #    — main-env numpy/ leaked alongside a bubble causing mid-init conflict
                    # Both: purge all numpy modules and retry once from the active bubble.
                    _mne_str = str(_mne)
                    _is_numpy_import = (name == "numpy" or (name and name.startswith("numpy.")))
                    _is_numpy_err = "numpy" in _mne_str
                    _is_partial = (
                        "partially initialized" in _mne_str
                        or "source directory" in _mne_str
                        or "cannot import name" in _mne_str
                    )
                    if _is_numpy_import and (_is_numpy_err or _is_partial):
                        # Guard: only do purge+retry at the TOP of the import chain.
                        # If we're already inside a purge+retry, DON'T intercept —
                        # let the import proceed naturally.  numpy's __init__.py imports
                        # many C extension submodules (numpy.core._multiarray_tests etc.)
                        # during its init sequence; if any of them fail and we intercept
                        # them here, we break numpy's own error handling which gracefully
                        # handles optional C-ext failures.
                        # Only intercept if this is ALSO a top-level "numpy not found"
                        # (not a submodule failure during an ongoing numpy init).
                        if getattr(_numpy_purge_retry_guard, "active", False):
                            # Let the exception propagate naturally without interference
                            raise  # re-raise original exception unmodified

                        _numpy_purge_retry_guard.active = True
                        try:
                            # Purge all numpy modules including any partial sentinel
                            # left by an interrupted numpy.lib.__init__ execution.
                            _stale = [k for k in list(sys.modules.keys())
                                      if k == "numpy" or k.startswith("numpy.")]
                            for _k in _stale:
                                sys.modules.pop(_k, None)
                            importlib.invalidate_caches()
                            # Single retry from active bubble/site-packages path.
                            try:
                                return _original_import_func(name, globals, locals, fromlist, level)
                            except Exception as _retry_err:
                                # Purge any new partial sentinels the failed retry created
                                _stale2 = [k for k in list(sys.modules.keys())
                                           if k == "numpy" or k.startswith("numpy.")]
                                for _k in _stale2:
                                    sys.modules.pop(_k, None)
                                importlib.invalidate_caches()
                                # Diagnostic: show exactly what numpy dirs exist in sys.path
                                # so we know whether the problem is filesystem or import state
                                try:
                                    import os as _os
                                    from pathlib import Path as _Path
                                    _diag_lines = [
                                        f"[genius_import] retry failed: {_retry_err}",
                                        f"[genius_import] name={name!r} sys.path={sys.path[:4]}",
                                    ]
                                    for _pp in sys.path[:6]:
                                        _nd = _Path(_pp) / "numpy"
                                        _diag_lines.append(
                                            f"  {_pp}/numpy -> exists={_nd.exists()}"
                                            + (f" init={(_nd/'__init__.py').exists()}" if _nd.exists() else "")
                                        )
                                    _diag_lines.append(f"  cwd={_os.getcwd()}")
                                    sys.stderr.write("\n".join(_diag_lines) + "\n")
                                    sys.stderr.flush()
                                except Exception:
                                    pass
                                raise _mne
                        finally:
                            _numpy_purge_retry_guard.active = False
                    raise

        # ═══════════════════════════════════════════════════════════
        # Handle opt_einsum (used by both TF and PyTorch)
        # ═══════════════════════════════════════════════════════════
        is_opt_einsum = name and name.startswith("opt_einsum")

        if not is_opt_einsum:
            return _original_import_func(name, globals, locals, fromlist, level)

        # Patch numpy AND opt_einsum BEFORE TensorFlow imports them
        if is_tf_import and "tensorflow" not in sys.modules:
            _patch_numpy_for_tf_recursion()
            _patch_opt_einsum_for_isolation()

        # ═══════════════════════════════════════════════════════════
        # opt_einsum SPECIFIC LOGIC (Prevent cross-framework imports)
        # ═══════════════════════════════════════════════════════════
        if is_opt_einsum:
            # If we're in opt_einsum.backends and trying to import torch/jax/etc,
            # catch the ImportError gracefully
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore")
                try:
                    return _original_import_func(name, globals, locals, fromlist, level)
                except ImportError:
                    # This is expected - opt_einsum tries to detect available backends
                    # Return a dummy module to prevent crashes
                    if fromlist and "torch" in fromlist:
                        # Create a fake namespace
                        class DummyBackend:
                            pass
                        return DummyBackend()
                    raise

        # ═══════════════════════════════════════════════════════════
        # tensorflow SPECIFIC LOGIC (Reload Protection & Circular Deps)
        # ═══════════════════════════════════════════════════════════
        if is_tf_import:
            import os
            current_pid = os.getpid()
            
            # Only check for reload if THIS WORKER previously loaded TF successfully
            if current_pid in _tf_loaded_pids and "tensorflow" not in sys.modules:
                safe_print("☢️  [OMNIPKG] FATAL TENSORFLOW RELOAD DETECTED!")
                raise ProcessCorruptedException(
                    "Attempted to reload TensorFlow in a process where its C++ libraries were already initialized."
                )
        if is_tf_import or is_tf_submodule:
            # FIX: Pre-emptively heal circular 'experimental' imports in nn modules
            if name and name.endswith(".nn") and fromlist and "experimental" in fromlist:
                if name in sys.modules:
                    mod = sys.modules[name]
                    # If experimental isn't attached yet, look for the partial module
                    if not hasattr(mod, "experimental"):
                        exp_name = name + ".experimental"
                        if exp_name in sys.modules:
                            setattr(mod, "experimental", sys.modules[exp_name])

            # Check for circular imports first
            if _detect_circular_import_scenario(name, fromlist, globals):
                return _handle_circular_import(name, fromlist, globals)

            # Handle partial initialization scenarios (only if numpy is available)
            try:
                if _is_partially_initialized_tf(globals):
                    return _handle_partial_initialization(name, fromlist, globals)
            except Exception:
                pass  # If checks fail, proceed with normal import

            # Handle C++/Python boundary crossing (only if needed)
            try:
                if _is_cpp_boundary_import(name, fromlist):
                    return _handle_cpp_boundary_import(name, fromlist, globals)
            except Exception:
                pass  # If C++ handling fails, proceed with normal import

        # ═══════════════════════════════════════════════════════════
        # THE ACTUAL IMPORT
        # ═══════════════════════════════════════════════════════════
        module = _original_import_func(name, globals, locals, fromlist, level)

        if is_tf_import and module:
            import os
            _tf_loaded_pids.add(os.getpid())  # Mark THIS worker as having loaded TF
            _patch_numpy_for_tf_recursion()

        return module

    genius_import.__omnipkg_genius_import__ = True
    builtins.__import__ = genius_import
    _tf_smart_initialized = True


# ═══════════════════════════════════════════════════════════
# LAZY C++ STABILIZATION (only when actually needed)
# ═══════════════════════════════════════════════════════════


def _lazy_init_cpp_reality_anchors():
    """Lazily initialize C++ reality anchors only when first needed."""
    if "cpp_reality_anchor" not in _tf_module_cache:
        _tf_module_cache["cpp_reality_anchor"] = {
            "numpy_dtype_mappings": _create_stable_dtype_mappings(),
            "memory_layout_guides": _create_memory_layout_guides(),
            "type_conversion_handles": _create_type_conversion_handles(),
        }


def _create_stable_dtype_mappings():
    """Create stable dtype mappings (only if numpy is available)."""
    stable_mappings = {}
    try:
        import numpy as np

        stable_mappings = {
            "float32": np.float32,
            "float64": np.float64,
            "int32": np.int32,
            "int64": np.int64,
            "bool": np.bool_,
        }
    except ImportError:
        pass  # NumPy not available, return empty mappings
    return stable_mappings


def _create_memory_layout_guides():
    """Create memory layout consistency guides."""
    return {
        "C_CONTIGUOUS": "C",
        "F_CONTIGUOUS": "F",
        "ANY_CONTIGUOUS": "A",
    }


def _create_type_conversion_handles():
    """Create handles for C++ type conversion functions."""
    return {
        "tensor_to_numpy": _tensor_to_numpy_stabilized,
        "numpy_to_tensor": _numpy_to_tensor_stabilized,
    }


def _is_cpp_boundary_import(name, fromlist):
    """Detect imports that cross the C++/Python boundary."""
    cpp_boundary_modules = [
        "tensorflow.python.pywrap_tensorflow",
        "tensorflow.python._pywrap_",
        "tensorflow.compiler.",
        "tensorflow.lite.python.",
    ]

    for boundary in cpp_boundary_modules:
        if name and name.startswith(boundary):
            return True
        if fromlist and any(boundary in str(f) for f in fromlist):
            return True

    return False


def _handle_cpp_boundary_import(name, fromlist, globals):
    """Handle C++/Python boundary imports with lazy initialization."""
    try:
        _lazy_init_cpp_reality_anchors()  # Only init if not already done
        _stabilize_cpp_psyche()
    except Exception:
        pass  # If stabilization fails, continue anyway

    result = _original_import_func(name, globals, None, fromlist, level=0)

    try:
        _post_import_cpp_stabilization(name, result)
    except Exception:
        pass  # If post-stabilization fails, continue anyway

    return result


def _stabilize_cpp_psyche():
    """Stabilize TensorFlow's C++ extensions (only if TF is loaded)."""
    if "tensorflow" in sys.modules:
        tf_module = sys.modules["tensorflow"]
        if not hasattr(tf_module, "__omnipkg_reality_anchors__"):
            if "cpp_reality_anchor" in _tf_module_cache:
                tf_module.__omnipkg_reality_anchors__ = _tf_module_cache["cpp_reality_anchor"]


def _post_import_cpp_stabilization(name, module):
    """Apply post-import stabilization to C++ modules."""
    try:
        if hasattr(module, "__file__") and module.__file__ and ".so" in str(module.__file__):
            module.__omnipkg_cpp_stabilized__ = True
    except Exception:
        pass


def _tensor_to_numpy_stabilized(tensor):
    """Stabilized tensor to numpy conversion (with graceful fallback)."""
    try:
        if hasattr(tensor, "numpy"):
            result = tensor.numpy()
            if hasattr(result, "flags"):
                result.flags.writeable = True
            return result
    except Exception:
        pass
    return None


def _numpy_to_tensor_stabilized(array):
    """Stabilized numpy to tensor conversion (with graceful fallback)."""
    try:
        stabilized_array = _stabilize_numpy_array(array)
        return stabilized_array
    except Exception:
        pass
    return array


def _stabilize_numpy_array(array):
    """Ensure numpy array is in a stable state (only if numpy is available)."""
    try:
        import numpy as np

        if not array.flags["C_CONTIGUOUS"]:
            array = np.ascontiguousarray(array)
        if not array.flags["WRITEABLE"]:
            array = array.copy()
        return array
    except Exception:
        pass
    return array


# ═══════════════════════════════════════════════════════════
# CIRCULAR IMPORT HANDLING (TensorFlow-specific)
# ═══════════════════════════════════════════════════════════


def _detect_circular_import_scenario(name, fromlist, globals):
    """Detect if we're in a circular import scenario."""
    if not globals:
        return False

    current_module = globals.get("__name__", "")

    circular_patterns = [
        ("tensorflow", "module_util"),
        ("tensorflow.python", "lazy_loader"),
        ("tensorflow.python.util", "tf_export"),
        ("tensorflow.python.util", "deprecation"),
        ("tensorflow.python.util", "compat"),
        ("tensorflow.python.util", "dispatch"),
    ]

    for pattern_module, pattern_import in circular_patterns:
        if pattern_module in current_module and fromlist and pattern_import in fromlist:
            _circular_import_stats[pattern_import] = (
                _circular_import_stats.get(pattern_import, 0) + 1
            )
            return True

    return False

def _patch_opt_einsum_for_isolation():
    """
    Robustly isolate opt_einsum from torch/jax/cupy using stubs
    to prevent circular imports and partial initialization errors during TF load.
    """
    import types

    # Frameworks to isolate if not already loaded
    targets = ['torch', 'jax', 'cupy']
    
    try:
        # We only stub if the main package isn't already loaded.
        # If torch is already in sys.modules, opt_einsum can use it safely.
        # If it's NOT, we stub it to prevent opt_einsum from triggering a load.
        unavailable = [t for t in targets if t not in sys.modules]
        
        if not unavailable:
            return

        for framework in unavailable:
            backend_name = f'opt_einsum.backends.{framework}'
            
            # Create stub module
            backend_module = types.ModuleType(backend_name)
            backend_module.__file__ = '<omnipkg-isolated>'
            
            # Add required opt_einsum interface methods to prevent AttributeErrors
            backend_module.build_expression = lambda *args, **kwargs: None
            backend_module.evaluate_constants = lambda *args, **kwargs: None
            backend_module.compute_size_by_dict = lambda *args, **kwargs: None
            
            # Add framework-specific stubs
            if framework == 'torch':
                backend_module.to_torch = lambda x: None
                backend_module.TorchBackend = object
            elif framework == 'jax':
                backend_module.to_jax = lambda x: None
                backend_module.JaxBackend = object
            elif framework == 'cupy':
                backend_module.to_cupy = lambda x: None
                backend_module.CupyBackend = object
            
            # Inject into sys.modules
            sys.modules[backend_name] = backend_module

    except Exception as e:
        safe_print(_('⚠️  [OMNIPKG] opt_einsum isolation failed: {}').format(e))

def _handle_circular_import(name, fromlist, globals):
    """Handle circular imports by pre-loading dependencies."""
    if not fromlist:
        return _original_import_func(name, globals, None, fromlist, level=0)

    successfully_imported = {}
    for import_name in fromlist:
        if import_name in _tf_circular_deps_known:
            real_module_path = _tf_circular_deps_known[import_name]

            if real_module_path in sys.modules:
                successfully_imported[import_name] = sys.modules[real_module_path]
            else:
                try:
                    target_module = importlib.import_module(real_module_path)
                    sys.modules[real_module_path] = target_module
                    successfully_imported[import_name] = target_module
                except ImportError:
                    pass

    if not successfully_imported:
        return _original_import_func(name, globals, None, fromlist, level=0)

    try:
        result = _original_import_func(name, globals, None, fromlist, level=0)
        return result
    except ImportError:
        if name in sys.modules:
            parent = sys.modules[name]
            for import_name, module in successfully_imported.items():
                if not hasattr(parent, import_name):
                    setattr(parent, import_name, module)
            return parent

        class CircularImportNamespace:
            def __init__(self, items):
                for key, value in items.items():
                    setattr(self, key, value)

        return CircularImportNamespace(successfully_imported)


def print_circular_import_summary():
    """Print a summary of circular imports healed."""
    if _circular_import_stats:
        summary = ", ".join(
            f"{dep}×{count}" for dep, count in sorted(_circular_import_stats.items())
        )
        safe_print(_('🔄 [OMNIPKG] Healed circular imports: {}').format(summary))


def _is_partially_initialized_tf(globals):
    """Check if we're in a partially initialized TensorFlow module."""
    if not globals or "__name__" not in globals:
        return False

    module_name = globals["__name__"]
    if not module_name.startswith("tensorflow"):
        return False

    module = sys.modules.get(module_name)
    if module and hasattr(module, "__file__"):
        if module_name == "tensorflow" and not hasattr(module, "python"):
            return True

    return False


def _handle_partial_initialization(name, fromlist, globals):
    """Handle partial initialization."""
    parent_module = globals["__name__"] if globals else name
    if parent_module in sys.modules:
        try:
            _force_complete_initialization(parent_module)
        except Exception:
            pass  # If force initialization fails, continue anyway

    return _original_import_func(name, globals, None, fromlist, level=0)


def _force_complete_initialization(module_name):
    """Force a module to complete its initialization."""
    if module_name not in sys.modules:
        return

    if module_name == "tensorflow":
        key_submodules = [
            "tensorflow.python",
            "tensorflow.python.util",
            "tensorflow.python.util.module_util",
        ]

        for submod in key_submodules:
            if submod not in sys.modules:
                try:
                    importlib.import_module(submod)
                except ImportError:
                    continue