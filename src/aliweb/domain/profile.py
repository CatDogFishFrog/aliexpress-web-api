"""Browser environment profile (fingerprint) for an account.

Each account uses the same environment on every run: User-Agent, fingerprint (navigator, screen,
GPU, etc.), locale, timezone, and window size. Otherwise, the site may see the same account on a
"new computer" each day. The profile is stored with the session in the encrypted account file and
included in exports and imports.

The domain does not know how the profile is generated or applied (that belongs to ``infrastructure``);
it only defines the data and safe getters.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

PROFILE_SCHEMA_VERSION = 1


class ClaimedOs(StrEnum):
    """Operating system declared by the profile and represented in its User-Agent and fingerprint."""

    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    """Persistent account environment.

    ``fingerprint`` is an opaque browserforge-stealth fingerprint (navigator, screen, videoCard,
    headers, etc.). Its Chromium version reflects creation time; each run retargets it to the actual
    browser engine, as a real browser updates.
    """

    created_at: datetime
    claimed_os: ClaimedOs
    locale: str  # e.g. "en-US", "uk-UA".
    timezone_id: str | None  # IANA, e.g. "Europe/Kyiv"; None uses the host timezone.
    viewport: tuple[int, int]  # Emulated inner window size in CSS pixels.
    fingerprint: Mapping[str, Any]
    schema_version: int = PROFILE_SCHEMA_VERSION

    # ── Safe getters (profiles loaded from files may omit keys) ──────────────
    def _section(self, name: str) -> Mapping[str, Any]:
        section = self.fingerprint.get(name)
        return section if isinstance(section, Mapping) else {}

    @property
    def user_agent(self) -> str:
        return str(self._section("navigator").get("userAgent", ""))

    @property
    def screen_size(self) -> tuple[int, int]:
        screen = self._section("screen")
        return int(screen.get("width", 0)), int(screen.get("height", 0))

    @property
    def device_scale_factor(self) -> float:
        return float(self._section("screen").get("devicePixelRatio", 1) or 1)

    @property
    def gpu_renderer(self) -> str:
        return str(self._section("videoCard").get("renderer", ""))

    @property
    def hardware_concurrency(self) -> int | None:
        value = self._section("navigator").get("hardwareConcurrency")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def device_memory(self) -> float | None:
        value = self._section("navigator").get("deviceMemory")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def label(self) -> str:
        """Short label for lists, for example ``windows 1920x1080``."""
        width, height = self.screen_size
        return f"{self.claimed_os.value} {width}x{height}"
