"""Проходження слайдер-капчі (nocaptcha) людиноподібним рухом миші. Перенесено з ali-coins."""

from __future__ import annotations

import logging
import random

from patchright.async_api import FloatRect, Frame, Page

from aliweb.aliexpress.selectors import LoginSelectors

_log = logging.getLogger(__name__)


def ease_in_out(progress: float) -> float:
    """Квадратичне пришвидшення-сповільнення, ``progress`` в [0, 1]."""
    return 2 * progress * progress if progress < 0.5 else -1 + (4 - 2 * progress) * progress


class SliderSolver:
    """Шукає слайдер на сторінці та в усіх фреймах і «протягує» його."""

    def __init__(self, selectors: LoginSelectors, *, rng: random.Random | None = None) -> None:
        self._sel = selectors
        self._rng = rng or random.Random()  # noqa: S311 — не криптографія, а «людський» джитер

    async def try_solve(self, page: Page) -> bool:
        """``True``, лише якщо капча справді зникла після протягування."""
        targets: list[Page | Frame] = [page, *page.frames]
        for target in targets:
            try:
                handle = await target.query_selector(self._sel.slider_handle)
                if handle is None:
                    continue
                box = await handle.bounding_box()
                if box is None:
                    continue
                _log.info("Виявлено слайдер-капчу, спроба проходження")
                return await self._drag(page, target, box)
            except Exception:
                _log.debug("Помилка при спробі пройти слайдер у цілі", exc_info=True)
        return False

    async def _drag(self, page: Page, target: Page | Frame, box: FloatRect) -> bool:
        track_width = (
            await target.eval_on_selector(self._sel.slider_track, "el => el.getBoundingClientRect().width")
            if await target.query_selector(self._sel.slider_track)
            else 320
        )
        distance = track_width - box["width"] + 10 if track_width > 150 else 300

        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        steps = 25
        for i in range(1, steps + 1):
            x = start_x + distance * ease_in_out(i / steps)
            y = start_y + self._rng.uniform(-1, 1)
            await page.mouse.move(x, y)
            await page.wait_for_timeout(10 + self._rng.randint(0, 10))
        await page.wait_for_timeout(50)
        await page.mouse.up()

        # Успіх лише якщо трек зник у ТІЙ САМІЙ цілі, де знайдено ручку (фрейм ≠ main frame).
        try:
            await target.wait_for_selector(self._sel.slider_track, state="detached", timeout=2000)
            solved = True
        except Exception:
            solved = False
        await page.wait_for_timeout(1000)
        return solved
