"""Secure file operations: atomic writes with owner-only permissions."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from aliweb.domain.errors import StorageError


def ensure_private_dir(path: Path) -> None:
    """Create a directory with mode 0700; chmod is best-effort on Windows."""
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def atomic_write_private(path: Path, data: bytes) -> None:
    """Atomically write a file (temp file + replace) with mode 0600.

    Raises:
        StorageError: if the write fails.
    """
    ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(OSError):
            tmp.chmod(0o600)
        tmp.replace(path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise StorageError(f"Failed to write file {path}: {exc}") from exc


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise StorageError(f"Failed to read file {path}: {exc}") from exc
