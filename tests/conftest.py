"""Shared fixtures and fakes for tests without a browser or network."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aliweb.application.ports.auth import LoginRequest, LoginResult
from aliweb.domain.accounts import Account, AccountId, Credentials, SessionState
from aliweb.domain.profile import BrowserProfile, ClaimedOs
from aliweb.domain.secret import Secret
from aliweb.infrastructure.crypto.scrypt_aesgcm import ScryptAesGcmCipher, ScryptParams
from aliweb.infrastructure.storage.file_repository import FileAccountRepository

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
SECRET = Secret("test-secret-with-enough-length")


def fast_cipher(secret: Secret = SECRET) -> ScryptAesGcmCipher:
    """Fast scrypt configuration for tests using the minimum allowed N."""
    return ScryptAesGcmCipher(secret, ScryptParams(log2_n=12, r=8, p=1))


def make_session(cookie: str = "v1") -> SessionState:
    return SessionState(
        storage_state={"cookies": [{"name": "xman_us_t", "value": cookie}], "origins": []},
        captured_at=NOW,
    )


def make_profile(tag: str = "A", *, claimed_os: ClaimedOs = ClaimedOs.WINDOWS) -> BrowserProfile:
    """Minimal profile for service tests; ``tag`` distinguishes profiles."""
    return BrowserProfile(
        created_at=NOW,
        claimed_os=claimed_os,
        locale="en-US",
        timezone_id="Europe/Kyiv",
        viewport=(1536, 731),
        fingerprint={
            "navigator": {
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
                "hardwareConcurrency": 8,
                "deviceMemory": 8,
                "tag": tag,
            },
            "screen": {"width": 1536, "height": 864, "devicePixelRatio": 1.25},
            "videoCard": {"vendor": "Google Inc. (AMD)", "renderer": f"ANGLE (AMD {tag})"},
        },
    )


class FakeProfileGenerator:
    """Return profiles A, B, C, etc. in sequence and count calls."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self) -> BrowserProfile:
        self.calls += 1
        return make_profile(chr(ord("A") + self.calls - 1))


def make_account(
    name: str = "main",
    *,
    session: SessionState | None = None,
    profile: BrowserProfile | None = None,
) -> Account:
    return Account(
        id=AccountId(name),
        created_at=NOW,
        updated_at=NOW,
        credentials=Credentials("user@mail.com", Secret("p@ss-word")),
        session=session,
        profile=profile,
    )


class MemoryRepository:
    """In-memory implementation of AccountRepository."""

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
    """Configurable fake for the Authenticator port."""

    def __init__(self) -> None:
        self.valid = True
        self.login_calls: list[LoginRequest] = []
        self.validity_checks = 0
        self.validity_profiles: list[BrowserProfile | None] = []
        self.logout_calls = 0
        self.logout_error: Exception | None = None
        self.next_cookie = "fresh"
        self.already_authenticated = False

    async def login(self, request: LoginRequest) -> LoginResult:
        self.login_calls.append(request)
        return LoginResult(make_session(self.next_cookie), self.already_authenticated)

    async def is_session_valid(
        self,
        session: SessionState,
        *,
        headless: bool | None = None,
        profile: BrowserProfile | None = None,
    ) -> bool:
        self.validity_checks += 1
        self.validity_profiles.append(profile)
        return self.valid

    async def logout(self, session: SessionState) -> None:
        self.logout_calls += 1
        if self.logout_error:
            raise self.logout_error


@pytest.fixture
def profiles() -> FakeProfileGenerator:
    return FakeProfileGenerator()


@pytest.fixture
def repo() -> MemoryRepository:
    return MemoryRepository()


@pytest.fixture
def active() -> MemoryActiveStore:
    return MemoryActiveStore()


@pytest.fixture
def file_repo(tmp_path: Path) -> FileAccountRepository:
    return FileAccountRepository(tmp_path / "accounts", fast_cipher())
