"""Process-lifetime locks: the OS releases ownership even after a crash."""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
from pathlib import Path

_HELD: dict[Path, int] = {}
_GUARD = threading.Lock()


def _lock(fd: int, unlock: bool = False) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


def acquire(path: Path, legacy_max_age_s: float) -> bool:
    path = path.resolve()
    with _GUARD:
        if path in _HELD:
            return False
        created = False
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            created = True
        except FileExistsError:
            fd = os.open(path, os.O_RDWR)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            return False
        try:
            old = os.read(fd, 4096).decode("utf-8", errors="replace")
            if not created and not old.startswith('{"process_lock":'):
                # Honor a fresh pre-upgrade lease; old programs do not hold an OS lock.
                if time.time() - os.fstat(fd).st_mtime < legacy_max_age_s:
                    _lock(fd, unlock=True)
                    os.close(fd)
                    return False
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps({"process_lock": 1, "pid": os.getpid()}).encode())
            _HELD[path] = fd
            return True
        except BaseException:
            _lock(fd, unlock=True)
            os.close(fd)
            raise


def release(path: Path) -> None:
    with _GUARD:
        fd = _HELD.pop(path.resolve(), None)
        if fd is not None:
            try:
                _lock(fd, unlock=True)
            finally:
                os.close(fd)
    # The inode stays put: unlinking can split waiting processes across two locks.


def parent_holds(path: Path) -> bool:
    """Allow only a direct pipeline child to share its parent's held lock."""
    parent = os.getppid()
    if os.environ.get("ZTT_FUNNEL_PARENT") != str(parent):
        return False
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            _lock(fd)
        except OSError:
            # Windows prevents reading the locked first byte; metadata follows it.
            os.lseek(fd, 1, os.SEEK_SET)
            owner = json.loads("{" + os.read(fd, 4096).decode())
            return owner.get("process_lock") == 1 and owner.get("pid") == parent
        _lock(fd, unlock=True)
        return False
    except (OSError, ValueError, UnicodeError):
        return False
    finally:
        os.close(fd)


def _release_all() -> None:
    for path in tuple(_HELD):
        release(path)


atexit.register(_release_all)
