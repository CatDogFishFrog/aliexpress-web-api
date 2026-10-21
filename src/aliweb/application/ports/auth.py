from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aliweb.domain.accounts import Credentials, SessionState, StorageState
from aliweb.domain.browser import LoginMode
from aliweb.domain.profile import BrowserProfile


@dataclass(frozen=True, slots=True)
class LoginRequest:
    """Login request.

    ``mode=None`` uses the scraper configuration (patchright browser). ``storage_state`` contains
    existing cookies; AliExpress may then request only the password. ``credentials`` may be ``None``
    only for manual login (``mode.manual``).
    """

    credentials: Credentials | None
    mode: LoginMode | None = None
    storage_state: StorageState | None = None
    profile: BrowserProfile | None = None  # Account browser environment (UA, fingerprint, etc.).


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Login result. ``already_authenticated`` indicates the site accepted the session without logging in."""

    session: SessionState
    already_authenticated: bool = False


class Authenticator(Protocol):
    """Perform login, session verification, and logout for a site; browser details are internal."""

    async def login(self, request: LoginRequest) -> LoginResult:
        """Raises:
        AuthenticationError: subclasses such as risk-control, captcha, or 2FA errors.
        """
        ...

    async def is_session_valid(
        self,
        session: SessionState,
        *,
        headless: bool | None = None,
        profile: BrowserProfile | None = None,
    ) -> bool: ...

    async def logout(self, session: SessionState) -> None:
        """Best-effort remote logout. The service clears local state."""
        ...


class TwoFactorPrompt(Protocol):
    """Source of 2FA codes (console, Telegram, web form, etc.)."""

    async def ask_code(self, hint: str) -> str | None:
        """Return a code or ``None``.

        Raises:
            TwoFactorRequiredError: if interactive input is unavailable.
        """
        ...
