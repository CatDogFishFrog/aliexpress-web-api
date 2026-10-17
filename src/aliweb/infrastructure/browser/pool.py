"""Пул запущених браузерів (patchright). Не більше одного браузера на режим headless/headful."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import Any

from patchright.async_api import Browser, Playwright, async_playwright

from aliweb.domain.browser import BrowserChoice, BrowserKind
from aliweb.domain.errors import ConfigurationError
from aliweb.infrastructure.config import BrowserSettings

_log = logging.getLogger(__name__)


def build_launch_options(
    settings: BrowserSettings, *, headless: bool, browser: BrowserChoice | None = None
) -> dict[str, Any]:
    """Параметри ``chromium.launch``. Чиста функція (тестується без браузера).

    Свідомо мінімум прапорців: patchright сам прибирає основні витоки, а зайві аргументи
    командного рядка самі по собі можуть бути ознакою автоматизації (див. memory/05).
    ``browser`` обирає браузер: вбудований patchright (за замовч.), Chrome, Edge чи власний шлях.
    """
    browser = browser or BrowserChoice()
    args = list(settings.extra_args)
    options: dict[str, Any] = {"headless": headless, "args": args}
    match browser.kind:
        case BrowserKind.CHROME | BrowserKind.MSEDGE:
            options["channel"] = browser.kind.value
        case BrowserKind.CUSTOM:
            options["executable_path"] = browser.executable_path
        case BrowserKind.CHROMIUM:
            pass
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


def check_executable_exists(executable: str | None) -> None:
    """Швидка перевірка шляху до браузера (зрозуміла помилка замість внутрішньої помилки Playwright)."""
    if executable and not Path(executable).is_file():
        raise ConfigurationError(f"Файл браузера не знайдено: {executable}")


def _install_hint(browser: BrowserChoice) -> str:
    match browser.kind:
        case BrowserKind.CHROMIUM:
            return "Чи виконано `patchright install chromium`?"
        case BrowserKind.CHROME | BrowserKind.MSEDGE:
            return (
                f"Чи встановлено {browser.kind.value} у системі? "
                f"Встановіть його (`patchright install {browser.kind.value}`) "
                "або вкажіть шлях: --browser-path."
            )
        case BrowserKind.CUSTOM:
            return "Перевірте шлях і те, що це chromium-сумісний браузер."


class BrowserPool:
    """Лінива фабрика браузерів. Один браузер на пару (режим headless, вибір браузера)."""

    def __init__(self, settings: BrowserSettings) -> None:
        self._settings = settings
        self._playwright: Playwright | None = None
        self._browsers: dict[tuple[bool, BrowserChoice], Browser] = {}
        self._lock = asyncio.Lock()

    @property
    def default_headless(self) -> bool:
        return self._settings.headless

    async def get(self, headless: bool | None = None, browser: BrowserChoice | None = None) -> Browser:
        mode = self._settings.headless if headless is None else headless
        choice = browser or BrowserChoice()
        key = (mode, choice)
        async with self._lock:
            existing = self._browsers.get(key)
            if existing is not None and existing.is_connected():
                return existing
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            options = build_launch_options(self._settings, headless=mode, browser=choice)
            check_executable_exists(options.get("executable_path"))
            _log.info("Запуск браузера", extra={"headless": mode, "browser": str(choice)})
            try:
                launched = await self._playwright.chromium.launch(**options)
            except Exception as exc:
                raise ConfigurationError(
                    f"Не вдалося запустити браузер ({choice}): {exc}. {_install_hint(choice)}"
                ) from exc
            self._browsers[key] = launched
            return launched

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
