import fcntl
import time
from pathlib import Path
from contextlib import contextmanager

class OmnipkgLockManager:
    """Process-safe locking for omnipkg operations."""
    
    def __init__(self, config_manager):
        self.lock_dir = Path(config_manager.config['multiversion_base']) / '.locks'
        self.lock_dir.mkdir(parents=True, exist_ok=True)
    
    @contextmanager
    def acquire_lock(self, lock_name: str, timeout: float = 300.0):
        """
        Acquire an exclusive lock for critical operations.
        
        Args:
            lock_name: Name of the lock (e.g., 'config', 'kb_update', 'install')
            timeout: Max seconds to wait for lock
        """
        lock_file = self.lock_dir / f'{lock_name}.lock'
        lock_fd = None
        start_time = time.time()
        
        try:
            # Open/create lock file
            lock_fd = open(lock_file, 'w')
            
            # Try to acquire lock with timeout
            while True:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break  # Lock acquired!
                except BlockingIOError:
                    if time.time() - start_time > timeout:
                        raise TimeoutError(f"Failed to acquire '{lock_name}' lock after {timeout}s")
                    safe_print(f"⏳ Waiting for {lock_name} lock...")
                    time.sleep(0.1)
            
            yield  # Critical section runs here
            
        finally:
            if lock_fd:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()