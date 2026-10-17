"""Збір діагностики: скріншот + повний дамп сторінки (MHTML) + HTML + звіт + мережевий журнал.

Принципи:
  * Діагностика НІКОЛИ не маскує оригінальну помилку: кожен артефакт збирається окремо,
    збій одного не зупиняє інші, жодних винятків назовні.
  * Секрети: перед зйомкою обнуляємо поля ``input[type=password]``; у текстових артефактах
    замінюємо зареєстровані секрети; cookie та заголовки не зберігаємо взагалі.
  * Дамп відкривається локально: ``snapshot.mhtml`` — це MHTML (повна копія сторінки з усіма
    ресурсами) від Chromium через CDP ``Page.captureSnapshot``. Відкривати в Chromium/Chrome/Edge.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import shutil
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from patchright.async_api import Page

from aliweb.infrastructure.config import DiagnosticsSettings, DumpPolicy
from aliweb.infrastructure.diagnostics.network_log import NetworkRecorder
from aliweb.infrastructure.redaction import Redactor
from aliweb.infrastructure.storage.files import ensure_private_dir

_log = logging.getLogger(__name__)
_STEP_TIMEOUT_S = 20.0

_MASK_PASSWORD_JS = """
() => {
  let n = 0;
  for (const el of document.querySelectorAll('input[type="password"]')) {
    if (el.value) { el.value = ''; n++; }
  }
  return n;
}
"""  # noqa: S105 — це JS-код, не пароль


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:40] or "dump"


class DiagnosticsCollector:
    """Створює теку ``<root>/<UTC-час>_<причина>_<rand>/`` з артефактами.

    Склад теки: ``screenshot.png``, ``snapshot.mhtml``, ``page.html``, ``network.jsonl``,
    ``report.json`` (з переліком артефактів і причин, якщо якийсь не вдалося зібрати).
    """

    def __init__(self, settings: DiagnosticsSettings, root: Path, redactor: Redactor) -> None:
        self._settings = settings
        self._root = root
        self._redactor = redactor

    # ── політика ───────────────────────────────────────────────────────────
    @property
    def policy(self) -> DumpPolicy:
        return self._settings.policy

    async def on_error(
        self, page: Page | None, error: BaseException, recorder: NetworkRecorder | None
    ) -> Path | None:
        """Дамп при помилці (якщо політика не ``never``)."""
        if self.policy is DumpPolicy.NEVER or page is None:
            return None
        return await self.capture(page, reason=type(error).__name__, error=error, recorder=recorder)

    async def checkpoint(self, page: Page, name: str, recorder: NetworkRecorder | None) -> Path | None:
        """Дамп на контрольній точці (лише при політиці ``always``)."""
        if self.policy is not DumpPolicy.ALWAYS:
            return None
        return await self.capture(page, reason=f"checkpoint-{name}", recorder=recorder)

    # ── зйомка ─────────────────────────────────────────────────────────────
    async def capture(
        self,
        page: Page,
        *,
        reason: str,
        error: BaseException | None = None,
        recorder: NetworkRecorder | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Path | None:
        """Примусовий дамп. Повертає теку або ``None``, якщо навіть її не вдалося створити."""
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        directory = self._root / f"{stamp}_{_slug(reason)}_{secrets.token_hex(2)}"
        try:
            ensure_private_dir(directory)
        except OSError:
            _log.exception("Не вдалося створити теку діагностики")
            return None

        artifacts: dict[str, str] = {}
        await self._step(artifacts, "mask_passwords", self._mask_passwords(page))
        await self._step(artifacts, "screenshot", self._screenshot(page, directory))
        await self._step(artifacts, "snapshot_mhtml", self._mhtml(page, directory))
        await self._step(artifacts, "page_html", self._html(page, directory))
        if recorder is not None:
            await self._step(artifacts, "network", self._network(recorder, directory))
        await self._step(artifacts, "report", self._report(page, directory, reason, error, artifacts, extra))

        self._apply_retention()
        _log.warning("Діагностичний дамп збережено", extra={"dir": str(directory), "reason": reason})
        return directory

    async def _step(self, artifacts: dict[str, str], name: str, coro: Any) -> None:
        try:
            await asyncio.wait_for(coro, timeout=_STEP_TIMEOUT_S)
            artifacts[name] = "ok"
        except Exception as exc:
            artifacts[name] = f"failed: {type(exc).__name__}: {self._redactor.redact(str(exc))[:200]}"
            _log.debug("Крок діагностики %s не вдався", name, exc_info=True)

    @staticmethod
    async def _mask_passwords(page: Page) -> None:
        await page.evaluate(_MASK_PASSWORD_JS)

    async def _screenshot(self, page: Page, directory: Path) -> None:
        await page.screenshot(
            path=directory / "screenshot.png",
            full_page=self._settings.screenshot_full_page,
            type="png",
        )

    async def _mhtml(self, page: Page, directory: Path) -> None:
        cdp = await page.context.new_cdp_session(page)
        try:
            result = await cdp.send("Page.captureSnapshot", {"format": "mhtml"})
        finally:
            with contextlib.suppress(Exception):
                await cdp.detach()
        data = self._redactor.redact_exact(result["data"])
        (directory / "snapshot.mhtml").write_text(data, encoding="utf-8", newline="")

    async def _html(self, page: Page, directory: Path) -> None:
        content = self._redactor.redact_exact(await page.content())
        (directory / "page.html").write_text(content, encoding="utf-8")

    @staticmethod
    async def _network(recorder: NetworkRecorder, directory: Path) -> None:
        (directory / "network.jsonl").write_text(recorder.to_jsonl(), encoding="utf-8")

    async def _report(
        self,
        page: Page,
        directory: Path,
        reason: str,
        error: BaseException | None,
        artifacts: dict[str, str],
        extra: dict[str, Any] | None,
    ) -> None:
        title = ""
        with contextlib.suppress(Exception):
            title = await page.title()
        report: dict[str, Any] = {
            "reason": reason,
            "captured_at": datetime.now(UTC).isoformat(),
            "url": self._redactor.redact(page.url),
            "title": title,
            "viewport": page.viewport_size,
            "artifacts": artifacts,
            "extra": extra or {},
        }
        if error is not None:
            report["error"] = {
                "type": type(error).__name__,
                "message": self._redactor.redact(str(error)),
                "traceback": self._redactor.redact("".join(traceback.format_exception(error))),
            }
        (directory / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    # ── ретеншн ────────────────────────────────────────────────────────────
    def _apply_retention(self) -> None:
        try:
            dirs = sorted(p for p in self._root.iterdir() if p.is_dir())
        except OSError:
            return
        for old in dirs[: max(0, len(dirs) - self._settings.keep)]:
            shutil.rmtree(old, ignore_errors=True)
