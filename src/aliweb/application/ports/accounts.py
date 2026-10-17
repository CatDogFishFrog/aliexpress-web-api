from __future__ import annotations

from typing import Protocol

from aliweb.domain.accounts import Account, AccountId


class AccountRepository(Protocol):
    """Сховище акаунтів (разом із сесіями та обліковими даними)."""

    async def list_ids(self) -> list[AccountId]: ...

    async def get(self, account_id: AccountId) -> Account | None: ...

    async def save(self, account: Account) -> None:
        """Створює або повністю замінює акаунт."""
        ...

    async def delete(self, account_id: AccountId) -> bool:
        """``True``, якщо акаунт існував."""
        ...


class ActiveAccountStore(Protocol):
    """Зберігає, який акаунт зараз активний (використовується для роботи API)."""

    async def get(self) -> AccountId | None: ...

    async def set(self, account_id: AccountId | None) -> None: ...
