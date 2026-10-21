"""Public library facade and single entry point for consumers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from aliweb.application.accounts import AccountService
from aliweb.application.auth import AuthService
from aliweb.application.catalog import CatalogService, SellerService
from aliweb.application.ports.debug import PageDebugger
from aliweb.application.transfer import SessionTransferService


@dataclass(frozen=True, slots=True)
class AliExpressWebApi:
    """Service graph created by :func:`aliweb.bootstrap.build_api`.

    Usage::

        async with build_api(Settings.from_env()) as api:
            await api.accounts.add(AccountId.parse("main"), Credentials(...))
            await api.auth.login(AccountId.parse("main"))
            page = await api.catalog.search(SearchQuery("usb cable"))
    """

    accounts: AccountService
    auth: AuthService
    transfer: SessionTransferService
    catalog: CatalogService
    sellers: SellerService
    debug: PageDebugger
    _close: Callable[[], Awaitable[None]]

    async def aclose(self) -> None:
        """Close browsers. Safe to call more than once."""
        await self._close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
