from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aliweb.domain.accounts import Credentials, SessionState, StorageState
from aliweb.domain.browser import LoginMode


@dataclass(frozen=True, slots=True)
class LoginRequest:
    """Запит на вхід.

    ``mode=None`` — режим скрапера з конфігурації (браузер patchright). ``storage_state`` —
    попередні cookie (AliExpress тоді може запитати лише пароль). ``credentials`` можуть бути
    ``None`` лише для ручного входу (``mode.manual``).
    """

    credentials: Credentials | None
    mode: LoginMode | None = None
    storage_state: StorageState | None = None


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Результат входу. ``already_authenticated`` — сайт одразу визнав сесію дійсною (вхід не потрібен)."""

    session: SessionState
    already_authenticated: bool = False


class Authenticator(Protocol):
    """Виконує вхід/перевірку/вихід на конкретному сайті. Браузер — деталь реалізації."""

    async def login(self, request: LoginRequest) -> LoginResult:
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
