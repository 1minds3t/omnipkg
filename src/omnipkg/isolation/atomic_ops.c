#include <Python.h>

// --------------------------------------------------------
// HARDWARE ATOMICS FOR PYTHON (HFT GRADE)
// --------------------------------------------------------

// 1. COMPARE AND SWAP (CAS)
// Used for: Optimistic locking, updating state without mutex
static PyObject* atomic_cas64(PyObject* self, PyObject* args) {
    long long buffer_addr;  // Address of the shared memory (from Tensor.data_ptr())
    long long expected;
    long long desired;

    if (!PyArg_ParseTuple(args, "LLL", &buffer_addr, &expected, &desired)) {
        return NULL;
    }

    // Volatile pointer to ensure compiler doesn't cache the read
    volatile long long* ptr = (volatile long long*)buffer_addr;
    
    // GCC BUILTIN: Generates 'lock cmpxchg' instruction
    // Returns true if swap happened, false otherwise
    int success = __sync_bool_compare_and_swap(ptr, expected, desired);

    return PyBool_FromLong(success);
}

// 2. ATOMIC STORE (WRITE)
// Used for: Ringing the Doorbell
static PyObject* atomic_store64(PyObject* self, PyObject* args) {
    long long buffer_addr;
    long long value;

    if (!PyArg_ParseTuple(args, "LL", &buffer_addr, &value)) {
        return NULL;
    }

    volatile long long* ptr = (volatile long long*)buffer_addr;
    
    // ATOMIC STORE with Release Semantics (Ensures prior writes are visible)
    // Generates 'mov' with memory barrier on x86
    __atomic_store_n(ptr, value, __ATOMIC_RELEASE);

    Py_RETURN_NONE;
}

// 3. ATOMIC LOAD (READ)
// Used for: Checking Stop Flags
static PyObject* atomic_load64(PyObject* self, PyObject* args) {
    long long buffer_addr;

    if (!PyArg_ParseTuple(args, "L", &buffer_addr)) {
        return NULL;
    }

    volatile long long* ptr = (volatile long long*)buffer_addr;
    
    // ATOMIC LOAD with Acquire Semantics
    long long val = __atomic_load_n(ptr, __ATOMIC_ACQUIRE);

    return PyLong_FromLongLong(val);
}

static PyMethodDef AtomicMethods[] = {
    {"cas64", atomic_cas64, METH_VARARGS, "Atomic Compare-And-Swap (64-bit)"},
    {"store64", atomic_store64, METH_VARARGS, "Atomic Store (Release Semantics)"},
    {"load64", atomic_load64, METH_VARARGS, "Atomic Load (Acquire Semantics)"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef atomicmodule = {
    PyModuleDef_HEAD_INIT,
    "omnipkg_atomic",
    "HFT Hardware Atomics",
    -1,
    AtomicMethods
};

PyMODINIT_FUNC PyInit_omnipkg_atomic(void) {
    return PyModule_Create(&atomicmodule);
}