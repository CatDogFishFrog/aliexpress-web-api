"""Реалізація ``PageDebugger``: відкрити URL активним акаунтом і зробити дамп."""

from __future__ import annotations

from aliweb.infrastructure.browser.page_provider import AuthenticatedPageProvider
from aliweb.infrastructure.diagnostics.collector import DiagnosticsCollector


class ProviderPageDebugger:
    def __init__(self, provider: AuthenticatedPageProvider, diagnostics: DiagnosticsCollector) -> None:
        self._provider = provider
        self._diagnostics = diagnostics

    async def open_and_dump(self, url: str, *, headless: bool | None = None) -> str | None:
        async with self._provider.acquire(headless=headless) as session:
            await session.page.goto(url, wait_until="domcontentloaded")
            path = await self._diagnostics.capture(
                session.page, reason="debug-open", recorder=session.recorder
            )
        return str(path) if path else None
