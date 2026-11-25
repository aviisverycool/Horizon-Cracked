"""Persist the launcher session using the user's operating-system credentials."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import keyring


SERVICE = "Crackrizon.Ely.by"


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def _crypt(data, protect):
    source = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(source, ctypes.c_void_p))
    outgoing = Blob()
    api = ctypes.WinDLL("crypt32", use_last_error=True)
    call = api.CryptProtectData if protect else api.CryptUnprotectData
    if not call(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(outgoing.data))


def read(directory):
    path = directory / "ely-session.dat"
    if os.name == "nt" and path.is_file():
        return json.loads(_crypt(path.read_bytes(), False))
    raw = keyring.get_password(SERVICE, "session")
    return json.loads(raw) if raw else None


def save(directory, record):
    if os.name != "nt":
        if record is None:
            try:
                keyring.delete_password(SERVICE, "session")
            except keyring.errors.PasswordDeleteError:
                if keyring.get_password(SERVICE, "session") is not None:
                    raise
        else:
            keyring.set_password(SERVICE, "session", json.dumps(record))
        return
    directory.mkdir(parents=True, exist_ok=True)
    data = _crypt(json.dumps(record).encode(), True)
    file = NamedTemporaryFile(dir=directory, delete=False)
    temporary = Path(file.name)
    try:
        with file:
            file.write(data)
        temporary.replace(directory / "ely-session.dat")
    finally:
        temporary.unlink(missing_ok=True)
    if record is None:
        # The encrypted signed-out marker also takes precedence over any legacy entry.
        try:
            keyring.delete_password(SERVICE, "session")
        except (keyring.errors.KeyringError, OSError):
            pass
