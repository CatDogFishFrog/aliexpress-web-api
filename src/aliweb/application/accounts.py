"""Account use cases: add, remove, rename, and select the active account."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from aliweb.application.ports.accounts import AccountRepository, ActiveAccountStore
from aliweb.application.ports.profiles import ProfileGenerator
from aliweb.domain.accounts import Account, AccountId, AccountSummary, Credentials
from aliweb.domain.errors import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    NoActiveAccountError,
)

type Clock = Callable[[], datetime]

_log = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


class AccountService:
    """Manage accounts and the active account pointer."""

    def __init__(
        self,
        repository: AccountRepository,
        active_store: ActiveAccountStore,
        profiles: ProfileGenerator,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._repo = repository
        self._active = active_store
        self._profiles = profiles
        self._clock = clock

    async def add(self, account_id: AccountId, credentials: Credentials | None = None) -> Account:
        """Add an account and make it active if no active account exists."""
        if await self._repo.get(account_id) is not None:
            raise AccountAlreadyExistsError(f"Account {account_id} already exists")
        now = self._clock()
        account = Account(id=account_id, created_at=now, updated_at=now, credentials=credentials)
        await self._repo.save(account)
        if await self._active.get() is None:
            await self._active.set(account_id)
        return account

    async def get(self, account_id: AccountId) -> Account:
        account = await self._repo.get(account_id)
        if account is None:
            raise AccountNotFoundError(f"Account {account_id} was not found")
        return account

    async def ensure_profile(self, account_id: AccountId) -> Account:
        """Ensure the account has a browser profile, creating and saving one if needed.

        The profile is created once and remains unchanged so the account always uses the same
        environment (UA, fingerprint, locale, timezone, and window size).
        """
        account = await self.get(account_id)
        if account.profile is not None:
            return account
        # Generate the profile off-thread to keep the event loop responsive.
        profile = await asyncio.to_thread(self._profiles.generate)
        updated = account.with_profile(profile, now=self._clock())
        await self._repo.save(updated)
        _log.info("Created browser profile", extra={"account": str(account_id), "profile": profile.label})
        return updated

    async def regenerate_profile(self, account_id: AccountId) -> Account:
        """Force creation of a NEW profile, replacing the old one. The existing session remains,
        but it was created in a different environment; logging in again is usually recommended
        (``login --fresh``)."""
        account = await self.get(account_id)
        profile = await asyncio.to_thread(self._profiles.generate)
        updated = account.with_profile(profile, now=self._clock())
        await self._repo.save(updated)
        return updated

    async def update_credentials(self, account_id: AccountId, credentials: Credentials | None) -> Account:
        account = (await self.get(account_id)).with_credentials(credentials, now=self._clock())
        await self._repo.save(account)
        return account

    async def remove(self, account_id: AccountId) -> None:
        if not await self._repo.delete(account_id):
            raise AccountNotFoundError(f"Account {account_id} was not found")
        if await self._active.get() == account_id:
            await self._active.set(None)

    async def rename(self, old_id: AccountId, new_id: AccountId) -> Account:
        """Rename an account, preserving its credentials and session, and update the active pointer."""
        if old_id == new_id:
            return await self.get(old_id)
        account = await self.get(old_id)
        if await self._repo.get(new_id) is not None:
            raise AccountAlreadyExistsError(f"Account {new_id} already exists")
        renamed = account.with_id(new_id, now=self._clock())
        await self._repo.save(renamed)  # Save the new account first to avoid data loss.
        await self._repo.delete(old_id)
        if await self._active.get() == old_id:
            await self._active.set(new_id)
        return renamed

    async def set_active(self, account_id: AccountId) -> None:
        await self.get(account_id)  # Verify that the account exists.
        await self._active.set(account_id)

    async def get_active_id(self) -> AccountId | None:
        active = await self._active.get()
        if active is not None and await self._repo.get(active) is None:
            await self._active.set(None)  # Clear a stale pointer if the file was removed externally.
            return None
        return active

    async def require_active_id(self) -> AccountId:
        active = await self.get_active_id()
        if active is None:
            raise NoActiveAccountError(
                "No active account selected. Add an account and select it (`aliweb accounts use <id>`)"
            )
        return active

    async def list_summaries(self) -> list[AccountSummary]:
        active = await self.get_active_id()
        summaries: list[AccountSummary] = []
        for account_id in sorted(await self._repo.list_ids(), key=lambda a: a.value):
            account = await self._repo.get(account_id)
            if account is None:
                continue
            summaries.append(
                AccountSummary(
                    id=account.id,
                    masked_login=account.credentials.masked_login if account.credentials else None,
                    has_session=account.session is not None,
                    session_captured_at=account.session.captured_at if account.session else None,
                    is_active=account.id == active,
                    profile_label=account.profile.label if account.profile else None,
                )
            )
        return summaries
