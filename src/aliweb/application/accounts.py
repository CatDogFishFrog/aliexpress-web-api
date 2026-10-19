"""Сценарії керування акаунтами: додати, видалити, перейменувати, обрати активний."""

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
    """Керує набором акаунтів і вказівником на активний."""

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
        """Додає акаунт. Якщо активного ще немає — новий стає активним."""
        if await self._repo.get(account_id) is not None:
            raise AccountAlreadyExistsError(f"Акаунт {account_id} уже існує")
        now = self._clock()
        account = Account(id=account_id, created_at=now, updated_at=now, credentials=credentials)
        await self._repo.save(account)
        if await self._active.get() is None:
            await self._active.set(account_id)
        return account

    async def get(self, account_id: AccountId) -> Account:
        account = await self._repo.get(account_id)
        if account is None:
            raise AccountNotFoundError(f"Акаунт {account_id} не знайдено")
        return account

    async def ensure_profile(self, account_id: AccountId) -> Account:
        """Гарантує, що в акаунта є профіль браузерного середовища (створює й зберігає, якщо немає).

        Профіль створюється ОДИН раз і далі не змінюється — щоб акаунт завжди працював в одному й тому
        самому «середовищі» (UA, відбиток, мова, часовий пояс, розмір вікна).
        """
        account = await self.get(account_id)
        if account.profile is not None:
            return account
        profile = await asyncio.to_thread(self._profiles.generate)  # CPU-робота, не блокуємо цикл подій
        updated = account.with_profile(profile, now=self._clock())
        await self._repo.save(updated)
        _log.info("Створено профіль середовища", extra={"account": str(account_id), "profile": profile.label})
        return updated

    async def regenerate_profile(self, account_id: AccountId) -> Account:
        """Примусово створює НОВИЙ профіль (старий втрачається). Існуюча сесія лишається, але вона
        створена в іншому середовищі — зазвичай варто увійти заново (``login --fresh``)."""
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
            raise AccountNotFoundError(f"Акаунт {account_id} не знайдено")
        if await self._active.get() == account_id:
            await self._active.set(None)

    async def rename(self, old_id: AccountId, new_id: AccountId) -> Account:
        """Перейменовує акаунт (зберігає облікові дані й сесію), оновлює активний."""
        if old_id == new_id:
            return await self.get(old_id)
        account = await self.get(old_id)
        if await self._repo.get(new_id) is not None:
            raise AccountAlreadyExistsError(f"Акаунт {new_id} уже існує")
        renamed = account.with_id(new_id, now=self._clock())
        await self._repo.save(renamed)  # спочатку зберігаємо нове — втрати даних не буде
        await self._repo.delete(old_id)
        if await self._active.get() == old_id:
            await self._active.set(new_id)
        return renamed

    async def set_active(self, account_id: AccountId) -> None:
        await self.get(account_id)  # перевірка існування
        await self._active.set(account_id)

    async def get_active_id(self) -> AccountId | None:
        active = await self._active.get()
        if active is not None and await self._repo.get(active) is None:
            await self._active.set(None)  # вказівник «завис» (файл видалили ззовні)
            return None
        return active

    async def require_active_id(self) -> AccountId:
        active = await self.get_active_id()
        if active is None:
            raise NoActiveAccountError(
                "Активний акаунт не обрано. Додайте акаунт і виберіть його (`aliweb accounts use <id>`)"
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
