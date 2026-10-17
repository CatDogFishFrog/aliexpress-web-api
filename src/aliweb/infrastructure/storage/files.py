"""Безпечні файлові операції: атомарний запис із правами лише для власника."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from aliweb.domain.errors import StorageError


def ensure_private_dir(path: Path) -> None:
    """Створює теку з правами 0700 (на Windows chmod — best-effort)."""
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def atomic_write_private(path: Path, data: bytes) -> None:
    """Пише файл атомарно (tmp + replace) з правами 0600.

    Raises:
        StorageError: якщо запис не вдався.
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
        raise StorageError(f"Не вдалося записати файл {path}: {exc}") from exc


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise StorageError(f"Не вдалося прочитати файл {path}: {exc}") from exc
