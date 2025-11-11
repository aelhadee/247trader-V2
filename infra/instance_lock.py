"""
Single Instance Lock - Prevent Multiple Bot Instances

Uses PID file locking to ensure only one instance of the trading bot
runs at a time, preventing:
- Double trading (exceeding risk limits)
- State corruption (concurrent writes)
- API rate limit exhaustion

The lock is automatically released on clean exit or crash.
"""

import os
import sys
import atexit
import signal
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SingleInstanceLock:
    """
    File-based single instance lock using PID files.
    
    Usage:
        lock = SingleInstanceLock("247trader-v2")
        if not lock.acquire():
            print("Another instance is running!")
            sys.exit(1)
        
        # Run bot...
        
        lock.release()  # Optional - auto-released on exit
    """
    
    def __init__(self, name: str, lock_dir: str = "data"):
        self.name = name
        self.lock_dir = Path(lock_dir)
        self.lock_file = self.lock_dir / f"{name}.pid"
        self.acquired = False
        
        # Ensure lock directory exists
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        
        # Register cleanup handlers
        atexit.register(self.release)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle termination signals"""
        logger.info(f"Received signal {signum}, releasing lock...")
        self.release()
        sys.exit(0)
    
    def _is_process_running(self, pid: int) -> bool:
        """Check if a process with given PID is running"""
        try:
            # Send signal 0 - doesn't kill, just checks if process exists
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    
    def acquire(self, force: bool = False) -> bool:
        """
        Acquire the lock.
        
        Args:
            force: If True, forcibly acquire lock even if another instance exists
                  (use with extreme caution - only for recovery)
        
        Returns:
            True if lock acquired, False if another instance is running
        """
        if self.acquired:
            logger.warning("Lock already acquired by this instance")
            return True
        
        # Check if lock file exists
        if self.lock_file.exists():
            try:
                # Read existing PID
                existing_pid = int(self.lock_file.read_text().strip())
                
                # Check if that process is still running
                if self._is_process_running(existing_pid):
                    if force:
                        logger.warning(
                            f"FORCE acquiring lock (killing instance PID={existing_pid})"
                        )
                        try:
                            os.kill(existing_pid, signal.SIGTERM)
                        except OSError:
                            pass
                    else:
                        logger.error(
                            f"Another instance is running (PID={existing_pid}). "
                            f"Cannot start. Lock file: {self.lock_file}"
                        )
                        return False
                else:
                    # Stale lock file (process died without cleanup)
                    logger.warning(
                        f"Found stale lock file (PID={existing_pid} not running), removing"
                    )
                    self.lock_file.unlink()
            
            except (ValueError, IOError) as e:
                logger.warning(f"Invalid lock file, removing: {e}")
                try:
                    self.lock_file.unlink()
                except Exception:
                    pass
        
        # Write our PID to lock file
        try:
            current_pid = os.getpid()
            self.lock_file.write_text(str(current_pid))
            self.acquired = True
            logger.info(f"Lock acquired (PID={current_pid}, file={self.lock_file})")
            return True
        
        except IOError as e:
            logger.error(f"Failed to create lock file: {e}")
            return False
    
    def release(self):
        """Release the lock (delete PID file)"""
        if not self.acquired:
            return
        
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
                logger.info(f"Lock released (file={self.lock_file})")
            self.acquired = False
        except Exception as e:
            logger.warning(f"Failed to release lock: {e}")
    
    def __enter__(self):
        """Context manager entry"""
        if not self.acquire():
            raise RuntimeError(f"Failed to acquire lock for {self.name}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.release()
        return False


def check_single_instance(name: str = "247trader-v2", 
                         lock_dir: str = "data") -> Optional[SingleInstanceLock]:
    """
    Convenience function to check and acquire single instance lock.
    
    Returns:
        SingleInstanceLock if successful, None if another instance is running
    
    Usage:
        lock = check_single_instance()
        if not lock:
            print("Another instance is running!")
            sys.exit(1)
    """
    lock = SingleInstanceLock(name, lock_dir)
    if lock.acquire():
        return lock
    return None
