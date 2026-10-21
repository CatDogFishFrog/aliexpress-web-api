"""Pool of launched patchright browsers, with at most one browser per headless/headful mode."""

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
    """Build ``chromium.launch`` options as a pure function testable without a browser.

    Keep flags minimal: patchright handles the main automation leaks, and extra command-line
    arguments may themselves indicate automation (see memory/05). ``browser`` selects patchright,
    Chrome, Edge, or a custom executable.
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
    # Chromium requires --no-sandbox in containers or when running as root.
    if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
        options["chromium_sandbox"] = False
        args.append("--no-sandbox")
    return options


def check_executable_exists(executable: str | None) -> None:
    """Check the browser path early and report a clear error instead of a Playwright exception."""
    if executable and not Path(executable).is_file():
        raise ConfigurationError(f"Browser executable not found: {executable}")


def _install_hint(browser: BrowserChoice) -> str:
    match browser.kind:
        case BrowserKind.CHROMIUM:
            return "Have you run `patchright install chromium`?"
        case BrowserKind.CHROME | BrowserKind.MSEDGE:
            return (
                f"Is {browser.kind.value} installed on this system? "
                f"Install it (`patchright install {browser.kind.value}`) "
                "or specify its path with --browser-path."
            )
        case BrowserKind.CUSTOM:
            return "Check the path and ensure this is a Chromium-compatible browser."


class BrowserPool:
    """Lazy browser factory with one browser per headless mode and browser choice."""

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
            _log.info("Launching browser", extra={"headless": mode, "browser": str(choice)})
            try:
                launched = await self._playwright.chromium.launch(**options)
            except Exception as exc:
                raise ConfigurationError(
                    f"Failed to launch browser ({choice}): {exc}. {_install_hint(choice)}"
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
