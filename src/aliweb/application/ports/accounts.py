from __future__ import annotations

from typing import Protocol

from aliweb.domain.accounts import Account, AccountId


class AccountRepository(Protocol):
    """Account storage, including sessions and credentials."""

    async def list_ids(self) -> list[AccountId]: ...

    async def get(self, account_id: AccountId) -> Account | None: ...

    async def save(self, account: Account) -> None:
        """Create or completely replace an account."""
        ...

    async def delete(self, account_id: AccountId) -> bool:
        """Return ``True`` if the account existed."""
        ...


class ActiveAccountStore(Protocol):
    """Store which account is active for API operations."""

    async def get(self) -> AccountId | None: ...

    async def set(self, account_id: AccountId | None) -> None: ...
