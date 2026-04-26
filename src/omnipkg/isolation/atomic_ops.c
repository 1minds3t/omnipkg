#include <Python.h>
#include <stdint.h>  /* uintptr_t — pointer-sized integer, safe on 32-bit and 64-bit */

#ifdef _MSC_VER
#include <intrin.h>
#include <windows.h>
#pragma intrinsic(_InterlockedCompareExchange64)
#pragma intrinsic(_InterlockedExchange64)

static PyObject* atomic_cas64(PyObject* self, PyObject* args) {
    uintptr_t buffer_addr;
    long long expected, desired;
    if (!PyArg_ParseTuple(args, "kLL", &buffer_addr, &expected, &desired)) return NULL;
    volatile long long* ptr = (volatile long long*)buffer_addr;
    long long prev = _InterlockedCompareExchange64(ptr, desired, expected);
    return PyBool_FromLong(prev == expected);
}
static PyObject* atomic_store64(PyObject* self, PyObject* args) {
    uintptr_t buffer_addr;
    long long value;
    if (!PyArg_ParseTuple(args, "kL", &buffer_addr, &value)) return NULL;
    volatile long long* ptr = (volatile long long*)buffer_addr;
    _InterlockedExchange64(ptr, value);
    Py_RETURN_NONE;
}
static PyObject* atomic_load64(PyObject* self, PyObject* args) {
    uintptr_t buffer_addr;
    if (!PyArg_ParseTuple(args, "k", &buffer_addr)) return NULL;
    volatile long long* ptr = (volatile long long*)buffer_addr;
    long long val = _InterlockedExchangeAdd64((volatile long long*)ptr, 0);
    return PyLong_FromLongLong(val);
}

#else
/* GCC/Clang path */
static PyObject* atomic_cas64(PyObject* self, PyObject* args) {
    uintptr_t buffer_addr;
    long long expected, desired;
    if (!PyArg_ParseTuple(args, "kLL", &buffer_addr, &expected, &desired)) return NULL;
    volatile long long* ptr = (volatile long long*)buffer_addr;
    int success = __sync_bool_compare_and_swap(ptr, expected, desired);
    return PyBool_FromLong(success);
}
static PyObject* atomic_store64(PyObject* self, PyObject* args) {
    uintptr_t buffer_addr;
    long long value;
    if (!PyArg_ParseTuple(args, "kL", &buffer_addr, &value)) return NULL;
    volatile long long* ptr = (volatile long long*)buffer_addr;
    __atomic_store_n(ptr, value, __ATOMIC_RELEASE);
    Py_RETURN_NONE;
}
static PyObject* atomic_load64(PyObject* self, PyObject* args) {
    uintptr_t buffer_addr;
    if (!PyArg_ParseTuple(args, "k", &buffer_addr)) return NULL;
    volatile long long* ptr = (volatile long long*)buffer_addr;
    long long val = __atomic_load_n(ptr, __ATOMIC_ACQUIRE);
    return PyLong_FromLongLong(val);
}
#endif

static PyMethodDef AtomicMethods[] = {
    {"cas64", atomic_cas64, METH_VARARGS, "Atomic Compare-And-Swap (64-bit)"},
    {"store64", atomic_store64, METH_VARARGS, "Atomic Store (Release Semantics)"},
    {"load64", atomic_load64, METH_VARARGS, "Atomic Load (Acquire Semantics)"},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef atomicmodule = {
    PyModuleDef_HEAD_INIT, "omnipkg_atomic", "HFT Hardware Atomics", -1, AtomicMethods
};
PyMODINIT_FUNC PyInit_omnipkg_atomic(void) {
    return PyModule_Create(&atomicmodule);
}