from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aliweb.domain.accounts import Credentials, SessionState, StorageState


@dataclass(frozen=True, slots=True)
class LoginRequest:
    """Запит на вхід.

    ``headless=None`` — використати значення з конфігурації. ``storage_state`` — попередні
    cookie (AliExpress тоді може запитати лише пароль).
    """

    credentials: Credentials
    headless: bool | None = None
    storage_state: StorageState | None = None


class Authenticator(Protocol):
    """Виконує вхід/перевірку/вихід на конкретному сайті. Браузер — деталь реалізації."""

    async def login(self, request: LoginRequest) -> SessionState:
        """Raises:
        AuthenticationError: нащадки (ризик-контроль, капча, 2FA тощо).
        """
        ...

    async def is_session_valid(self, session: SessionState, *, headless: bool | None = None) -> bool: ...

    async def logout(self, session: SessionState) -> None:
        """Best-effort вихід на боці сайту. Локальне очищення робить сервіс."""
        ...


class TwoFactorPrompt(Protocol):
    """Джерело коду 2FA (консоль, Telegram, веб-форма…)."""

    async def ask_code(self, hint: str) -> str | None:
        """Повертає код або ``None``.

        Raises:
            TwoFactorRequiredError: якщо інтерактивне введення неможливе.
        """
        ...
