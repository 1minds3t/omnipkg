# quantum_chaos.py - A script that should destroy any package manager

"""
This script is DESIGNED to be impossible:
1. Uses TensorFlow 2.12 (needs numpy<1.24)
2. Uses TensorFlow 2.20 (needs numpy>=1.26)  
3. Uses SciPy 1.12 (needs numpy~=1.26)
4. Uses SciPy 1.16 (needs numpy>=2.3)
5. ALL IN THE SAME EXECUTION
6. While also swapping Rich versions mid-print
7. And doing actual computations with all of them
"""
<<<<<<< HEAD

from omnipkg.common_utils import safe_print
from omnipkg.i18n import _
from omnipkg.loader import omnipkgLoader
from omnipkg.core import ConfigManager
=======
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
from omnipkg.common_utils import safe_print
from omnipkg.i18n import _
>>>>>>> development

def test_tensorflow_old():
    """TensorFlow 2.12 with its ancient numpy requirements"""
    import tensorflow as tf
    import numpy as np
    
<<<<<<< HEAD
    safe_print(f"   TensorFlow version: {tf.__version__}")
    safe_print(f"   NumPy version: {np.__version__}")
=======
    assert tf.__version__.startswith('2.12'), f"Wrong TF version: {tf.__version__}"
    assert np.__version__.startswith('1.23'), f"Wrong numpy version: {np.__version__}"
>>>>>>> development
    
    # Create a simple model
    model = tf.keras.Sequential([
        tf.keras.layers.Dense(10, activation='relu', input_shape=(5,)),
        tf.keras.layers.Dense(1)
    ])
    
    # Do actual computation
    test_input = np.array([[1, 2, 3, 4, 5]], dtype=np.float32)
    result = model(test_input).numpy()[0][0]
    
<<<<<<< HEAD
    safe_print(f"   ✅ TF 2.12 prediction = {result:.4f}")
=======
    safe_print(f"✅ TF 2.12 with numpy 1.23: prediction = {result:.4f}")
>>>>>>> development
    return result

def test_tensorflow_new():
    """TensorFlow 2.20 with modern numpy"""
    import tensorflow as tf
    import numpy as np
    
<<<<<<< HEAD
    safe_print(f"   TensorFlow version: {tf.__version__}")
    safe_print(f"   NumPy version: {np.__version__}")
=======
    assert tf.__version__.startswith('2.20'), f"Wrong TF version: {tf.__version__}"
    assert np.__version__.startswith('2.3'), f"Wrong numpy version: {np.__version__}"
>>>>>>> development
    
    # Create a different model
    model = tf.keras.Sequential([
        tf.keras.layers.Dense(20, activation='tanh', input_shape=(5,)),
        tf.keras.layers.Dense(1, activation='sigmoid')
    ])
    
    test_input = np.array([[5, 4, 3, 2, 1]], dtype=np.float32)
    result = model(test_input).numpy()[0][0]
    
<<<<<<< HEAD
    safe_print(f"   ✅ TF 2.20 prediction = {result:.4f}")
    return result

def test_scipy_old():
    """SciPy 1.12 with numpy 1.26"""
=======
    safe_print(f"✅ TF 2.20 with numpy 2.3: prediction = {result:.4f}")
    return result

def test_scipy_combo():
    """Mix two incompatible scipy versions"""
>>>>>>> development
    import scipy
    import numpy as np
    from scipy import sparse
    from scipy import linalg
    
<<<<<<< HEAD
    safe_print(f"   SciPy version: {scipy.__version__}")
    safe_print(f"   NumPy version: {np.__version__}")
=======
    # This should be scipy 1.12 with numpy 1.26
    assert scipy.__version__.startswith('1.12'), f"Wrong scipy: {scipy.__version__}"
    assert np.__version__.startswith('1.26'), f"Wrong numpy: {np.__version__}"
>>>>>>> development
    
    # Sparse matrix operations
    matrix = sparse.csr_matrix([[1, 2], [3, 4]])
    result1 = matrix.sum()
    
    # Linear algebra
    det = linalg.det([[1, 2], [3, 4]])
    
<<<<<<< HEAD
    safe_print(f"   ✅ Sparse sum={result1}, det={det}")
=======
    safe_print(f"✅ SciPy 1.12 + numpy 1.26: sparse_sum={result1}, det={det}")
>>>>>>> development
    return result1, det

def test_scipy_modern():
    """Modern scipy with numpy 2.x"""
    import scipy
    import numpy as np
    from scipy import sparse
    from scipy.sparse import linalg as sparse_linalg
    
<<<<<<< HEAD
    safe_print(f"   SciPy version: {scipy.__version__}")
    safe_print(f"   NumPy version: {np.__version__}")
    
    # Create sparse matrix
    matrix = sparse.random(50, 50, density=0.1)
    result = matrix.sum()
    
    safe_print(f"   ✅ Sparse sum = {result:.4f}")
    return result
