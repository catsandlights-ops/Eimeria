"""Windows helpers: stop the PC from going to sleep while the bot is running."""
from __future__ import annotations

import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake(on: bool) -> bool:
    """Ask Windows not to sleep (the screen may still turn off; that's fine).
    Returns True if it worked. Does nothing on other operating systems."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED if on else ES_CONTINUOUS
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:
        return False
