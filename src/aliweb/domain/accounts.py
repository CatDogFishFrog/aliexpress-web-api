"""Account and session models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from aliweb.domain.errors import InvalidAccountIdError
from aliweb.domain.profile import BrowserProfile
from aliweb.domain.secret import Secret

#: Playwright storage_state format: ``{"cookies": [...], "origins": [...]}``.
type StorageState = dict[str, Any]

_ACCOUNT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True, slots=True)
class AccountId:
    """User-defined account identifier (ASCII letters, digits, ``_`` and ``-``, up to 32 characters).

    Used as a filename, so the format is strict to prevent path traversal.
    """

    value: str

    def __post_init__(self) -> None:
        if not _ACCOUNT_ID_RE.fullmatch(self.value):
            raise InvalidAccountIdError(
                f"Invalid account identifier {self.value!r}: allowed characters are a-z, 0-9, '_' and '-'; "
                "it must start with a letter or digit and be at most 32 characters long"
            )

    @classmethod
    def parse(cls, raw: str) -> AccountId:
        """Normalize (trim and lowercase) and validate user input."""
        return cls(raw.strip().lower())

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Credentials:
    """Account login (email or phone) and password."""

    login: str
    password: Secret

    @property
    def masked_login(self) -> str:
        return mask_login(self.login)

    def __repr__(self) -> str:
        return f"Credentials(login={self.masked_login!r}, password=Secret('***'))"


def mask_login(login: str) -> str:
    """``user@mail.com`` → ``u***@mail.com``; ``+380501234567`` → ``***67``."""
    login = login.strip()
    if "@" in login:
        local, _, domain = login.partition("@")
        return f"{local[:1]}***@{domain}"
    digits = re.sub(r"\D", "", login)
    return f"***{digits[-2:]}" if len(digits) >= 2 else "***"


@dataclass(frozen=True, slots=True)
class SessionState:
    """Saved browser session for an account."""

    storage_state: StorageState
    captured_at: datetime

    @property
    def cookies(self) -> list[dict[str, Any]]:
        raw = self.storage_state.get("cookies", [])
        return list(raw) if isinstance(raw, list) else []


@dataclass(frozen=True, slots=True)
class Account:
    """Account identifier, optional credentials, and current session.

    Immutable: update operations return a new instance.
    """

    id: AccountId
    created_at: datetime
    updated_at: datetime
    credentials: Credentials | None = None
    session: SessionState | None = None
    profile: BrowserProfile | None = None  # persistent browser environment for the account

    def with_profile(self, profile: BrowserProfile | None, *, now: datetime) -> Account:
        return replace(self, profile=profile, updated_at=now)

    def with_session(self, session: SessionState | None, *, now: datetime) -> Account:
        return replace(self, session=session, updated_at=now)

    def with_credentials(self, credentials: Credentials | None, *, now: datetime) -> Account:
        return replace(self, credentials=credentials, updated_at=now)

    def with_id(self, new_id: AccountId, *, now: datetime) -> Account:
        return replace(self, id=new_id, updated_at=now)


@dataclass(frozen=True, slots=True)
class AccountSummary:
    """Account summary safe for display (contains no secrets)."""

    id: AccountId
    masked_login: str | None
    has_session: bool
    session_captured_at: datetime | None
    is_active: bool = field(default=False)
    profile_label: str | None = None  # e.g. "windows 1920x1080"; None if no profile exists
