#include <Python.h>
#include <stdatomic.h>
#include <stdbool.h>

// The Control Block Structure (Aligned to 64 bytes)
typedef struct {
    volatile long version;      // 8 bytes
    volatile long writer_pid;   // 8 bytes
    volatile long lock_state;   // 8 bytes
    char padding[40];           // Pad to 64 bytes (Cache Line)
} ControlBlock;

// Atomic Compare-And-Swap for Version
// Returns 1 if successful, 0 if failed
static PyObject* atomic_cas_version(PyObject* self, PyObject* args) {
    long long buffer_addr;
    long expected_ver;
    long new_ver;

    if (!PyArg_ParseTuple(args, "Lll", &buffer_addr, &expected_ver, &new_ver)) {
        return NULL;
    }

    ControlBlock* block = (ControlBlock*)buffer_addr;
    
    // ATOMIC MAGIC: atomic_compare_exchange_strong
    // If block->version == expected_ver, set it to new_ver and return True
    // Else, return False
    // This happens in ~10-20 CPU cycles.
    bool success = __sync_bool_compare_and_swap(&block->version, expected_ver, new_ver);

    return PyBool_FromLong(success);
}

// Method Definitions
static PyMethodDef AtomicMethods[] = {
    {"cas_version", atomic_cas_version, METH_VARARGS, "Atomic Compare-And-Swap on Version"},
    {NULL, NULL, 0, NULL}
};

// Module Definition
static struct PyModuleDef atomicmodule = {
    PyModuleDef_HEAD_INIT,
    "omnipkg_atomic",
    "Hardware atomic operations for OmniPkg",
    -1,
    AtomicMethods
};

// Init
PyMODINIT_FUNC PyInit_omnipkg_atomic(void) {
    return PyModule_Create(&atomicmodule);
}