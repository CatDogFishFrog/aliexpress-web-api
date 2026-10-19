from __future__ import annotations

from typing import Protocol

from aliweb.domain.profile import BrowserProfile


class ProfileGenerator(Protocol):
    """Створює реалістичний профіль браузерного середовища для акаунта."""

    def generate(self) -> BrowserProfile:
        """Raises:
        ProfileGenerationError: не вдалося отримати реалістичний профіль.
        """
        ...
