"""Сценарії автентифікації: вхід, вихід, гарантування дійсної сесії."""

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
        self, account_id: AccountId, *, mode: LoginMode | None = None, fresh: bool = False
    ) -> LoginResult:
        """Входить в акаунт та зберігає сесію.

        Args:
            mode: режим і браузер входу (``LoginMode``); ``None`` — як у скрапера (з конфігурації).
                Для ручного входу (``mode.manual``) облікові дані не потрібні.
            fresh: ігнорувати наявну сесію (вхід «з чистого аркуша»).

        Returns:
            ``LoginResult``; ``already_authenticated=True``, якщо сайт одразу визнав збережену
            сесію дійсною (повторний вхід не виконувався).
        """
        account = await self._accounts.ensure_profile(account_id)  # середовище акаунта фіксується тут
        manual = mode is not None and mode.manual
        if account.credentials is None and not manual:
            raise MissingCredentialsError(
                f"В акаунті {account_id} не збережено логін/пароль — автоматичний вхід неможливий. "
                "Задайте їх (`aliweb accounts update`) або увійдіть вручну: "
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
            "Вхід виконано",
            extra={"account": str(account_id), "already_authenticated": result.already_authenticated},
        )
        return result

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

        _log.info("Збережена сесія недійсна — повторний вхід", extra={"account": str(account_id)})
        return (await self.login(account_id, mode=relogin_mode)).session

    async def check(self, account_id: AccountId, *, headless: bool | None = None) -> bool:
        """Примусова перевірка збереженої сесії без повторного входу."""
        if (await self._accounts.get(account_id)).session is None:
            return False
        account = await self._accounts.ensure_profile(account_id)  # сесія вже є — потрібне її середовище
        session = account.session
        if session is None:  # лише для типізації: щойно перевірили, що сесія є
            return False
        valid = await self._auth.is_session_valid(session, headless=headless, profile=account.profile)
        if valid:
            self._verified_at[account_id] = self._monotonic()
        else:
            self._verified_at.pop(account_id, None)
        return valid

    def invalidate_verification(self, account_id: AccountId) -> None:
        """Скидає кеш перевірки (напр. коли сторінка показала, що сесія слетіла)."""
        self._verified_at.pop(account_id, None)
