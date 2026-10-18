from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest

from aliweb.aliexpress.manual_flow import ManualLoginFlow
from aliweb.aliexpress.selectors import SELECTORS
from aliweb.domain.errors import BrowserClosedError, ManualLoginTimeoutError
from aliweb.infrastructure.config import AliExpressSettings
from tests.fakes_browser import FakePage

SEL = SELECTORS.login
LOGIN_URL = AliExpressSettings().login_url
LOGIN_PAGE = "https://www.aliexpress.com/p/ug-login-page/login.html"
HOME = "https://www.aliexpress.com/"


class FakeContext:
    def __init__(self) -> None:
        self.cookie_list: list[dict[str, str]] = []

    async def cookies(self) -> list[dict[str, str]]:
        return list(self.cookie_list)


class FakeProbe:
    def __init__(self, answers: Callable[[float], bool]) -> None:
        self.answers = answers
        self.calls: list[float] = []
        self.clock: Callable[[], float] = lambda: 0.0

    async def check_in_context(self, context: object) -> bool:
        self.calls.append(self.clock())
        return self.answers(self.clock())


class Harness:
    """Віртуальний час + «користувач», що діє за сценарієм у певні моменти."""

    def __init__(self, *, probe_answer: Callable[[float], bool]) -> None:
        self.now = 0.0
        self.page = FakePage(LOGIN_PAGE)
        self.page.redirects[LOGIN_URL] = LOGIN_PAGE
        self.page.visible_selectors = {SEL.landing_email_button}  # екран входу видно одразу
        self.context = FakeContext()
        self.page.context = self.context  # type: ignore[attr-defined]
        self.probe = FakeProbe(probe_answer)
        self.probe.clock = lambda: self.now
        self.script: list[tuple[float, Callable[[], None]]] = []
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        for when, action in list(self.script):
            if self.now >= when:
                self.script.remove((when, action))
                action()

    def flow(self) -> ManualLoginFlow:
        return ManualLoginFlow(
            SEL,
            cast(Any, self.probe),
            AliExpressSettings(),
            poll_s=1.0,
            sleep=self.sleep,
            clock=lambda: self.now,
        )

    async def run(self, timeout_s: float = 60.0) -> bool:
        session = type("S", (), {"page": self.page})()
        return await self.flow().run(cast(Any, session), timeout_s=timeout_s)


def user_goes_home(h: Harness) -> Callable[[], None]:
    def act() -> None:
        h.page.url = HOME
        h.page.visible_selectors = set()

    return act


async def test_already_authenticated_returns_immediately_without_waiting() -> None:
    h = Harness(probe_answer=lambda t: True)
    h.page.redirects[LOGIN_URL] = HOME  # сторінка входу перекидає геть
    assert await h.run() is True
    assert h.sleeps == [] and h.probe.calls == []


async def test_waits_for_human_then_confirms_via_probe_and_settles() -> None:
    h = Harness(probe_answer=lambda t: True)
    h.script.append((5.0, user_goes_home(h)))
    assert await h.run() is False
    assert len(h.probe.calls) == 1 and h.probe.calls[0] >= 5.0
    assert h.sleeps[-1] == 2.0  # пауза «дати cookie дописатись» після підтвердження


async def test_probe_not_called_while_user_is_still_on_login_page() -> None:
    h = Harness(probe_answer=lambda t: True)
    with pytest.raises(ManualLoginTimeoutError):
        await h.run(timeout_s=20)
    assert h.probe.calls == []  # без ознак входу не навантажуємо сайт перевірками


async def test_clicking_logo_to_home_is_not_mistaken_for_login() -> None:
    """Людина могла просто піти на головну анонімно — це не вхід."""
    h = Harness(probe_answer=lambda t: t >= 20)  # справжня авторизація настає лише в t=20
    h.script.append((4.0, user_goes_home(h)))
    assert await h.run(timeout_s=60) is False
    assert len(h.probe.calls) >= 2 and h.probe.calls[-1] >= 20
    gaps = [b - a for a, b in zip(h.probe.calls, h.probe.calls[1:], strict=False)]
    assert all(g >= 3.0 for g in gaps)  # перевірки не частіше ніж раз на 3 с


async def test_ticket_cookie_triggers_probe_even_on_login_page() -> None:
    h = Harness(probe_answer=lambda t: True)
    h.script.append(
        (3.0, lambda: h.context.cookie_list.append({"name": "login_aliyunid_ticket", "value": "t"}))
    )
    assert await h.run() is False
    assert h.probe.calls


async def test_window_closed_by_user_is_clear_error() -> None:
    h = Harness(probe_answer=lambda t: True)

    def close() -> None:
        h.page.closed = True

    h.script.append((3.0, close))
    with pytest.raises(BrowserClosedError, match="закрито"):
        await h.run()


async def test_timeout_message_mentions_option() -> None:
    h = Harness(probe_answer=lambda t: False)
    with pytest.raises(ManualLoginTimeoutError, match="manual-timeout"):
        await h.run(timeout_s=10)
