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

# ═══════════════════════════════════════════════════════════
# BUBBLE-AWARE NUMPY REDIRECT
# ═══════════════════════════════════════════════════════════
# When a bubble is active (e.g. tensorflow-2.13.0 which bundles numpy-1.24.3),
# sys.path has the bubble at [0].  Any package imported transitively — including
# main-env packages like jax — will resolve `import numpy` to the bubble's
# numpy, not the main-env numpy they were built against.
#
# We fix this in the import hook: if numpy is being imported by a module that
# does NOT live inside the active bubble, we temporarily remove the bubble
# path(s) from sys.path for the duration of that one import so the normal
# site-packages numpy is found instead.

def _get_active_bubble_paths() -> list:
    """Return the list of omnipkg bubble paths currently at the front of sys.path.

    CRITICAL: this function must NEVER trigger an import (no `from x import y`).
    It is called from inside genius_import and from _import_numpy_from_main_env,
    both of which are already on the call stack when an import is in flight.
    Any import here would re-enter genius_import → infinite recursion.
    We rely solely on sys.path inspection which is always safe.
    """
    bubble_paths = []
    for p in sys.path:
        if '.omnipkg_versions' in p and '_omnipkg_cloaked' not in p:
            bubble_paths.append(p)
    return bubble_paths


def _caller_is_in_bubble(caller_file: str, bubble_paths: list) -> bool:
    """Return True if the importing module's __file__ lives inside a bubble directory."""
    if not caller_file or not bubble_paths:
        return False
    for bp in bubble_paths:
        if caller_file.startswith(bp):
            return True
    return False


_numpy_redirect_active = threading.local()  # re-entrancy guard for _import_numpy_from_main_env


