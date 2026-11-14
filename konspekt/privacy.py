"""Documented Windows window-capture exclusion; never hides a process."""
import ctypes
import sys
from ctypes import wintypes


def protect_window(handle: int, enabled: bool) -> bool:
    if sys.platform != "win32":
        return not enabled
    fn = ctypes.WinDLL("user32", use_last_error=True).SetWindowDisplayAffinity
    fn.argtypes = [wintypes.HWND, wintypes.DWORD]
    fn.restype = wintypes.BOOL
    applied = bool(fn(handle, 0x11 if enabled else 0))
    return applied or not enabled
