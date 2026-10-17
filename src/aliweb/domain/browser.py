"""Вибір браузера та режиму для входу в акаунт.

Правила (рішення замовника, див. memory/03-decisions.md ADR-13):
  * Безголовий режим ЗАВЖДИ працює на браузері patchright (``CHROMIUM``).
  * Для режиму з вікном (headful) можна обрати браузер: patchright, Chrome, Edge або власний шлях.
  * Скрапер (не вхід) завжди використовує браузер patchright; його режим задається в ``.env``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aliweb.domain.errors import InvalidLoginModeError


class BrowserKind(StrEnum):
    CHROMIUM = "chromium"  # вбудований Chromium patchright (`patchright install chromium`)
    CHROME = "chrome"  # встановлений Google Chrome
    MSEDGE = "msedge"  # встановлений Microsoft Edge
    CUSTOM = "custom"  # власний chromium-сумісний браузер за шляхом


@dataclass(frozen=True, slots=True)
class BrowserChoice:
    """Який браузер запускати. ``executable_path`` обов'язковий для CUSTOM і заборонений для решти."""

    kind: BrowserKind = BrowserKind.CHROMIUM
    executable_path: str | None = None

    def __post_init__(self) -> None:
        if self.kind is BrowserKind.CUSTOM and not self.executable_path:
            raise InvalidLoginModeError("Для власного браузера потрібен шлях до виконуваного файлу")
        if self.kind is not BrowserKind.CUSTOM and self.executable_path:
            raise InvalidLoginModeError("Шлях до браузера можна задавати лише для власного браузера")

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
    """Режим входу: безголовий чи з вікном + який браузер.

    Інваріант: безголовий вхід можливий лише в браузері patchright.
    """

    headless: bool = True
    browser: BrowserChoice = BrowserChoice()

    def __post_init__(self) -> None:
        if self.headless and not self.browser.is_bundled:
            raise InvalidLoginModeError(
                "Безголовий вхід можливий лише в браузері patchright (chromium). "
                "Для Chrome, Edge чи власного браузера використайте режим з вікном (--headful)."
            )