def _import_numpy_from_main_env(name, globals, locals, fromlist, level):
    """
    Import numpy while temporarily hiding bubble paths from sys.path
    AND evicting bubble-numpy from sys.modules cache.

    Python's import machinery returns sys.modules["numpy"] directly when it's
    already cached — sys.path is not consulted at all.  So we must also evict
    the bubble-numpy entries from sys.modules before the redirected import,
    then restore them afterward so tensorflow's own code keeps using bubble numpy.

    CRITICAL: a re-entrancy guard (_numpy_redirect_active) prevents infinite
    recursion when numpy's own __getattr__ (e.g. for numpy.typing) triggers
    another genius_import call while we're already inside this function.
    """
    # Re-entrancy guard: if we're already redirecting numpy, fall straight
    # through to _original_import_func to avoid genius_import → here → loop.
    if getattr(_numpy_redirect_active, 'active', False):
        return _original_import_func(name, globals, locals, fromlist, level)

    bubble_paths = _get_active_bubble_paths()
    if not bubble_paths:
        return _original_import_func(name, globals, locals, fromlist, level)

    # Check if the currently-cached numpy lives inside a bubble
    _np_mod = sys.modules.get("numpy")
    _np_file = getattr(_np_mod, "__file__", "") or ""
    _numpy_is_from_bubble = any(_np_file.startswith(bp) for bp in bubble_paths)

    # If bubble numpy is already FULLY initialized (has __version__), the C
    # extension (_multiarray_umath.so) is already dlopened and its Python layer
    # is fully wired to that specific ABI.  Evicting the Python layer and
    # re-running it against a different version's pure-Python code produces a
    # mixed-state numpy (1.x .so + 2.x Python) → AttributeError on BoolDType
    # and similar 2.x-only symbols.  No safe mid-process numpy swap is possible
    # once the C extension is loaded.  Return the already-live numpy directly.
    if _numpy_is_from_bubble and _np_mod is not None and hasattr(_np_mod, "__version__"):
        _numpy_redirect_active.active = True
        try:
            return _original_import_func(name, globals, locals, fromlist, level)
        finally:
            _numpy_redirect_active.active = False

    if not _numpy_is_from_bubble:
        # Numpy in sys.modules is already from main-env (or mid-init from main-env).
        # Fall through to _original_import_func under the re-entrancy guard so that
        # numpy's own __getattr__ (e.g. for numpy.typing sub-imports) doesn't loop
        # back into _import_numpy_from_main_env again.
        _numpy_redirect_active.active = True
        try:
            return _original_import_func(name, globals, locals, fromlist, level)
        finally:
            _numpy_redirect_active.active = False

    # Snapshot all numpy-related sys.modules entries that belong to the bubble
    _numpy_snapshot = {
        k: v for k, v in sys.modules.items()
        if (k == "numpy" or k.startswith("numpy."))
        and any((getattr(v, "__file__", "") or "").startswith(bp) for bp in bubble_paths)
    }

    # Evict bubble-numpy from cache so _original_import_func does a real lookup,
    # BUT never evict C-extension modules (.so/.pyd) — their shared libraries are
    # already dlopened into the process and cannot be loaded a second time.
    # Evicting them forces Python to re-exec the .so → "cannot load module more
    # than once per process".  Only pure-Python entries need eviction.
    _c_ext_suffixes = ('.so', '.pyd')
    for k in _numpy_snapshot:
        mod_file = getattr(_numpy_snapshot[k], "__file__", "") or ""
        if not any(mod_file.endswith(s) for s in _c_ext_suffixes):
            sys.modules.pop(k, None)

    # Temporarily remove bubble paths from sys.path
    saved_path = sys.path[:]
    for bp in bubble_paths:
        try:
            sys.path.remove(bp)
        except ValueError:
            pass

    _numpy_redirect_active.active = True
    try:
        result = _original_import_func(name, globals, locals, fromlist, level)
        # Leave main-env numpy in sys.modules["numpy"] for the caller (jax).
        # But also restore bubble-numpy under a private key so tensorflow's
        # already-initialized submodules (which hold references to bubble-numpy
        # objects) continue to work.  We do this by keeping the snapshot alive
        # as attributes on a hidden module rather than in sys.modules, since
        # putting both in sys.modules simultaneously is impossible.
        # The snapshot objects are kept alive by _numpy_snapshot dict which
        # lives on the stack of the outer genius_import call — that's enough.
        return result
    except Exception:
        # Import from main-env failed — restore bubble numpy and try again
        sys.modules.update(_numpy_snapshot)
        raise
    finally:
        _numpy_redirect_active.active = False
        # Restore sys.path so the bubble is back for tensorflow's own imports
        sys.path[:] = saved_path

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

        # Patch numpy AND opt_einsum AND stub jax BEFORE TensorFlow imports them
        if is_tf_import and "tensorflow" not in sys.modules:
            _patch_numpy_for_tf_recursion()
            _patch_opt_einsum_for_isolation()
            _stub_jax_for_tf_load()
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
                # ── BUBBLE NUMPY REDIRECT ────────────────────────────────────
                # If a bubble is active AND the importing module does NOT live
                # inside that bubble (e.g. jax importing numpy while the
                # tensorflow-2.13.0 bubble is active), redirect numpy to main-env
                # by temporarily hiding the bubble path(s) from sys.path.
                #
                # Without this, jax — a main-env package built against numpy 2.x
                # — gets the bubble's numpy 1.24.3 and immediately fails on
                # `np.dtypes.StringDType()` (added in numpy 2.0).
                if name == "numpy" or name.startswith("numpy."):
                    # ── RE-ENTRANCY GUARD ────────────────────────────────────────
                    # While _import_numpy_from_main_env is executing it calls
                    # _original_import_func("numpy"), which triggers numpy/__init__.py,
                    # which immediately imports sub-modules (numpy.__config__,
                    # numpy._core, etc.) — all of which re-enter genius_import here.
                    # If those sub-imports go through the redirect path they try to
                    # start a second init of numpy's C extension → "cannot load module
                    # more than once per process".
                    # Solution: while the redirect is active, let ALL numpy/* imports
                    # pass straight through — they're already on the correct sys.path.
                    if getattr(_numpy_redirect_active, 'active', False):
                        return _original_import_func(name, globals, locals, fromlist, level)
                    # ── END RE-ENTRANCY GUARD ────────────────────────────────────
                    _bubble_paths = _get_active_bubble_paths()
                    if _bubble_paths:
                        _caller_file = (globals or {}).get("__file__", "") or ""
                        _in_bubble = _caller_is_in_bubble(_caller_file, _bubble_paths)
                        # Check if the ACTIVE bubble is itself a numpy bubble (e.g. numpy-1.26.4).
                        # A non-numpy bubble (e.g. pytorch_lightning-1.9.0) may contain a nested
                        # numpy copy, but we must NEVER load it — numpy's C extension (.so) can
                        # only be dlopen'd once per process. Loading the nested copy while main-env
                        # numpy is already (even partially) initialized causes:
                        #   ImportError: cannot load module more than once per process
                        # Fix: if the active bubble is NOT a numpy bubble, always redirect to
                        # main-env numpy regardless of whether the caller is inside the bubble.
                        _active_bubble_is_numpy = any(
                            bp.rstrip('/').rsplit('/', 1)[-1].startswith('numpy-')
                            for bp in _bubble_paths
                        )
                        if not _in_bubble or not _active_bubble_is_numpy:
                            return _import_numpy_from_main_env(
                                name, globals, locals, fromlist, level
                            )
                # ── END BUBBLE NUMPY REDIRECT ─────────────────────────────────
                return _original_import_func(name, globals, locals, fromlist, level)
        # ═══════════════════════════════════════════════════════════
        # NEW: Handle opt_einsum (used by both TF and PyTorch)
        # ═══════════════════════════════════════════════════════════
        is_opt_einsum = name and name.startswith("opt_einsum")

        # Be more specific about torch - don't catch safetensors.torch!
        is_torch_or_numpy = name and (
            (name == "torch" or name.startswith("torch.")) or 
            (name == "numpy" or name.startswith("numpy."))
        )
        is_tf_import = name and (name == "tensorflow" or name.startswith("tensorflow."))
        is_tf_submodule = (
            fromlist and any("tensorflow" in str(f) for f in fromlist) if fromlist else False
        )

        needs_special_handling = is_torch_or_numpy or is_tf_import or is_tf_submodule or is_opt_einsum

        if not needs_special_handling:
            return _original_import_func(name, globals, locals, fromlist, level)

        # Patch numpy AND opt_einsum AND stub jax BEFORE TensorFlow imports them
        if is_tf_import and "tensorflow" not in sys.modules:
            _patch_numpy_for_tf_recursion()
            _patch_opt_einsum_for_isolation()
            _stub_jax_for_tf_load()

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

        if is_tf_import and module and name == "tensorflow":
            import os
            _tf_loaded_pids.add(os.getpid())  # Mark THIS worker as having loaded TF
            _patch_numpy_for_tf_recursion()
            # ── Remove jax stub now that TF is fully loaded ──────────────────
            # _stub_jax_for_tf_load() injected a lightweight stub so TF's
            # lite/python/util.py could do `from jax import xla_computation`
            # without triggering real jax init (which would pull the wrong numpy).
            # TF is done now — evict the stub so subsequent `import jax` calls
            # load the real jax from main env with the correct numpy.
            _jax_stub = sys.modules.get("jax")
            if getattr(_jax_stub, "__omnipkg_jax_stub__", False):
                _jax_stub_keys = [k for k in list(sys.modules)
                                  if k == "jax" or k.startswith("jax.")]
                for _k in _jax_stub_keys:
                    sys.modules.pop(_k, None)

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


