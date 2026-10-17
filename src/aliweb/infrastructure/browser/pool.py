"""Пул запущених браузерів (patchright). Не більше одного браузера на режим headless/headful."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from typing import Any

from patchright.async_api import Browser, Playwright, async_playwright

from aliweb.domain.errors import ConfigurationError
from aliweb.infrastructure.config import BrowserSettings

_log = logging.getLogger(__name__)


def build_launch_options(settings: BrowserSettings, *, headless: bool) -> dict[str, Any]:
    """Параметри ``chromium.launch``. Чиста функція (тестується без браузера).

    Свідомо мінімум прапорців: patchright сам прибирає основні витоки, а зайві аргументи
    командного рядка самі по собі можуть бути ознакою автоматизації (див. memory/05).
    """
    args = list(settings.extra_args)
    options: dict[str, Any] = {"headless": headless, "args": args}
    if settings.channel:
        options["channel"] = settings.channel
    if settings.slow_mo_ms:
        options["slow_mo"] = settings.slow_mo_ms
    if settings.proxy:
        proxy: dict[str, str] = {"server": settings.proxy.server}
        if settings.proxy.username:
            proxy["username"] = settings.proxy.username
        if settings.proxy.password:
            proxy["password"] = settings.proxy.password.reveal()
        options["proxy"] = proxy
    # У контейнерах/під root Chromium без --no-sandbox не стартує.
    if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
        options["chromium_sandbox"] = False
        args.append("--no-sandbox")
    return options


class BrowserPool:
    """Лінива фабрика браузерів. Закривається разом із застосунком (:meth:`aclose`)."""

    def __init__(self, settings: BrowserSettings) -> None:
        self._settings = settings
        self._playwright: Playwright | None = None
        self._browsers: dict[bool, Browser] = {}
        self._lock = asyncio.Lock()

    @property
    def default_headless(self) -> bool:
        return self._settings.headless

    async def get(self, headless: bool | None = None) -> Browser:
        mode = self._settings.headless if headless is None else headless
        async with self._lock:
            browser = self._browsers.get(mode)
            if browser is not None and browser.is_connected():
                return browser
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            options = build_launch_options(self._settings, headless=mode)
            _log.info(
                "Запуск браузера",
                extra={"headless": mode, "channel": self._settings.channel or "bundled"},
            )
            try:
                browser = await self._playwright.chromium.launch(**options)
            except Exception as exc:
                raise ConfigurationError(
                    f"Не вдалося запустити Chromium: {exc}. "
                    "Чи виконано `patchright install chromium` (або встановлено Chrome для channel=chrome)?"
                ) from exc
            self._browsers[mode] = browser
            return browser

    async def aclose(self) -> None:
        async with self._lock:
            for browser in self._browsers.values():
                with contextlib.suppress(Exception):
                    await browser.close()
            self._browsers.clear()
            if self._playwright is not None:
                with contextlib.suppress(Exception):
                    await self._playwright.stop()
                self._playwright = None
