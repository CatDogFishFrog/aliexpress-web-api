"""Solve the nocaptcha slider with a human-like drag.

The slider lives in the baxia punish dialog iframe (``…/_____tmd_____/punish?…action=captcha``)
and loads about three seconds after an RGV587 response (the dialog initially shows "Loading").
Wait for the handle to appear before dragging it.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from patchright.async_api import ElementHandle, FloatRect, Frame, Page

from aliweb.aliexpress.login_screen import captcha_frame_present
from aliweb.aliexpress.pages import looks_like_challenge_url
from aliweb.aliexpress.selectors import LoginSelectors
from aliweb.infrastructure.browser.human import HumanInput

_log = logging.getLogger(__name__)
type Target = Page | Frame


def ease_in_out(progress: float) -> float:
    """Quadratic acceleration and deceleration for ``progress`` in [0, 1]."""
    return 2 * progress * progress if progress < 0.5 else -1 + (4 - 2 * progress) * progress


class SliderSolver:
    def __init__(
        self,
        selectors: LoginSelectors,
        *,
        rng: random.Random | None = None,
        poll_s: float = 0.2,
    ) -> None:
        self._sel = selectors
        self._rng = rng or random.Random()  # noqa: S311 — not used for cryptography.
        self._poll_s = poll_s

    def _targets(self, page: Page) -> list[Target]:
        """Return the main page and captcha iframes, excluding unrelated ad and analytics frames."""
        frames = [f for f in page.frames if f != page.main_frame and "punish" in f.url]
        return [page, *frames]

    async def _find_handle(self, page: Page) -> tuple[Target, ElementHandle, FloatRect] | None:
        for target in self._targets(page):
            try:
                handle = await target.query_selector(self._sel.slider_handle)
                if handle is None or not await handle.is_visible():
                    continue
                box = await handle.bounding_box()
                if box is not None:
                    return target, handle, box
            except Exception:
                _log.debug("Failed to locate slider handle", exc_info=True)
        return None

    async def _wait_for_handle(
        self, page: Page, timeout_s: float
    ) -> tuple[Target, ElementHandle, FloatRect] | None:
        deadline = time.monotonic() + timeout_s
        while True:
            found = await self._find_handle(page)
            if found is not None or time.monotonic() >= deadline or page.is_closed():
                return found
            await asyncio.sleep(self._poll_s)

    async def _track_width(self, target: Target, handle_box: FloatRect) -> float:
        try:
            track = await target.query_selector(self._sel.slider_track)
            box = await track.bounding_box() if track else None
            if box is not None and box["width"] > handle_box["width"] + 40:
                return float(box["width"])
        except Exception:
            _log.debug("Failed to measure slider track", exc_info=True)
        return 300.0  # Typical nocaptcha track width.

    async def solve(self, page: Page, human: HumanInput, *, timeout_s: float = 12.0) -> bool:
        """Wait for and solve the slider. Return ``True`` only if the captcha disappears."""
        found = await self._wait_for_handle(page, timeout_s)
        if found is None:
            _log.warning("Slider did not appear within %.0f seconds", timeout_s)
            return False
        target, _handle, box = found
        _log.info("Slider loaded; solving challenge")
        await human.pause(0.35, 0.9)  # Allow time to inspect the challenge.
        distance = max(40.0, await self._track_width(target, box) - box["width"])
        distance += self._rng.uniform(1.0, 6.0)  # The track boundary stops the handle.
        await human.drag_horizontally((box["x"] + box["width"] / 2, box["y"] + box["height"] / 2), distance)
        return await self._wait_until_solved(page)

    async def _wait_until_solved(self, page: Page, timeout_s: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if page.is_closed():
                return False
            if not captcha_frame_present(page) and not looks_like_challenge_url(page.url):
                await asyncio.sleep(0.3)
                return True
            await asyncio.sleep(self._poll_s)
        return False
