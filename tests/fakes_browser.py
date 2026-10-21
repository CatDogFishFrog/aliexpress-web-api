"""Fake Page and Element objects for tests without a browser."""

from __future__ import annotations

from typing import Any


class FakeMouse:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.moves: list[tuple[float, float]] = []
        self.downs = 0
        self.ups = 0
        self.wheels: list[float] = []

    async def move(self, x: float, y: float, *, steps: int | None = None) -> None:
        self.moves.append((x, y))

    async def down(self) -> None:
        self.downs += 1

    async def up(self) -> None:
        self.ups += 1

    async def wheel(self, dx: float, dy: float) -> None:
        self.wheels.append(dy)
        self.page.scroll_y += dy


class FakeKeyboard:
    def __init__(self) -> None:
        self.typed: list[tuple[str, float | None]] = []
        self.pressed: list[str] = []

    async def type(self, text: str, *, delay: float | None = None) -> None:
        self.typed.append((text, delay))

    async def press(self, key: str, *, delay: float | None = None) -> None:
        self.pressed.append(key)


class FakeElement:
    def __init__(
        self, page: FakePage, x: float, y: float, w: float, h: float, *, visible: bool = True
    ) -> None:
        self.page = page
        self._x, self._y, self._w, self._h = x, y, w, h
        self.visible = visible
        self.clicks = 0
        self.filled: list[str] = []
        self.focused = False

    async def bounding_box(self) -> dict[str, float] | None:
        return {"x": self._x, "y": self._y - self.page.scroll_y, "width": self._w, "height": self._h}

    async def is_visible(self) -> bool:
        return self.visible

    async def click(self) -> None:
        self.clicks += 1

    async def fill(self, text: str) -> None:
        self.filled.append(text)

    async def focus(self) -> None:
        self.focused = True

    async def scroll_into_view_if_needed(self) -> None:
        return None


class FakeFrame:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> FakeLocator:
        return self

    async def is_visible(self) -> bool:
        return self._selector in self._page.visible_selectors


class FakePage:
    """Minimal patchright ``Page`` fake. ``visible_selectors`` lists selectors visible on the page."""

    def __init__(self, url: str = "https://www.aliexpress.com/p/ug-login-page/login.html") -> None:
        self.url = url
        self.viewport_size: dict[str, int] | None = {"width": 1366, "height": 768}
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard()
        self.main_frame = FakeFrame(url)
        self.frames: list[Any] = [self.main_frame]
        self.visible_selectors: set[str] = set()
        self.scroll_y = 0.0
        self.closed = False
        self.overflow: float = 80.0
        self.goto_calls: list[str] = []
        self.redirects: dict[str, str] = {}

    async def goto(self, url: str, *, wait_until: str | None = None) -> None:
        self.goto_calls.append(url)
        self.url = self.redirects.get(url, url)

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def is_closed(self) -> bool:
        return self.closed

    async def evaluate(self, script: str) -> float:
        return self.overflow
