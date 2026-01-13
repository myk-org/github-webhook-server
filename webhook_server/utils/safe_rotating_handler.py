"""Safe rotating file handler that handles missing backup files gracefully."""

from __future__ import annotations

import os
from logging.handlers import RotatingFileHandler


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that handles missing backup files during rollover.

    The standard RotatingFileHandler can crash with FileNotFoundError when
    backup log files are deleted externally (e.g., by logrotate, manual cleanup,
    or disk space management) during the rollover process.

    This implementation catches FileNotFoundError during doRollover and continues
    gracefully, ensuring logging is not interrupted by missing backup files.
    """

    def doRollover(self) -> None:
        """Perform log file rollover with graceful handling of missing files.

        Catches FileNotFoundError that can occur when backup files are missing
        and logs a warning instead of crashing.
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
                        # Other OS errors (permissions, etc.) - continue gracefully
                        pass

            dfn = self.rotation_filename(f"{self.baseFilename}.1")
            try:
                if os.path.exists(dfn):
                    os.remove(dfn)
            except FileNotFoundError:
                pass
            except OSError:
                pass

            try:
                self.rotate(self.baseFilename, dfn)
            except FileNotFoundError:
                # Base file was deleted - just create a new one
                pass
            except OSError:
                pass

        if not self.delay:
            self.stream = self._open()
