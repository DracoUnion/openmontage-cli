"""OpenMontage command-line interface package."""

__version__ = "0.1.0"

# Force UTF-8 for stdio and spawned subprocesses (Windows default is often GBK).
from .utils.utf8 import configure_utf8 as _configure_utf8

_configure_utf8()