=======
    assert scipy.__version__.startswith('1.16'), f"Wrong scipy: {scipy.__version__}"
    assert np.__version__.startswith('2.3'), f"Wrong numpy: {np.__version__}"
    
    # Create larger sparse matrix
    matrix = sparse.random(100, 100, density=0.1)
    eigenvalues = sparse_linalg.eigs(matrix, k=1, return_eigenvectors=False)
    
    safe_print(f"✅ SciPy 1.16 + numpy 2.3: largest_eigenvalue={eigenvalues[0].real:.4f}")
    return eigenvalues[0]
>>>>>>> development

def print_with_old_rich():
    """Use Rich 13.4.2 for retro styling"""
    import rich
    from rich import print as rprint
    from rich.panel import Panel
    
<<<<<<< HEAD
    safe_print(f"   Rich version: {rich.__version__}")
=======
    assert rich.__version__ == '13.4.2', f"Wrong Rich: {rich.__version__}"
>>>>>>> development
    
    rprint(Panel("[bold cyan]This is from Rich 13.4.2![/bold cyan]", 
                 title="Old School", border_style="blue"))

def print_with_new_rich():
    """Use Rich 13.7.1 for modern styling"""
    import rich
    from rich import print as rprint
    from rich.panel import Panel
    
<<<<<<< HEAD
    safe_print(f"   Rich version: {rich.__version__}")
=======
    assert rich.__version__ == '13.7.1', f"Wrong Rich: {rich.__version__}"
>>>>>>> development
    
    rprint(Panel("[bold magenta]This is from Rich 13.7.1![/bold magenta]",
                 title="Modern", border_style="magenta"))

if __name__ == "__main__":
    safe_print("\n" + "="*70)
    safe_print("🌀 QUANTUM CHAOS TEST - The Impossible Script 🌀")
    safe_print("="*70 + "\n")
    
<<<<<<< HEAD
    safe_print("📊 Phase 1: Old TensorFlow (2.12) with ancient numpy...")
    with omnipkgLoader('tensorflow==2.12.0'):
        result1 = test_tensorflow_old()
    
    safe_print("\n📊 Phase 2: New TensorFlow (2.20) with modern numpy...")
    with omnipkgLoader('tensorflow==2.20.0'):
        result2 = test_tensorflow_new()
    
    safe_print("\n📊 Phase 3: Old SciPy (1.12) with numpy 1.26...")
    with omnipkgLoader('scipy==1.12.0'):
        scipy_result1 = test_scipy_old()
    
    safe_print("\n📊 Phase 4: Modern SciPy (1.16) with numpy 2.x...")
    with omnipkgLoader('scipy==1.16.1'):
        scipy_result2 = test_scipy_modern()
    
    safe_print("\n🎨 Phase 5: Old Rich (13.4.2) styling...")
    with omnipkgLoader('rich==13.4.2'):
        print_with_old_rich()
    
    safe_print("\n🎨 Phase 6: New Rich (13.7.1) styling...")
    with omnipkgLoader('rich==13.7.1'):
=======
    safe_print("📊 Phase 1: Old TensorFlow with ancient numpy...")
    with __import__('omnipkgLoader').omnipkgLoader(['tensorflow==2.12.0']):
        result1 = test_tensorflow_old()
    
    safe_print("\n📊 Phase 2: New TensorFlow with modern numpy...")
    with __import__('omnipkgLoader').omnipkgLoader(['tensorflow==2.20.0']):
        result2 = test_tensorflow_new()
    
    safe_print("\n📊 Phase 3: SciPy with numpy 1.26...")
    with __import__('omnipkgLoader').omnipkgLoader(['scipy==1.12.0', 'numpy==1.26.4']):
        scipy_result1 = test_scipy_combo()
    
    safe_print("\n📊 Phase 4: Modern SciPy with numpy 2.x...")
    with __import__('omnipkgLoader').omnipkgLoader(['scipy==1.16.1']):
        scipy_result2 = test_scipy_modern()
    
    safe_print("\n🎨 Phase 5: Old Rich styling...")
    with __import__('omnipkgLoader').omnipkgLoader(['rich==13.4.2']):
        print_with_old_rich()
    
    safe_print("\n🎨 Phase 6: New Rich styling...")
    with __import__('omnipkgLoader').omnipkgLoader(['rich==13.7.1']):
>>>>>>> development
        print_with_new_rich()
    
    safe_print("\n" + "="*70)
    safe_print("🎉 IMPOSSIBLE TEST PASSED!")
    safe_print(f"   TF 2.12 result: {result1:.4f}")
    safe_print(f"   TF 2.20 result: {result2:.4f}")
<<<<<<< HEAD
    safe_print(f"   SciPy 1.12 results: {scipy_result1}")
    safe_print(f"   SciPy 1.16 result: {scipy_result2:.4f}")
=======
    safe_print(f"   SciPy results: {scipy_result1}, {scipy_result2}")
>>>>>>> development
    safe_print("="*70 + "\n")
    
    safe_print("💀 This script should have destroyed pip, conda, docker, uv, poetry...")
    safe_print("✅ But Omnipkg just yawned and handled it in milliseconds.\n")