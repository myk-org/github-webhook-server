"""Safe rotating file handler that handles missing backup files gracefully.

Design Rationale - Broad OSError Suppression:
    This handler intentionally suppresses all OSError exceptions during rollover
    operations. This is a deliberate design choice based on the principle that
    **logging must NEVER crash the application**, even when file operations fail.

    Why broad suppression instead of specific exceptions:
    1. Race conditions: Files can be deleted between exists() check and operation
       (FileNotFoundError, but also PermissionError if replaced by protected file)
    2. Disk full: ENOSPC can occur mid-operation, but logging should continue
    3. Permission changes: Files may become unwritable during rotation
    4. Network filesystems: Various transient errors can occur on NFS/CIFS
    5. Container environments: Filesystem can change unexpectedly

    The alternative (letting errors propagate) would cause:
    - Application crashes from logging failures
    - Lost log entries for the actual application errors
    - Cascading failures in webhook processing

    Trade-off accepted: Some rotation failures may go unnoticed. This is acceptable
    because the primary log file will be recreated and logging will continue.
    The handler prioritizes availability over perfect rotation.
"""

from __future__ import annotations

import os
from logging.handlers import RotatingFileHandler


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that handles missing backup files during rollover.

    The standard RotatingFileHandler can crash with FileNotFoundError when
    backup log files are deleted externally (e.g., by logrotate, manual cleanup,
    or disk space management) during the rollover process.

    This implementation catches all OSError exceptions during doRollover and
    continues gracefully. This is intentional - logging infrastructure must
    never crash the application, even if rotation fails. See module docstring
    for detailed rationale.
    """

    def doRollover(self) -> None:
        """Perform log file rollover with graceful handling of file errors.

        Suppresses all OSError exceptions during rollover to ensure logging
        never crashes the application. This includes:
        - FileNotFoundError: Files deleted between check and operation
        - PermissionError: Files became unwritable
        - OSError: Disk full, network filesystem errors, etc.

        See module docstring for detailed rationale on this design choice.

        Note:
            This method does not log warnings internally to avoid recursion.
            Callers should perform external monitoring if explicit notification
            of rotation failures is required.
        """
        if self.stream:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]

        if self.backupCount > 0:
            # Remove backup files that exceed backupCount, handle missing files
            for i in range(self.backupCount - 1, 0, -1):
                sfn = self.rotation_filename(f"{self.baseFilename}.{i}")
                dfn = self.rotation_filename(f"{self.baseFilename}.{i + 1}")
                if os.path.exists(sfn):
                    try:
                        if os.path.exists(dfn):
                            os.remove(dfn)
                        os.rename(sfn, dfn)
                    except FileNotFoundError:
                        # File was deleted between exists check and operation - ignore
                        pass
                    except OSError:
                        # Broad suppression intentional: logging must never crash.
                        # See module docstring for full rationale.
                        pass

            dfn = self.rotation_filename(f"{self.baseFilename}.1")
            try:
                if os.path.exists(dfn):
                    os.remove(dfn)
            except FileNotFoundError:
                # File was deleted between exists check and remove - ignore
                pass
            except OSError:
                # Broad suppression intentional: logging must never crash.
                # See module docstring for full rationale.
                pass

            try:
                self.rotate(self.baseFilename, dfn)
            except FileNotFoundError:
                # Base file was deleted - just create a new one
                pass
            except OSError:
                # Broad suppression intentional: logging must never crash.
                # See module docstring for full rationale.
                pass

        if not self.delay:
            try:
                self.stream = self._open()
            except OSError:
                # Cannot open new log file - leave stream as None.
                # FileHandler.emit() will attempt to open on next log entry.
                pass
