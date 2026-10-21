from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aliweb.domain.accounts import AccountId


@dataclass(frozen=True, slots=True)
class DebugOpenResult:
    """Result of ``open_and_dump``: dump location and session used to open the page."""

    dump_dir: str | None
    account_id: AccountId | None  # Account environment used; None means no profile overrides.
    with_session: bool = False  # Whether saved session cookies were used.
    with_profile: bool = False  # Whether the browser profile was applied.


class PageDebugger(Protocol):
    """Manual page debugging tools, primarily for extractor development."""

    async def open_and_dump(
        self,
        url: str,
        *,
        headless: bool | None = None,
        account_id: AccountId | None = None,
        anonymous: bool = False,
    ) -> DebugOpenResult:
        """Open ``url`` and save a diagnostic dump without logging in or changing the saved session.

        Use the selected (or active) account's browser profile and saved session cookies, if available,
        without verifying the session. ``anonymous=True`` uses a clean browser with no cookies or overrides.
        """
        ...
