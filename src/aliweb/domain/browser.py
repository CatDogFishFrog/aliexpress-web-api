"""Browser selection and account login mode.

Rules (see memory/03-decisions.md ADR-13):
    * Headless mode always uses the patchright browser (``CHROMIUM``).
    * Headful mode can use patchright, Chrome, Edge, or a custom executable path.
    * The scraper always uses patchright; its mode is configured in ``.env``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aliweb.domain.errors import InvalidLoginModeError


class BrowserKind(StrEnum):
    CHROMIUM = "chromium"  # patchright's bundled Chromium (`patchright install chromium`)
    CHROME = "chrome"  # installed Google Chrome
    MSEDGE = "msedge"  # installed Microsoft Edge
    CUSTOM = "custom"  # custom Chromium-compatible browser at the given path


@dataclass(frozen=True, slots=True)
class BrowserChoice:
    """Browser to launch. ``executable_path`` is required for CUSTOM and forbidden otherwise."""

    kind: BrowserKind = BrowserKind.CHROMIUM
    executable_path: str | None = None

    def __post_init__(self) -> None:
        if self.kind is BrowserKind.CUSTOM and not self.executable_path:
            raise InvalidLoginModeError("A custom browser requires an executable path")
        if self.kind is not BrowserKind.CUSTOM and self.executable_path:
            raise InvalidLoginModeError("An executable path can only be set for a custom browser")

    @classmethod
    def custom(cls, executable_path: str) -> BrowserChoice:
        return cls(BrowserKind.CUSTOM, executable_path)

    @property
    def is_bundled(self) -> bool:
        return self.kind is BrowserKind.CHROMIUM

    def __str__(self) -> str:
        return self.executable_path or self.kind.value


@dataclass(frozen=True, slots=True)
class LoginMode:
    """Login mode: headless or headful, browser choice, and automatic or manual login.

        Invariants:
            * headless login only works with patchright;
            * manual login (``manual``) requires a visible browser window.
        Manual mode disables automation: the program waits for the user to log in, then captures the
        session and closes the browser. Credentials are not required.
    """

    headless: bool = True
    browser: BrowserChoice = BrowserChoice()
    manual: bool = False
    manual_timeout_s: float = 600.0

    def __post_init__(self) -> None:
        if self.headless and not self.browser.is_bundled:
            raise InvalidLoginModeError(
                "Headless login only supports the patchright browser (chromium). "
                "Use headful mode (--headful) for Chrome, Edge, or a custom browser."
            )
        if self.manual and self.headless:
            raise InvalidLoginModeError("Manual login (--manual) requires a browser window; add --headful.")
        if self.manual_timeout_s <= 0:
            raise InvalidLoginModeError("Manual login timeout must be positive")
