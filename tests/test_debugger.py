"""``debug open`` НЕ входить в акаунт: відкриває сторінку з наявною сесією або анонімно."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from aliweb.application.accounts import AccountService
from aliweb.domain.accounts import AccountId, StorageState
from aliweb.domain.errors import AccountNotFoundError
from aliweb.infrastructure.browser.debugger import AccountSessionDebugger
from aliweb.infrastructure.browser.session import BrowserSession, is_target_closed
from tests.conftest import MemoryActiveStore, MemoryRepository, make_account, make_session


class FakePage:
    def __init__(self) -> None:
        self.visited: list[str] = []
        self._closed = False

    async def goto(self, url: str, *, wait_until: str | None = None) -> None:
        self.visited.append(url)

    async def wait_for_load_state(self, state: str, *, timeout: float | None = None) -> None:
        return None

    def is_closed(self) -> bool:
        return self._closed


class FakeFactory:
    def __init__(self) -> None:
        self.opened: list[dict[str, Any]] = []
        self.page = FakePage()

    @asynccontextmanager
    async def open(
        self,
        storage_state: StorageState | None = None,
        *,
        headless: bool | None = None,
        browser: object = None,
    ) -> AsyncIterator[Any]:
        self.opened.append({"storage_state": storage_state, "headless": headless, "browser": browser})
        yield type("Session", (), {"page": self.page, "recorder": None})()


class FakeDiagnostics:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    async def capture(self, page: object, *, reason: str, recorder: object = None, **kw: object) -> Path:
        self.reasons.append(reason)
        return Path("data/diagnostics/x")


@pytest.fixture
def parts(
    repo: MemoryRepository, active: MemoryActiveStore
) -> tuple[AccountSessionDebugger, FakeFactory, AccountService]:
    accounts = AccountService(repo, active)
    factory = FakeFactory()
    debugger = AccountSessionDebugger(accounts, cast(Any, factory), cast(Any, FakeDiagnostics()))
    return debugger, factory, accounts


async def test_uses_stored_session_without_any_login(
    parts: tuple[AccountSessionDebugger, FakeFactory, AccountService],
    repo: MemoryRepository,
    active: MemoryActiveStore,
) -> None:
    debugger, factory, accounts = parts
    repo.items[AccountId("main")] = make_account("main", session=make_session("COOKIE"))
    active.value = AccountId("main")
    result = await debugger.open_and_dump("https://www.aliexpress.com/")
    assert result.account_id == AccountId("main") and result.dump_dir
    assert factory.opened[0]["storage_state"] == make_session("COOKIE").storage_state
    assert factory.page.visited == ["https://www.aliexpress.com/"]


async def test_account_without_session_opens_anonymously_and_does_not_login(
    parts: tuple[AccountSessionDebugger, FakeFactory, AccountService],
    repo: MemoryRepository,
    active: MemoryActiveStore,
) -> None:
    """Сценарій з логу: акаунт без сесії, а `debug open` чомусь намагався увійти."""
    debugger, factory, _ = parts
    repo.items[AccountId("main")] = make_account("main")  # є пароль, сесії немає
    active.value = AccountId("main")
    result = await debugger.open_and_dump("https://www.aliexpress.com/")
    assert result.account_id is None
    assert factory.opened[0]["storage_state"] is None
    assert factory.page.visited == ["https://www.aliexpress.com/"]  # лише запитана сторінка


async def test_anonymous_flag_ignores_existing_session(
    parts: tuple[AccountSessionDebugger, FakeFactory, AccountService],
    repo: MemoryRepository,
    active: MemoryActiveStore,
) -> None:
    debugger, factory, _ = parts
    repo.items[AccountId("main")] = make_account("main", session=make_session())
    active.value = AccountId("main")
    result = await debugger.open_and_dump("https://x.aliexpress.com/", anonymous=True)
    assert result.account_id is None and factory.opened[0]["storage_state"] is None


async def test_explicit_account_overrides_active(
    parts: tuple[AccountSessionDebugger, FakeFactory, AccountService],
    repo: MemoryRepository,
    active: MemoryActiveStore,
) -> None:
    debugger, factory, _ = parts
    repo.items[AccountId("a")] = make_account("a", session=make_session("A"))
    repo.items[AccountId("b")] = make_account("b", session=make_session("B"))
    active.value = AccountId("a")
    result = await debugger.open_and_dump(
        "https://x.aliexpress.com/", account_id=AccountId("b"), headless=False
    )
    assert result.account_id == AccountId("b")
    assert factory.opened[0] == {
        "storage_state": make_session("B").storage_state,
        "headless": False,
        "browser": None,
    }


async def test_no_accounts_at_all_is_anonymous(
    parts: tuple[AccountSessionDebugger, FakeFactory, AccountService],
) -> None:
    debugger, factory, _ = parts
    assert (await debugger.open_and_dump("https://x.aliexpress.com/")).account_id is None


async def test_unknown_account_is_an_error(
    parts: tuple[AccountSessionDebugger, FakeFactory, AccountService],
) -> None:
    debugger, _, _ = parts
    with pytest.raises(AccountNotFoundError):
        await debugger.open_and_dump("https://x.aliexpress.com/", account_id=AccountId("ghost"))


class TargetClosedError(Exception):
    """Ім'я збігається з patchright._impl._errors.TargetClosedError (його не експортують публічно)."""


def test_target_closed_detection_by_class_name() -> None:
    assert is_target_closed(TargetClosedError("x"))
    assert not is_target_closed(ValueError("x"))


async def test_no_dump_attempted_when_window_was_closed() -> None:
    """Лог користувача: TargetClosedError → 4 марні невдалі кроки дампу. Тепер дамп пропускається."""

    class Diagnostics:
        called = False

        async def on_error(self, *a: object, **k: object) -> None:
            Diagnostics.called = True

    page = FakePage()
    page._closed = True
    session = BrowserSession(
        cast(Any, object()), cast(Any, page), cast(Any, object()), cast(Any, Diagnostics()), headless=False
    )
    err = TargetClosedError("closed")
    await session.on_error(err)
    assert Diagnostics.called is False and not getattr(err, "__notes__", [])
