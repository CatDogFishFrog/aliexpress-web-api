"""Моделі акаунтів і сесій."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from aliweb.domain.errors import InvalidAccountIdError
from aliweb.domain.secret import Secret

#: Формат storage_state Playwright: ``{"cookies": [...], "origins": [...]}``.
type StorageState = dict[str, Any]

_ACCOUNT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True, slots=True)
class AccountId:
    """Користувацька назва-ідентифікатор акаунта (латиниця, цифри, ``_`` і ``-``, до 32 символів).

    Використовується як ім'я файлу, тому формат суворий (захист від path traversal).
    """

    value: str

    def __post_init__(self) -> None:
        if not _ACCOUNT_ID_RE.fullmatch(self.value):
            raise InvalidAccountIdError(
                f"Некоректний ідентифікатор акаунта {self.value!r}: дозволено a-z, 0-9, '_' і '-', "
                "починається з літери/цифри, до 32 символів"
            )

    @classmethod
    def parse(cls, raw: str) -> AccountId:
        """Нормалізує (trim + lower-case) і валідує введення користувача."""
        return cls(raw.strip().lower())

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Credentials:
    """Логін (e-mail/телефон) і пароль акаунта."""

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
    """Збережена браузерна сесія акаунта."""

    storage_state: StorageState
    captured_at: datetime

    @property
    def cookies(self) -> list[dict[str, Any]]:
        raw = self.storage_state.get("cookies", [])
        return list(raw) if isinstance(raw, list) else []


@dataclass(frozen=True, slots=True)
class Account:
    """Акаунт: ідентифікатор, (необов'язково) облікові дані та поточна сесія.

    Незмінний: «змінюючі» операції повертають нову копію.
    """

    id: AccountId
    created_at: datetime
    updated_at: datetime
    credentials: Credentials | None = None
    session: SessionState | None = None

    def with_session(self, session: SessionState | None, *, now: datetime) -> Account:
        return replace(self, session=session, updated_at=now)

    def with_credentials(self, credentials: Credentials | None, *, now: datetime) -> Account:
        return replace(self, credentials=credentials, updated_at=now)

    def with_id(self, new_id: AccountId, *, now: datetime) -> Account:
        return replace(self, id=new_id, updated_at=now)


@dataclass(frozen=True, slots=True)
class AccountSummary:
    """Безпечне для виводу представлення акаунта (без секретів)."""

    id: AccountId
    masked_login: str | None
    has_session: bool
    session_captured_at: datetime | None
    is_active: bool = field(default=False)
