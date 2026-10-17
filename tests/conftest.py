"""Спільні фікстури та фейки для тестів (без браузера й мережі)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aliweb.application.ports.auth import LoginRequest
from aliweb.domain.accounts import Account, AccountId, Credentials, SessionState
from aliweb.domain.secret import Secret
from aliweb.infrastructure.crypto.scrypt_aesgcm import ScryptAesGcmCipher, ScryptParams
from aliweb.infrastructure.storage.file_repository import FileAccountRepository

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
SECRET = Secret("test-secret-with-enough-length")


def fast_cipher(secret: Secret = SECRET) -> ScryptAesGcmCipher:
    """Швидкий scrypt для тестів (мінімально допустимий N)."""
    return ScryptAesGcmCipher(secret, ScryptParams(log2_n=12, r=8, p=1))


def make_session(cookie: str = "v1") -> SessionState:
    return SessionState(
        storage_state={"cookies": [{"name": "xman_us_t", "value": cookie}], "origins": []},
        captured_at=NOW,
    )


def make_account(name: str = "main", *, session: SessionState | None = None) -> Account:
    return Account(
        id=AccountId(name),
        created_at=NOW,
        updated_at=NOW,
        credentials=Credentials("user@mail.com", Secret("p@ss-word")),
        session=session,
    )


class MemoryRepository:
    """Реалізація AccountRepository у пам'яті."""

    def __init__(self) -> None:
        self.items: dict[AccountId, Account] = {}

    async def list_ids(self) -> list[AccountId]:
        return list(self.items)

    async def get(self, account_id: AccountId) -> Account | None:
        return self.items.get(account_id)

    async def save(self, account: Account) -> None:
        self.items[account.id] = account

    async def delete(self, account_id: AccountId) -> bool:
        return self.items.pop(account_id, None) is not None


class MemoryActiveStore:
    def __init__(self) -> None:
        self.value: AccountId | None = None

    async def get(self) -> AccountId | None:
        return self.value

    async def set(self, account_id: AccountId | None) -> None:
        self.value = account_id


class FakeAuthenticator:
    """Керований фейк порту Authenticator."""

    def __init__(self) -> None:
        self.valid = True
        self.login_calls: list[LoginRequest] = []
        self.validity_checks = 0
        self.logout_calls = 0
        self.logout_error: Exception | None = None
        self.next_cookie = "fresh"

    async def login(self, request: LoginRequest) -> SessionState:
        self.login_calls.append(request)
        return make_session(self.next_cookie)

    async def is_session_valid(self, session: SessionState, *, headless: bool | None = None) -> bool:
        self.validity_checks += 1
        return self.valid

    async def logout(self, session: SessionState) -> None:
        self.logout_calls += 1
        if self.logout_error:
            raise self.logout_error


@pytest.fixture
def repo() -> MemoryRepository:
    return MemoryRepository()


@pytest.fixture
def active() -> MemoryActiveStore:
    return MemoryActiveStore()


@pytest.fixture
def file_repo(tmp_path: Path) -> FileAccountRepository:
    return FileAccountRepository(tmp_path / "accounts", fast_cipher())
