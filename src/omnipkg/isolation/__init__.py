# src/omnipkg/isolation/__init__.py

try:
    from .omnipkg_atomic import store64, load64, cas64
    HAS_ATOMICS = True
except ImportError:
    HAS_ATOMICS = False
    
    # SLOW FALLBACKS (Just so code doesn't crash)
    def store64(addr, val):
        import ctypes
        ctypes.cast(addr, ctypes.POINTER(ctypes.c_longlong)).contents.value = val
        
    def load64(addr):
        import ctypes
        return ctypes.cast(addr, ctypes.POINTER(ctypes.c_longlong)).contents.value

    # CAS is hard to emulate safely in Python without locks, 
    # but for simple flags this works
    def cas64(addr, expected, desired):
        import ctypes
        ptr = ctypes.cast(addr, ctypes.POINTER(ctypes.c_longlong))
        if ptr.contents.value == expected:
            ptr.contents.value = desired
            return True
        return False