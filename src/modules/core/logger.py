"""
LOG CAPTURE

Tees sys.stdout to both the terminal (when verbose) and the run's
logs.txt file. Writes are appended incrementally — nothing is buffered,
every printed line lands on disk as it happens.

Usage:
    capture = LogCapture(verbose=True, log_path="results/1/logs.txt")
    sys.stdout = capture
    ... run pipeline ...      # output appended to logs.txt live
    capture.close_log()       # close the file handle
    capture.restore()         # restore original sys.stdout
"""

import io
import os
import sys
from typing import IO, Optional


class LogCapture(io.TextIOBase):
    """File-like object that mirrors stdout to a live log file.

    Writes to ``_stdout`` (the real terminal) when ``verbose`` is True,
    and always appends each write to the log file at ``log_path``.
    """

    def __init__(self, verbose: bool = True, log_path: Optional[str] = None):
        super().__init__()
        self._stdout: IO = sys.stdout
        self.verbose = verbose
        self.log_path = log_path
        self._log_file: Optional[IO] = None
        if log_path is not None:
            os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
            self._log_file = open(log_path, "a", encoding="utf-8")

    # ------------------------------------------------------------------
    # IO overrides
    # ------------------------------------------------------------------
    def write(self, s: str) -> int:
        if self.verbose:
            self._stdout.write(s)
            self._stdout.flush()
        if self._log_file is not None:
            self._log_file.write(s)
            self._log_file.flush()
        return len(s)

    def flush(self) -> None:
        if self.verbose:
            self._stdout.flush()
        if self._log_file is not None:
            self._log_file.flush()

    def fileno(self) -> int:
        return self._stdout.fileno()

    def isatty(self) -> bool:
        return self._stdout.isatty()

    # ------------------------------------------------------------------
    # Capture management
    # ------------------------------------------------------------------
    def close_log(self) -> None:
        """Close the live log file (stop appending)."""
        if self._log_file is not None:
            self._log_file.flush()
            self._log_file.close()
            self._log_file = None

    def restore(self) -> None:
        """Restore the original sys.stdout."""
        sys.stdout = self._stdout