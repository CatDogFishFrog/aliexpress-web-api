"""Браузерна сесія: контекст + сторінка + запис мережі + авто-діагностика при помилці."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from patchright.async_api import BrowserContext, Page
from patchright.async_api import Error as PlaywrightError

from aliweb.domain.accounts import StorageState
from aliweb.domain.browser import BrowserChoice
from aliweb.domain.errors import AliWebError, BrowserClosedError, ScrapingError
from aliweb.infrastructure.browser.pool import BrowserPool
from aliweb.infrastructure.config import BrowserSettings
from aliweb.infrastructure.diagnostics.collector import DiagnosticsCollector
from aliweb.infrastructure.diagnostics.network_log import NetworkRecorder
from aliweb.infrastructure.redaction import Redactor

_log = logging.getLogger(__name__)


def is_target_closed(error: BaseException) -> bool:
    """Чи помилка означає, що сторінку/контекст/браузер закрито (напр. користувач закрив вікно)."""
    return type(error).__name__ == "TargetClosedError"


def build_context_options(settings: BrowserSettings, storage_state: StorageState | None) -> dict[str, Any]:
    """Параметри ``browser.new_context``. Чиста функція.

    User-Agent свідомо НЕ підміняємо: реальний UA вбудованого/встановленого Chrome
    узгоджений із рештою відбитка (рекомендація patchright).
    """
    options: dict[str, Any] = {
        "locale": settings.locale,
        "service_workers": "block",  # SW можуть обходити маршрутизацію та засмічувати дампи
        "accept_downloads": False,
    }
    if settings.viewport is None:
        options["no_viewport"] = True
    else:
        options["viewport"] = {"width": settings.viewport[0], "height": settings.viewport[1]}
    if settings.timezone_id:
        options["timezone_id"] = settings.timezone_id
    if storage_state is not None:
        options["storage_state"] = storage_state
    return options


class BrowserSession:
    """Обгортка над контекстом із потрібним проєкту поведінкою. Створюється фабрикою."""

    def __init__(
        self,
        context: BrowserContext,
        page: Page,
        recorder: NetworkRecorder,
        diagnostics: DiagnosticsCollector,
        *,
        headless: bool,
    ) -> None:
        self._context = context
        self.page = page
        self.recorder = recorder
        self._diagnostics = diagnostics
        self.headless = headless

    async def storage_state(self) -> StorageState:
        return dict(await self._context.storage_state())

    async def checkpoint(self, name: str) -> None:
        """Контрольна точка потоку; дамп лише при ``ALIWEB_DUMP_POLICY=always``."""
        await self._diagnostics.checkpoint(self.page, name, self.recorder)

    async def dump(self, reason: str) -> None:
        """Примусовий дамп (для ручного дебагу)."""
        await self._diagnostics.capture(self.page, reason=reason, recorder=self.recorder)

    async def on_error(self, error: BaseException) -> None:
        if self.page.is_closed():
            return  # вікно закрито — знімати вже нічого, дамп лише засмітить теку
        path = await self._diagnostics.on_error(self.page, error, self.recorder)
        if path is None:
            return
        error.add_note(f"Діагностика: {path}")
        if isinstance(error, AliWebError):
            error.diagnostics_dir = str(path)


class BrowserSessionFactory:
    def __init__(
        self,
        pool: BrowserPool,
        settings: BrowserSettings,
        diagnostics: DiagnosticsCollector,
        redactor: Redactor,
    ) -> None:
        self._pool = pool
        self._settings = settings
        self._diagnostics = diagnostics
        self._redactor = redactor

    @asynccontextmanager
    async def open(
        self,
        storage_state: StorageState | None = None,
        *,
        headless: bool | None = None,
        browser: BrowserChoice | None = None,
    ) -> AsyncIterator[BrowserSession]:
        """Відкриває ізольований контекст (cookie з ``storage_state``) і сторінку.

        ``browser=None`` — браузер patchright (так працює скрапер); інший браузер лише для входу.

        При винятку: зберігає діагностику, додає до помилки примітку зі шляхом, а сторонні
        помилки patchright перетворює на :class:`ScrapingError`. Контекст завжди закривається.
        """
        launched = await self._pool.get(headless, browser)
        context = await launched.new_context(**build_context_options(self._settings, storage_state))
        context.set_default_timeout(self._settings.action_timeout_ms)
        context.set_default_navigation_timeout(self._settings.nav_timeout_ms)
        recorder = NetworkRecorder(self._redactor)
        recorder.attach(context)
        page = await context.new_page()
        session = BrowserSession(
            context,
            page,
            recorder,
            self._diagnostics,
            headless=self._pool.default_headless if headless is None else headless,
        )
        try:
            yield session
        except Exception as exc:
            await session.on_error(exc)
            if isinstance(exc, PlaywrightError) and is_target_closed(exc):
                raise BrowserClosedError(
                    "Вікно браузера закрито до завершення операції. "
                    "Не закривайте його вручну, поки програма працює."
                ) from exc
            if isinstance(exc, PlaywrightError):
                converted = ScrapingError(f"{type(exc).__name__}: {exc}")
                converted.diagnostics_dir = next(
                    (
                        n.removeprefix("Діагностика: ")
                        for n in getattr(exc, "__notes__", [])
                        if n.startswith("Діагностика: ")
                    ),
                    None,
                )
                raise converted from exc
            raise
        finally:
            with contextlib.suppress(Exception):
                await context.close()
