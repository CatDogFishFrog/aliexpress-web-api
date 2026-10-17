from __future__ import annotations

from pathlib import Path

import pytest

from aliweb.application.codec import decode_account, encode_account
from aliweb.domain.accounts import AccountId
from aliweb.domain.errors import StorageError
from aliweb.infrastructure.storage.file_repository import (
    FileAccountRepository,
    FileActiveAccountStore,
)
from tests.conftest import fast_cipher, make_account, make_session


def test_codec_roundtrip_and_filters() -> None:
    account = make_account(session=make_session())
    decoded = decode_account(encode_account(account))
    assert decoded == account
    stripped = decode_account(encode_account(account, include_credentials=False))
    assert stripped.credentials is None and stripped.session == account.session


def test_codec_rejects_foreign_or_future_format() -> None:
    with pytest.raises(StorageError):
        decode_account(b'{"format":"other"}')
    with pytest.raises(StorageError):
        decode_account(b'{"format":"aliweb.account","version":99}')
    with pytest.raises(StorageError):
        decode_account(b"not json")


async def test_repository_crud(file_repo: FileAccountRepository) -> None:
    assert await file_repo.get(AccountId("a")) is None
    await file_repo.save(make_account("a", session=make_session()))
    await file_repo.save(make_account("b"))
    assert sorted(i.value for i in await file_repo.list_ids()) == ["a", "b"]
    assert (await file_repo.get(AccountId("a"))) == make_account("a", session=make_session())
    assert await file_repo.delete(AccountId("a")) is True
    assert await file_repo.delete(AccountId("a")) is False


async def test_files_are_encrypted_and_private(file_repo: FileAccountRepository, tmp_path: Path) -> None:
    await file_repo.save(make_account("a", session=make_session("COOKIE_VALUE_123")))
    path = tmp_path / "accounts" / "a.bin"
    raw = path.read_bytes()
    assert b"COOKIE_VALUE_123" not in raw and b"p@ss-word" not in raw and b"user@mail.com" not in raw
    assert (path.stat().st_mode & 0o077) == 0  # лише власник


async def test_wrong_key_or_corruption_is_storage_error(tmp_path: Path) -> None:
    repo = FileAccountRepository(tmp_path, fast_cipher())
    await repo.save(make_account("a"))
    from aliweb.domain.secret import Secret

    other = FileAccountRepository(tmp_path, fast_cipher(Secret("different-secret-long-enough")))
    with pytest.raises(StorageError):
        await other.get(AccountId("a"))


async def test_file_swap_detected(tmp_path: Path) -> None:
    """Файл, перейменований на чужий id, не має підмінити акаунт."""
    repo = FileAccountRepository(tmp_path, fast_cipher())
    await repo.save(make_account("a"))
    (tmp_path / "a.bin").rename(tmp_path / "b.bin")
    with pytest.raises(StorageError, match="очікувався"):
        await repo.get(AccountId("b"))


async def test_foreign_files_ignored(file_repo: FileAccountRepository, tmp_path: Path) -> None:
    await file_repo.save(make_account("a"))
    (tmp_path / "accounts" / "Bad Name!.bin").write_bytes(b"x")
    assert [i.value for i in await file_repo.list_ids()] == ["a"]


async def test_active_store(tmp_path: Path) -> None:
    store = FileActiveAccountStore(tmp_path / "active")
    assert await store.get() is None
    await store.set(AccountId("main"))
    assert await store.get() == AccountId("main")
    await store.set(None)
    assert await store.get() is None
