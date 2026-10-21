"""Create and apply a persistent browser environment profile for an account.

Based on the standalone ``browserforge-stealth`` package (``browserforge_stealth`` module), which
provides generation, sanitization, realism filtering (rejection sampling), Chromium GREASE brands,
and dynamic retargeting to the browser engine version.

This module adapts ``browserforge-stealth`` to the ``BrowserProfile`` domain model. The profile is
stored with the account and remains stable across runs. Chromium's User-Agent and client hints are
retargeted to the actual engine version on each launch.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import browserforge_stealth as bfs
from patchright.async_api import BrowserContext

from aliweb.domain.errors import ProfileGenerationError
from aliweb.domain.profile import BrowserProfile, ClaimedOs
from aliweb.infrastructure.config import FingerprintSettings

_log = logging.getLogger(__name__)


def host_os() -> ClaimedOs:
    """Detect the host operating system."""
    plat = sys.platform
    if plat == "darwin":
        return ClaimedOs.MACOS
    if plat.startswith("linux"):
        return ClaimedOs.LINUX
    return ClaimedOs.WINDOWS


def chromium_brands(major: int, full_version: str | None = None) -> list[tuple[str, str]]:
    """Return ``navigator.userAgentData.brands`` as constructed by Chromium."""
    return bfs.chromium_brands(major, full_version)


def sec_ch_ua(brands: list[tuple[str, str]]) -> str:
    """Format brand data as an HTTP Sec-CH-UA header value."""
    return bfs.sec_ch_ua(brands)


def build_user_agent(claimed_os: ClaimedOs, major: int) -> str:
    """Return a reduced User-Agent for modern Chrome: ``Chrome/<major>.0.0.0``."""
    return bfs.build_user_agent(bfs.OperatingSystem(claimed_os.value), major, bfs.DeviceType.DESKTOP)


def parse_browser_version(version: str | None, fallback_ua: str = "") -> tuple[int, str]:
    """Return ``(major, full_version)``; use the existing UA if the engine version is unknown."""
    return bfs.parse_browser_version(version, fallback_ua)


def normalize_languages(locale: str) -> tuple[list[str], str]:
    """Return ``(navigator.languages, Accept-Language)`` in Chrome format."""
    return bfs.normalize_languages(locale)


def realism_issues(fingerprint: Mapping[str, Any], claimed_os: ClaimedOs) -> list[str]:
    """Explain why the fingerprint is unrealistic; an empty list means it is valid."""
    return bfs.realism_issues(fingerprint, bfs.OperatingSystem(claimed_os.value), bfs.DeviceType.DESKTOP)


def derive_viewport(fingerprint: Mapping[str, Any], claimed_os: ClaimedOs) -> tuple[int, int]:
    """Inner window size: maximized window minus browser chrome."""
    return bfs.derive_viewport(fingerprint, bfs.OperatingSystem(claimed_os.value), bfs.DeviceType.DESKTOP)


def retarget(profile: BrowserProfile, browser_version: str | None) -> dict[str, Any]:
    """Copy the fingerprint with UA, brands, and client hints matched to the actual engine version."""
    return bfs.retarget(_to_bfs_fingerprint(profile), browser_version)


def client_hint_headers(profile: BrowserProfile, major: int) -> dict[str, str]:
    """HTTP headers sent by Chrome itself: low-entropy client hints and Accept-Language."""
    return bfs.client_hint_headers(bfs.OperatingSystem(profile.claimed_os.value), profile.locale, major)


def _to_bfs_fingerprint(profile: BrowserProfile) -> bfs.Fingerprint:
    return bfs.Fingerprint(
        schema_version=profile.schema_version,
        created_at=profile.created_at,
        device_type=bfs.DeviceType.DESKTOP,
        os=bfs.OperatingSystem(profile.claimed_os.value),
        locale=profile.locale,
        timezone_id=profile.timezone_id,
        viewport=profile.viewport,
        raw_data=dict(profile.fingerprint),
    )


def profile_context_options(profile: BrowserProfile, browser_version: str | None) -> dict[str, Any]:
    """``browser.new_context`` options that apply the profile environment."""
    return bfs.context_options(_to_bfs_fingerprint(profile), browser_version)


def build_init_script(profile: BrowserProfile, browser_version: str | None) -> str:
    """JavaScript that overrides navigator, screen, WebGL, and userAgentData before page scripts run."""
    return bfs.build_init_script(_to_bfs_fingerprint(profile), browser_version)


async def apply_profile(
    context: BrowserContext,
    profile: BrowserProfile,
    browser_version: str | None,
) -> None:
    """Inject the fingerprint into the context. Other options come from :func:`profile_context_options`."""
    await context.add_init_script(build_init_script(profile, browser_version))


def warn_on_environment_drift(
    profile: BrowserProfile, *, configured_locale: str, current_os: ClaimedOs | None = None
) -> None:
    """Warn if the host differs from the profile; the profile itself is never modified."""
    here = current_os or host_os()
    if profile.claimed_os is not here:
        _log.warning(
            "Profile OS differs from host OS; fonts, GPU, or rendering may not match",
            extra={"profile_os": profile.claimed_os.value, "host_os": here.value},
        )
    if profile.locale.lower() != configured_locale.lower():
        _log.warning(
            "Profile locale differs from ALIWEB_LOCALE; the profile locale remains in effect",
            extra={"profile_locale": profile.locale, "configured": configured_locale},
        )


class BrowserForgeStealthProfileGenerator:
    """Implement the ``ProfileGenerator`` port using browserforge-stealth."""

    def __init__(
        self,
        settings: FingerprintSettings,
        *,
        locale: str,
        timezone_id: str | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_attempts: int = 60,
    ) -> None:
        self._claimed_os = settings.claimed_os or host_os()
        self._locale = locale
        self._timezone_id = timezone_id
        self._clock = clock
        self._max_attempts = max_attempts
        self._generator: bfs.FingerprintGenerator | None = None

    def generate(self) -> BrowserProfile:
        """Generate a realistic profile, rejecting unrealistic samples until one passes validation."""
        if self._generator is None:
            self._generator = bfs.FingerprintGenerator(
                os=bfs.OperatingSystem(self._claimed_os.value),
                device_type=bfs.DeviceType.DESKTOP,
                locale=self._locale,
                timezone_id=self._timezone_id,
                realistic=True,
                max_attempts=self._max_attempts,
            )

        try:
            fp = self._generator.generate()
        except bfs.GenerationError as err:
            raise ProfileGenerationError(str(err)) from err
        return BrowserProfile(
            created_at=self._clock(),
            claimed_os=self._claimed_os,
            locale=self._locale,
            timezone_id=self._timezone_id,
            viewport=fp.viewport,
            fingerprint=fp.raw_data,
        )

