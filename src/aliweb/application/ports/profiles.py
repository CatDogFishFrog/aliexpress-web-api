from __future__ import annotations

from typing import Protocol

from aliweb.domain.profile import BrowserProfile


class ProfileGenerator(Protocol):
    """Create a realistic browser environment profile for an account."""

    def generate(self) -> BrowserProfile:
        """Raises:
        ProfileGenerationError: if a realistic profile could not be generated.
        """
        ...
