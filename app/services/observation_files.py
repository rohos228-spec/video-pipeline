"""Local body access protection, independently testable without app/settings."""
import os
from pathlib import Path


def secure_directory(path: Path):
    if os.name != "nt":
        os.chmod(path, 0o700)
        return
    import ctypes
    from ctypes import wintypes

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = security.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    apply = security.SetFileSecurityW
    apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    apply.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    # Protected DACL: owner rights and SYSTEM only; children inherit this ACL.
    if not convert("D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)", 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not apply(str(path), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)
