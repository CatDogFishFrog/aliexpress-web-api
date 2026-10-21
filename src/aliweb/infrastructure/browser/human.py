"""Human-like page interaction: typing, mouse movement, scrolling, clicks, and dragging.

Principles: avoid perfectly instant actions and implausibly long pauses.
    * Type one character at a time with variable intervals of about 100–200 ms and key holds of
        about 40–130 ms. Occasionally pause briefly; uppercase and special characters take slightly longer.
        No individual pause exceeds about 1.2 seconds.
    * Move the mouse along a curved path with variable speed, small jitter, and occasional overshoot.
        Duration depends on the distance.
    * Click a random point inside the element, pause briefly before pressing, and hold for about 50–130 ms.
    * Scroll with several short wheel events rather than one jump.

Randomness and waiting are injected (``rng``, ``sleep``), making tests deterministic and fast.
Path and timing calculations are pure functions that can be tested without a browser.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from patchright.async_api import ElementHandle, FloatRect, Page

from aliweb.infrastructure.config import HumanSettings

_log = logging.getLogger(__name__)

type Sleep = Callable[[float], Awaitable[None]]
type Point = tuple[float, float]

MAX_DELAY_S = 1.2  # Maximum individual pause before applying the speed multiplier.


@dataclass(frozen=True, slots=True)
class KeyTiming:
    """Timing for one character: key hold duration and wait before pressing, in seconds."""

    char: str
    wait_s: float
    hold_s: float


@dataclass(frozen=True, slots=True)
class MouseStep:
    x: float
    y: float
    dt_s: float  # Pause after moving to this point.


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def typing_timings(text: str, rng: random.Random, *, speed: float = 1.0) -> list[KeyTiming]:
    """Typing delays. The typical interval is about 0.12 seconds (70–80 words per minute)."""
    timings: list[KeyTiming] = []
    previous = ""
    for index, char in enumerate(text):
        if index == 0:
            wait = rng.uniform(0.15, 0.40)  # A short pause before typing.
        else:
            wait = rng.lognormvariate(math.log(0.115), 0.38)
            if char.isupper() or not (char.isalnum() or char == " "):
                wait += rng.uniform(0.04, 0.12)  # Shift or distant keys.
            if previous in "@._-":
                wait += rng.uniform(0.02, 0.08)
            if rng.random() < 0.04:
                wait += rng.uniform(0.18, 0.45)  # Occasional hesitation.
        hold = _clip(rng.gauss(0.075, 0.02), 0.035, 0.14)
        timings.append(KeyTiming(char, _clip(wait, 0.04, MAX_DELAY_S) / speed, hold / speed))
        previous = char
    return timings


def build_mouse_path(
    start: Point, end: Point, rng: random.Random, *, speed: float = 1.0, target_width: float = 40.0
) -> list[MouseStep]:
    """Mouse path from ``start`` to ``end``; the last point is exactly ``end``."""
    distance = math.dist(start, end)
    if distance < 1.0:
        return [MouseStep(end[0], end[1], 0.0)]

    # Fitts's law: movement time grows with the logarithm of the distance; clamp the extremes.
    duration = _clip(
        (0.16 + 0.13 * math.log2(distance / max(target_width, 8.0) + 1)) * rng.uniform(0.85, 1.2), 0.22, 1.1
    )
    duration /= speed

    # Offset Bezier control points perpendicular to the direction of travel.
    dx, dy = end[0] - start[0], end[1] - start[1]
    nx, ny = -dy / distance, dx / distance
    bend1 = rng.uniform(-0.18, 0.18) * distance
    bend2 = rng.uniform(-0.12, 0.12) * distance
    c1 = (start[0] + dx * 0.30 + nx * bend1, start[1] + dy * 0.30 + ny * bend1)
    c2 = (start[0] + dx * 0.72 + nx * bend2, start[1] + dy * 0.72 + ny * bend2)

    def bezier(t: float) -> Point:
        u = 1 - t
        return (
            u**3 * start[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * end[0],
            u**3 * start[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * end[1],
        )

    count = int(_clip(distance / 14 + 8, 12, 55))
    steps: list[MouseStep] = []
    weights: list[float] = []
    for i in range(1, count + 1):
        t = i / count
        eased = t * t * (3 - 2 * t)  # Smoothstep: slow start and finish.
        x, y = bezier(eased)
        if i < count:  # Add slight hand jitter, except at the final point.
            x += rng.gauss(0, 0.7)
            y += rng.gauss(0, 0.7)
        steps.append(MouseStep(x, y, 0.0))
        weights.append(rng.uniform(0.75, 1.25))

    total = sum(weights)
    steps = [MouseStep(s.x, s.y, duration * w / total) for s, w in zip(steps, weights, strict=True)]

    # Occasionally overshoot and correct on long movements.
    if distance > 280 and rng.random() < 0.35:
        over = rng.uniform(4, 11)
        ox, oy = end[0] + dx / distance * over, end[1] + dy / distance * over
        steps[-1] = MouseStep(ox, oy, steps[-1].dt_s)
        steps.append(MouseStep(end[0], end[1], rng.uniform(0.05, 0.11) / speed))
    else:
        steps[-1] = MouseStep(end[0], end[1], steps[-1].dt_s)
    return steps


class HumanInput:
    """Human-like page interaction. Use one instance per page to retain the mouse position."""

    def __init__(
        self,
        page: Page,
        settings: HumanSettings,
        *,
        rng: random.Random | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._page = page
        self._settings = settings
        self._rng = rng or random.Random()  # noqa: S311 — not used for cryptography.
        self._sleep = sleep
        self._pos: Point | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    @property
    def _speed(self) -> float:
        return self._settings.speed

    async def pause(self, low_s: float, high_s: float) -> None:
        """Wait for a random reaction delay; do nothing if human-like interaction is disabled."""
        if self.enabled:
            await self._sleep(_clip(self._rng.uniform(low_s, high_s), 0, MAX_DELAY_S) / self._speed)

    # ── Mouse ───────────────────────────────────────────────────────────────
    def _viewport(self) -> tuple[float, float]:
        size = self._page.viewport_size
        return (float(size["width"]), float(size["height"])) if size else (1280.0, 720.0)

    def _current_position(self) -> Point:
        if self._pos is None:
            width, height = self._viewport()
            self._pos = (self._rng.uniform(0.2, 0.8) * width, self._rng.uniform(0.2, 0.7) * height)
        return self._pos

    async def move_to(self, x: float, y: float, *, target_width: float = 40.0) -> None:
        start = self._current_position()
        if not self.enabled:
            await self._page.mouse.move(x, y)
            self._pos = (x, y)
            return
        for step in build_mouse_path(start, (x, y), self._rng, speed=self._speed, target_width=target_width):
            await self._page.mouse.move(step.x, step.y)
            if step.dt_s:
                await self._sleep(step.dt_s)
        self._pos = (x, y)

    async def settle(self) -> None:
        """After page load, pause briefly, move the mouse, and occasionally scroll a little."""
        if not self.enabled:
            return
        await self.pause(0.25, 0.7)
        width, height = self._viewport()
        await self.move_to(self._rng.uniform(0.25, 0.75) * width, self._rng.uniform(0.25, 0.7) * height)
        if self._rng.random() < 0.35:
            await self.idle_scroll()

    # ── Scrolling ──────────────────────────────────────────────────────────
    async def scroll(self, delta_y: float) -> None:
        """Scroll with several short wheel events (``delta_y`` > 0 means down)."""
        if not self.enabled:
            await self._page.mouse.wheel(0, delta_y)
            return
        pieces = self._rng.randint(3, 6)
        weights = [self._rng.uniform(0.6, 1.4) for _ in range(pieces)]
        total = sum(weights)
        for weight in weights:
            await self._page.mouse.wheel(0, delta_y * weight / total)
            await self._sleep(self._rng.uniform(0.018, 0.05) / self._speed)
        await self.pause(0.12, 0.3)

    async def idle_scroll(self) -> None:
        """Briefly scroll down and up if the page is scrollable."""
        with contextlib.suppress(Exception):
            overflow = await self._page.evaluate(
                "() => document.documentElement.scrollHeight - window.innerHeight"
            )
            if isinstance(overflow, (int, float)) and overflow > 20:
                amount = min(float(overflow), self._rng.uniform(40, 140))
                await self.scroll(amount)
                await self.pause(0.2, 0.5)
                await self.scroll(-amount)

    async def _ensure_visible(self, element: ElementHandle) -> None:
        """Scroll the element into view using wheel events if it is outside the viewport."""
        _, height = self._viewport()
        for _ in range(5):
            box = await element.bounding_box()
            if box is None:
                return
            top, bottom = box["y"], box["y"] + box["height"]
            if top >= 12 and bottom <= height - 12:
                return
            target_center = height * self._rng.uniform(0.4, 0.55)
            await self.scroll((top + box["height"] / 2) - target_center)
        with contextlib.suppress(Exception):
            await element.scroll_into_view_if_needed()

    # ── Clicking ───────────────────────────────────────────────────────────
    def _point_inside(self, box: FloatRect) -> Point:
        """Choose a random point inside the element, near its center and at least 3px from the edge."""

        def pick(origin: float, size: float) -> float:
            margin = min(3.0, size / 4)
            value = origin + size / 2 + self._rng.gauss(0, size * 0.14)
            return _clip(value, origin + margin, origin + size - margin)

        return pick(box["x"], box["width"]), pick(box["y"], box["height"])

    async def click(self, element: ElementHandle) -> None:
        """Click an element with human-like input, or use ``element.click()`` when disabled."""
        if not self.enabled:
            await element.click()
            return
        await self._ensure_visible(element)
        box = await element.bounding_box()
        if box is None:
            await element.click()  # Fallback if the element disappeared or has no bounding box.
            return
        x, y = self._point_inside(box)
        await self.move_to(x, y, target_width=box["width"])
        await self.pause(0.06, 0.18)  # Brief hover before pressing.
        await self._page.mouse.down()
        await self._sleep(_clip(self._rng.gauss(0.085, 0.025), 0.045, 0.16) / self._speed)
        await self._page.mouse.up()
        await self.pause(0.08, 0.25)

    # ── Typing ─────────────────────────────────────────────────────────────
    async def type_text(self, text: str) -> None:
        """Type text into the focused element one character at a time."""
        if not self.enabled:
            await self._page.keyboard.type(text)
            return
        for timing in typing_timings(text, self._rng, speed=self._speed):
            await self._sleep(timing.wait_s)
            # Playwright delay is the key hold duration between keydown and keyup.
            await self._page.keyboard.type(timing.char, delay=timing.hold_s * 1000)

    async def fill(self, element: ElementHandle, text: str) -> None:
        """Click the field and type naturally, or use ``element.fill`` when disabled."""
        if not self.enabled:
            await element.fill(text)
            return
        await self.click(element)
        await self.pause(0.1, 0.3)
        await self.type_text(text)

    async def press_enter(self) -> None:
        await self.pause(0.15, 0.45)
        await self._page.keyboard.press("Enter", delay=self._rng.uniform(40, 110))

    # ── Dragging (slider) ──────────────────────────────────────────────────
    async def drag_horizontally(self, handle_center: Point, distance: float) -> None:
        """Drag an element right from ``handle_center`` by ``distance`` pixels.

        Use variable speed: a slow pickup, acceleration, deceleration near the end, and slight
        vertical wobble. The movement takes about 0.7–1.5 seconds.
        """
        await self.move_to(*handle_center)
        await self.pause(0.12, 0.35)
        await self._page.mouse.down()
        await self._sleep(self._rng.uniform(0.06, 0.16) / self._speed)

        sx, sy = handle_center
        if not self.enabled:
            await self._page.mouse.move(sx + distance, sy, steps=20)
            await self._page.mouse.up()
            return

        steps = self._rng.randint(22, 34)
        total = self._rng.uniform(0.7, 1.5) / self._speed
        weights = [self._rng.uniform(0.7, 1.3) for _ in range(steps)]
        scale = total / sum(weights)
        wobble = self._rng.uniform(1.0, 2.5)
        for i in range(1, steps + 1):
            t = i / steps
            eased = 1 - (1 - t) ** 2.2 if t > 0.5 else 2 * t * t  # Accelerate, then decelerate.
            x = sx + distance * eased
            y = sy + math.sin(t * math.pi * 2) * wobble + self._rng.gauss(0, 0.4)
            await self._page.mouse.move(x, y)
            await self._sleep(weights[i - 1] * scale)
        await self._page.mouse.move(sx + distance, sy + self._rng.uniform(-0.8, 0.8))
        await self._sleep(self._rng.uniform(0.06, 0.18) / self._speed)
        await self._page.mouse.up()
        self._pos = (sx + distance, sy)
