"""UTF-8 process default setup.

On Windows, Python's default text encoding is often the locale (GBK), which
raises ``UnicodeDecodeError`` in subprocess reader threads when a child emits
non-ASCII UTF-8. This module forces UTF-8 as the process default for stdio and
for Python subprocesses spawned afterwards.

Call ``configure_utf8()`` at process entry points (CLI ``main`` and backlot
``main``). It is idempotent.
"""

from __future__ import annotations

import os
import sys

_configured = False


def configure_utf8() -> None:
    """Set UTF-8 as the process default for stdio and spawned interpreters."""
    global _configured
    if _configured:
        return
    _configured = True

    # Child Python interpreters inherit UTF-8 mode / UTF-8 stdio.
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    # Reconfigure the current process's stdio to write UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


__all__ = ["configure_utf8"]
