"""Authentication use cases: login, logout, and ensuring a valid session."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from aliweb.application.accounts import AccountService, Clock, utc_now
from aliweb.application.ports.accounts import AccountRepository
from aliweb.application.ports.auth import Authenticator, LoginRequest, LoginResult
from aliweb.domain.accounts import AccountId, SessionState
from aliweb.domain.browser import LoginMode
from aliweb.domain.errors import AliWebError, MissingCredentialsError

_log = logging.getLogger(__name__)


class AuthService:
    """Orchestrate account login and logout through :class:`Authenticator`.

    Browser details remain behind the ``Authenticator`` port.
    """

    def __init__(
        self,
        accounts: AccountService,
        repository: AccountRepository,
        authenticator: Authenticator,
        *,
        verify_ttl_s: float = 600.0,
        clock: Clock = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._accounts = accounts
        self._repo = repository
        self._auth = authenticator
        self._verify_ttl_s = verify_ttl_s
        self._clock = clock
        self._monotonic = monotonic
        self._verified_at: dict[AccountId, float] = {}

    async def login(
        self, account_id: AccountId, *, mode: LoginMode | None = None, fresh: bool = False
    ) -> LoginResult:
        """Log in to an account and save its session.

        Args:
            mode: LoginMode and browser choice; ``None`` uses the scraper configuration.
                Credentials are not required for manual login (``mode.manual``).
            fresh: Ignore any existing session and start a fresh login.

        Returns:
            ``LoginResult``; ``already_authenticated=True`` if the site accepts the saved session
            without performing another login.
        """
        account = await self._accounts.ensure_profile(account_id)  # Fix the account environment here.
        manual = mode is not None and mode.manual
        if account.credentials is None and not manual:
            raise MissingCredentialsError(
                f"Account {account_id} has no saved login credentials, so automatic login is unavailable. "
                "Set them with (`aliweb accounts update`) or log in manually: "
                "`aliweb login <id> --headful --manual`"
            )
        previous = None if fresh or account.session is None else account.session.storage_state
        result = await self._auth.login(
            LoginRequest(
                credentials=account.credentials,
                mode=mode,
                storage_state=previous,
                profile=account.profile,
            )
        )
        await self._repo.save(account.with_session(result.session, now=self._clock()))
        self._verified_at[account_id] = self._monotonic()
        _log.info(
            "Login completed",
            extra={"account": str(account_id), "already_authenticated": result.already_authenticated},
        )
        return result

    async def logout(self, account_id: AccountId) -> None:
        """Log out on the site on a best-effort basis and always clear the stored session."""
        account = await self._accounts.get(account_id)
        if account.session is not None:
            try:
                await self._auth.logout(account.session)
            except AliWebError:
                _log.warning(
                    "Remote logout failed; cleared the local session",
                    extra={"account": str(account_id)},
                    exc_info=True,
                )
        await self._repo.save(account.with_session(None, now=self._clock()))
        self._verified_at.pop(account_id, None)

    async def ensure_session(self, account_id: AccountId, *, headless: bool | None = None) -> SessionState:
        """Return a valid session by verifying the current one or logging in again."""
        account = await self._accounts.ensure_profile(account_id)
        session = account.session
        relogin_mode = None if headless is None else LoginMode(headless=headless)
        if session is None:
            return (await self.login(account_id, mode=relogin_mode)).session

        verified = self._verified_at.get(account_id)
        if verified is not None and self._monotonic() - verified < self._verify_ttl_s:
            return session

        if await self._auth.is_session_valid(session, headless=headless, profile=account.profile):
            self._verified_at[account_id] = self._monotonic()
            return session

        _log.info("Saved session is invalid; logging in again", extra={"account": str(account_id)})
        return (await self.login(account_id, mode=relogin_mode)).session

    async def check(self, account_id: AccountId, *, headless: bool | None = None) -> bool:
        """Force verification of the saved session without logging in again."""
        if (await self._accounts.get(account_id)).session is None:
            return False
        # Use the environment associated with the session.
        account = await self._accounts.ensure_profile(account_id)
        session = account.session
        if session is None:  # For typing only; the session was checked above.
            return False
        valid = await self._auth.is_session_valid(session, headless=headless, profile=account.profile)
        if valid:
            self._verified_at[account_id] = self._monotonic()
        else:
            self._verified_at.pop(account_id, None)
        return valid

    def invalidate_verification(self, account_id: AccountId) -> None:
        """Clear the verification cache, for example when a page indicates an expired session."""
        self._verified_at.pop(account_id, None)
