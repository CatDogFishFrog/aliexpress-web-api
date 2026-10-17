"""Сценарії автентифікації: вхід, вихід, гарантування дійсної сесії."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from aliweb.application.accounts import AccountService, Clock, utc_now
from aliweb.application.ports.accounts import AccountRepository
from aliweb.application.ports.auth import Authenticator, LoginRequest
from aliweb.domain.accounts import AccountId, SessionState
from aliweb.domain.errors import AliWebError, MissingCredentialsError

_log = logging.getLogger(__name__)


class AuthService:
    """Оркеструє вхід/вихід акаунтів поверх :class:`Authenticator`.

    Не знає про браузер: усе, що стосується сайту, — за портом ``Authenticator``.
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
        self, account_id: AccountId, *, headless: bool | None = None, fresh: bool = False
    ) -> SessionState:
        """Входить в акаунт та зберігає сесію.

        Args:
            headless: ``None`` — з конфігурації; ``False`` — показати браузер (інколи
                headless-вхід не пускає).
            fresh: ігнорувати наявну сесію (вхід «з чистого аркуша»).
        """
        account = await self._accounts.get(account_id)
        if account.credentials is None:
            raise MissingCredentialsError(
                f"В акаунті {account_id} не збережено логін/пароль — вхід неможливий"
            )
        previous = None if fresh or account.session is None else account.session.storage_state
        session = await self._auth.login(
            LoginRequest(credentials=account.credentials, headless=headless, storage_state=previous)
        )
        await self._repo.save(account.with_session(session, now=self._clock()))
        self._verified_at[account_id] = self._monotonic()
        _log.info("Вхід виконано", extra={"account": str(account_id)})
        return session

    async def logout(self, account_id: AccountId) -> None:
        """Вихід: best-effort на сайті + гарантоване очищення сесії у сховищі."""
        account = await self._accounts.get(account_id)
        if account.session is not None:
            try:
                await self._auth.logout(account.session)
            except AliWebError:
                _log.warning(
                    "Вихід на боці сайту не вдався; сесію очищено локально",
                    extra={"account": str(account_id)},
                    exc_info=True,
                )
        await self._repo.save(account.with_session(None, now=self._clock()))
        self._verified_at.pop(account_id, None)

    async def ensure_session(self, account_id: AccountId, *, headless: bool | None = None) -> SessionState:
        """Повертає дійсну сесію: перевіряє наявну (з TTL-кешем) або входить заново."""
        account = await self._accounts.get(account_id)
        session = account.session
        if session is None:
            return await self.login(account_id, headless=headless)

        verified = self._verified_at.get(account_id)
        if verified is not None and self._monotonic() - verified < self._verify_ttl_s:
            return session

        if await self._auth.is_session_valid(session, headless=headless):
            self._verified_at[account_id] = self._monotonic()
            return session

        _log.info("Збережена сесія недійсна — повторний вхід", extra={"account": str(account_id)})
        return await self.login(account_id, headless=headless)

    async def check(self, account_id: AccountId, *, headless: bool | None = None) -> bool:
        """Примусова перевірка збереженої сесії без повторного входу."""
        account = await self._accounts.get(account_id)
        if account.session is None:
            return False
        valid = await self._auth.is_session_valid(account.session, headless=headless)
        if valid:
            self._verified_at[account_id] = self._monotonic()
        else:
            self._verified_at.pop(account_id, None)
        return valid

    def invalidate_verification(self, account_id: AccountId) -> None:
        """Скидає кеш перевірки (напр. коли сторінка показала, що сесія слетіла)."""
        self._verified_at.pop(account_id, None)
