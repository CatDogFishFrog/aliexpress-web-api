"""Людиноподібна взаємодія зі сторінкою: набір тексту, рухи миші, скрол, кліки, перетягування.

Принципи (вимога замовника): ніяких «ідеальних» миттєвих дій і ніяких неправдоподібно довгих пауз.
  * Набір — символ за символом; інтервал між натисканнями ~100–200 мс з випадковим розкидом
    (логнормальний), час утримання клавіші ~40–130 мс; зрідка коротке «замислення»; великі
    літери/символи трохи повільніші. Жодна пауза не перевищує ~1,2 с.
  * Миша — крива Безьє з випадковим вигином, нерівномірною швидкістю (прискорення/гальмування),
    мікро-тремтінням і зрідка легким «перельотом» з корекцією; тривалість залежить від відстані.
  * Клік — не в центр, а в випадкову точку всередині елемента; перед кліком коротка «затримка
    наведення», кнопка утримується ~50–130 мс.
  * Скрол — колесом кількома короткими поштовхами, а не стрибком.

Вся «випадковість» і очікування інжектуються (``rng``, ``sleep``): тести детерміновані й миттєві.
Побудова траєкторій та затримок винесена в чисті функції — їх можна перевіряти без браузера.
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

MAX_DELAY_S = 1.2  # жодна окрема пауза між діями не довша за це (до поділу на speed)


@dataclass(frozen=True, slots=True)
class KeyTiming:
    """Один символ: скільки утримувати клавішу та скільки чекати ПЕРЕД натисканням (секунди)."""

    char: str
    wait_s: float
    hold_s: float


@dataclass(frozen=True, slots=True)
class MouseStep:
    x: float
    y: float
    dt_s: float  # пауза після переміщення в цю точку


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def typing_timings(text: str, rng: random.Random, *, speed: float = 1.0) -> list[KeyTiming]:
    """Затримки набору тексту. Типовий інтервал ≈ 0,12 с (≈ 70–80 слів/хв для швидкого друкаря)."""
    timings: list[KeyTiming] = []
    previous = ""
    for index, char in enumerate(text):
        if index == 0:
            wait = rng.uniform(0.15, 0.40)  # перед першим символом людина «налаштовується»
        else:
            wait = rng.lognormvariate(math.log(0.115), 0.38)
            if char.isupper() or not (char.isalnum() or char == " "):
                wait += rng.uniform(0.04, 0.12)  # Shift / віддалені клавіші
            if previous in "@._-":
                wait += rng.uniform(0.02, 0.08)
            if rng.random() < 0.04:
                wait += rng.uniform(0.18, 0.45)  # короткий «затик»
        hold = _clip(rng.gauss(0.075, 0.02), 0.035, 0.14)
        timings.append(KeyTiming(char, _clip(wait, 0.04, MAX_DELAY_S) / speed, hold / speed))
        previous = char
    return timings


def build_mouse_path(
    start: Point, end: Point, rng: random.Random, *, speed: float = 1.0, target_width: float = 40.0
) -> list[MouseStep]:
    """Траєкторія миші від ``start`` до ``end`` (остання точка — рівно ``end``)."""
    distance = math.dist(start, end)
    if distance < 1.0:
        return [MouseStep(end[0], end[1], 0.0)]

    # Закон Фітса: час руху росте з логарифмом відстані; обмежуємо, щоб не було ні миттєво, ні вічно.
    duration = _clip(
        (0.16 + 0.13 * math.log2(distance / max(target_width, 8.0) + 1)) * rng.uniform(0.85, 1.2), 0.22, 1.1
    )
    duration /= speed

    # Контрольні точки Безьє зі зсувом перпендикулярно напрямку руху (природний вигин).
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
        eased = t * t * (3 - 2 * t)  # smoothstep: повільний старт і гальмування наприкінці
        x, y = bezier(eased)
        if i < count:  # мікро-тремтіння руки (не на останній точці)
            x += rng.gauss(0, 0.7)
            y += rng.gauss(0, 0.7)
        steps.append(MouseStep(x, y, 0.0))
        weights.append(rng.uniform(0.75, 1.25))

    total = sum(weights)
    steps = [MouseStep(s.x, s.y, duration * w / total) for s, w in zip(steps, weights, strict=True)]

    # На далеких рухах інколи легкий «переліт» і корекція назад до цілі.
    if distance > 280 and rng.random() < 0.35:
        over = rng.uniform(4, 11)
        ox, oy = end[0] + dx / distance * over, end[1] + dy / distance * over
        steps[-1] = MouseStep(ox, oy, steps[-1].dt_s)
        steps.append(MouseStep(end[0], end[1], rng.uniform(0.05, 0.11) / speed))
    else:
        steps[-1] = MouseStep(end[0], end[1], steps[-1].dt_s)
    return steps


class HumanInput:
    """Людиноподібне керування сторінкою. Один екземпляр на сторінку (пам'ятає позицію миші)."""

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
        self._rng = rng or random.Random()  # noqa: S311 — не криптографія
        self._sleep = sleep
        self._pos: Point | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    @property
    def _speed(self) -> float:
        return self._settings.speed

    async def pause(self, low_s: float, high_s: float) -> None:
        """Випадкова пауза «реакції». Нічого не робить, якщо людяність вимкнено."""
        if self.enabled:
            await self._sleep(_clip(self._rng.uniform(low_s, high_s), 0, MAX_DELAY_S) / self._speed)

    # ── миша ───────────────────────────────────────────────────────────────
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
        """Після завантаження сторінки: коротка пауза, легкий рух миші, інколи невеликий скрол."""
        if not self.enabled:
            return
        await self.pause(0.25, 0.7)
        width, height = self._viewport()
        await self.move_to(self._rng.uniform(0.25, 0.75) * width, self._rng.uniform(0.25, 0.7) * height)
        if self._rng.random() < 0.35:
            await self.idle_scroll()

    # ── скрол ──────────────────────────────────────────────────────────────
    async def scroll(self, delta_y: float) -> None:
        """Скрол колесом кількома короткими поштовхами (``delta_y`` > 0 — вниз)."""
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
        """Легке «оглядове» прокручування вниз-вгору, якщо сторінка взагалі прокручується."""
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
        """Якщо елемент поза видимою областю — прокручуємо колесом, як людина."""
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

    # ── клік ───────────────────────────────────────────────────────────────
    def _point_inside(self, box: FloatRect) -> Point:
        """Випадкова точка всередині елемента (гауссово навколо центру, не ближче 3px до краю)."""

        def pick(origin: float, size: float) -> float:
            margin = min(3.0, size / 4)
            value = origin + size / 2 + self._rng.gauss(0, size * 0.14)
            return _clip(value, origin + margin, origin + size - margin)

        return pick(box["x"], box["width"]), pick(box["y"], box["height"])

    async def click(self, element: ElementHandle) -> None:
        """Людиноподібний клік по елементу. Без людяності — звичайний ``element.click()``."""
        if not self.enabled:
            await element.click()
            return
        await self._ensure_visible(element)
        box = await element.bounding_box()
        if box is None:
            await element.click()  # елемент зник/без геометрії — запасний варіант
            return
        x, y = self._point_inside(box)
        await self.move_to(x, y, target_width=box["width"])
        await self.pause(0.06, 0.18)  # «наведення» перед натисканням
        await self._page.mouse.down()
        await self._sleep(_clip(self._rng.gauss(0.085, 0.025), 0.045, 0.16) / self._speed)
        await self._page.mouse.up()
        await self.pause(0.08, 0.25)

    # ── набір тексту ───────────────────────────────────────────────────────
    async def type_text(self, text: str) -> None:
        """Набирає текст у сфокусований елемент символ за символом."""
        if not self.enabled:
            await self._page.keyboard.type(text)
            return
        for timing in typing_timings(text, self._rng, speed=self._speed):
            await self._sleep(timing.wait_s)
            # delay у Playwright — це час утримання клавіші між keydown і keyup
            await self._page.keyboard.type(timing.char, delay=timing.hold_s * 1000)

    async def fill(self, element: ElementHandle, text: str) -> None:
        """Клік у поле і людський набір. Без людяності — миттєвий ``element.fill``."""
        if not self.enabled:
            await element.fill(text)
            return
        await self.click(element)
        await self.pause(0.1, 0.3)
        await self.type_text(text)

    async def press_enter(self) -> None:
        await self.pause(0.15, 0.45)
        await self._page.keyboard.press("Enter", delay=self._rng.uniform(40, 110))

    # ── перетягування (слайдер) ────────────────────────────────────────────
    async def drag_horizontally(self, handle_center: Point, distance: float) -> None:
        """Тягне елемент із центру ``handle_center`` вправо на ``distance`` px.

        Профіль швидкості нерівний: повільний «захват», розгін, уповільнення біля кінця,
        легке вертикальне хитання. Загалом ~0,7–1,5 с.
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
            eased = 1 - (1 - t) ** 2.2 if t > 0.5 else 2 * t * t  # розгін, потім гальмування
            x = sx + distance * eased
            y = sy + math.sin(t * math.pi * 2) * wobble + self._rng.gauss(0, 0.4)
            await self._page.mouse.move(x, y)
            await self._sleep(weights[i - 1] * scale)
        await self._page.mouse.move(sx + distance, sy + self._rng.uniform(-0.8, 0.8))
        await self._sleep(self._rng.uniform(0.06, 0.18) / self._speed)
        await self._page.mouse.up()
        self._pos = (sx + distance, sy)
