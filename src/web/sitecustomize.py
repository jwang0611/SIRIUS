"""
Site customization for Windows encoding support.
This module is auto-imported when Python starts if placed in the path.
"""

import contextlib
import os
import sys

# Force UTF-8 encoding for stdout/stderr on Windows
if sys.platform == "win32":
    # Set environment variable for child processes
    os.environ["PYTHONIOENCODING"] = "utf-8"

    # Reconfigure stdout/stderr to use UTF-8 with error replacement
    if hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