def _stub_jax_for_tf_load():
    """
    Stub out jax during TensorFlow bubble initialization to prevent numpy version
    conflicts.

    TF 2.x's tensorflow/lite/python/util.py does:
        from jax import xla_computation as _xla_computation

    If jax is not yet loaded, this triggers jax/__init__.py which immediately
    imports numpy.  When a TF bubble is active, sys.path[0] is the bubble
    directory which contains numpy 1.24.3.  jax was built against numpy 2.x and
    calls np.dtypes.StringDType() at module level — that attribute doesn't exist
    in 1.24.3, so the entire TF import explodes.

    The fix: if jax is not already in sys.modules (i.e. it hasn't been imported
    yet in this process), inject a lightweight stub that satisfies TF's
    xla_computation import without triggering the real jax init.  The stub is
    removed after TF finishes loading so real jax imports work normally later.

    If jax IS already loaded (user imported it before entering the TF bubble),
    it already has the correct numpy bound to it — no stub needed.
    """
    import types

    if "jax" in sys.modules:
        # jax already loaded — nothing to do
        return

    # Build a minimal jax stub that satisfies `from jax import xla_computation`
    _JAX_STUB_MARKER = "__omnipkg_jax_stub__"

    jax_stub = types.ModuleType("jax")
    jax_stub.__file__ = "<omnipkg-jax-stub>"
    jax_stub.__path__ = []
    jax_stub.__package__ = "jax"
    setattr(jax_stub, _JAX_STUB_MARKER, True)

    # xla_computation stub — callable, returns None-like object
    def _xla_computation_stub(*args, **kwargs):
        return None
    _xla_computation_stub.__name__ = "xla_computation"
    jax_stub.xla_computation = _xla_computation_stub

    # Also stub common jax submodules that TF might touch
    for _submod in ["jax.core", "jax._src", "jax._src.core", "jax._src.dtypes",
                    "jax.numpy", "jax.interpreters", "jax.lib"]:
        _sm = types.ModuleType(_submod)
        _sm.__file__ = "<omnipkg-jax-stub>"
        sys.modules[_submod] = _sm

    sys.modules["jax"] = jax_stub

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