"""Файлове сховище акаунтів: один зашифрований файл ``<id>.bin`` на акаунт."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from aliweb.application.codec import decode_account, encode_account
from aliweb.application.ports.crypto import Cipher
from aliweb.domain.accounts import Account, AccountId
from aliweb.domain.errors import AliWebError, InvalidAccountIdError, StorageError
from aliweb.infrastructure.storage.files import atomic_write_private, ensure_private_dir, read_bytes

#: AAD прив'язує шифротекст до типу файлу сховища (а не до експортного пакета).
VAULT_AAD = b"aliweb.vault.v1"
_SUFFIX = ".bin"


class FileAccountRepository:
    """Реалізує ``AccountRepository``. Припущення: один процес (див. memory/04)."""

    def __init__(self, root: Path, cipher: Cipher) -> None:
        self._root = root
        self._cipher = cipher
        self._lock = asyncio.Lock()

    def _path(self, account_id: AccountId) -> Path:
        return self._root / f"{account_id.value}{_SUFFIX}"

    async def list_ids(self) -> list[AccountId]:
        if not self._root.is_dir():
            return []
        ids: list[AccountId] = []
        for path in self._root.glob(f"*{_SUFFIX}"):
            with contextlib.suppress(InvalidAccountIdError):
                ids.append(AccountId(path.stem))  # сторонні файли ігноруємо
        return ids

    async def get(self, account_id: AccountId) -> Account | None:
        path = self._path(account_id)
        if not path.is_file():
            return None
        try:
            blob = await asyncio.to_thread(read_bytes, path)
            plaintext = await asyncio.to_thread(self._cipher.decrypt, blob, aad=VAULT_AAD)
            account = decode_account(plaintext)
        except AliWebError as exc:
            raise StorageError(f"Не вдалося завантажити акаунт {account_id}: {exc}") from exc
        if account.id != account_id:
            raise StorageError(f"Файл {path.name} містить акаунт {account.id} (очікувався {account_id})")
        return account

    async def save(self, account: Account) -> None:
        plaintext = encode_account(account)
        async with self._lock:
            ensure_private_dir(self._root)
            blob = await asyncio.to_thread(self._cipher.encrypt, plaintext, aad=VAULT_AAD)
            await asyncio.to_thread(atomic_write_private, self._path(account.id), blob)

    async def delete(self, account_id: AccountId) -> bool:
        path = self._path(account_id)
        async with self._lock:
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise StorageError(f"Не вдалося видалити {path}: {exc}") from exc
            return True


class FileActiveAccountStore:
    """Реалізує ``ActiveAccountStore``: id активного акаунта в простому файлі (без секретів)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def get(self) -> AccountId | None:
        if not self._path.is_file():
            return None
        raw = (await asyncio.to_thread(read_bytes, self._path)).decode("utf-8").strip()
        try:
            return AccountId.parse(raw) if raw else None
        except InvalidAccountIdError:
            return None

    async def set(self, account_id: AccountId | None) -> None:
        if account_id is None:
            with contextlib.suppress(FileNotFoundError):
                self._path.unlink()
            return
        await asyncio.to_thread(atomic_write_private, self._path, account_id.value.encode("utf-8"))
