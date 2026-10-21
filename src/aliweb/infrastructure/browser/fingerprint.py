"""Створення та застосування постійного профілю браузерного середовища акаунта.

Базується на автономному пакеті ``browserforge-stealth`` (модуль ``browserforge_stealth``),
що інкапсулює алгоритми генерації, санітизації (нормалізації), фільтрації реалістичності (rejection sampling),
Chromium GREASE-брендів та динамічного retargeting під версію рушія.

Цей модуль виступає адаптером між ``browserforge-stealth`` та доменною моделлю ``BrowserProfile``.
Профіль зберігається в акаунті й незмінний: щодня та сама ОС, екран, GPU, мова, часовий пояс.
Версія Chromium у User-Agent та client hints підганяється під реальний рушій при кожному запуску.
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
    """Визначає ОС хоста."""
    plat = sys.platform
    if plat == "darwin":
        return ClaimedOs.MACOS
    if plat.startswith("linux"):
        return ClaimedOs.LINUX
    return ClaimedOs.WINDOWS


def chromium_brands(major: int, full_version: str | None = None) -> list[tuple[str, str]]:
    """Список брендів ``navigator.userAgentData.brands`` рівно так, як його будує Chromium."""
    return bfs.chromium_brands(major, full_version)


def sec_ch_ua(brands: list[tuple[str, str]]) -> str:
    """Форматує список брендів у значення HTTP-заголовка Sec-CH-UA."""
    return bfs.sec_ch_ua(brands)


def build_user_agent(claimed_os: ClaimedOs, major: int) -> str:
    """Скорочений (reduced) UA сучасного Chrome: ``Chrome/<major>.0.0.0``."""
    return bfs.build_user_agent(bfs.OperatingSystem(claimed_os.value), major, bfs.DeviceType.DESKTOP)


def parse_browser_version(version: str | None, fallback_ua: str = "") -> tuple[int, str]:
    """``(major, full_version)``. Якщо версія рушія невідома — береться з наявного UA."""
    return bfs.parse_browser_version(version, fallback_ua)


def normalize_languages(locale: str) -> tuple[list[str], str]:
    """``(navigator.languages, Accept-Language)`` у форматі Chrome."""
    return bfs.normalize_languages(locale)


def realism_issues(fingerprint: Mapping[str, Any], claimed_os: ClaimedOs) -> list[str]:
    """Чому відбиток вважається нереалістичним (порожній список — усе гаразд)."""
    return bfs.realism_issues(fingerprint, bfs.OperatingSystem(claimed_os.value), bfs.DeviceType.DESKTOP)


def derive_viewport(fingerprint: Mapping[str, Any], claimed_os: ClaimedOs) -> tuple[int, int]:
    """Внутрішній розмір вікна: максимізоване вікно мінус інтерфейс браузера."""
    return bfs.derive_viewport(fingerprint, bfs.OperatingSystem(claimed_os.value), bfs.DeviceType.DESKTOP)


def retarget(profile: BrowserProfile, browser_version: str | None) -> dict[str, Any]:
    """Копія відбитка з UA, брендами й client hints, узгодженими з РЕАЛЬНОЮ версією рушія."""
    return bfs.retarget(_to_bfs_fingerprint(profile), browser_version)


def client_hint_headers(profile: BrowserProfile, major: int) -> dict[str, str]:
    """HTTP-заголовки, які справжній Chrome шле сам (low-entropy client hints) + Accept-Language."""
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
    """Параметри ``browser.new_context``, що фіксують середовище з профілю."""
    return bfs.context_options(_to_bfs_fingerprint(profile), browser_version)


def build_init_script(profile: BrowserProfile, browser_version: str | None) -> str:
    """JS, що підміняє navigator/screen/WebGL/userAgentData тощо ще до скриптів сторінки."""
    return bfs.build_init_script(_to_bfs_fingerprint(profile), browser_version)


async def apply_profile(
    context: BrowserContext,
    profile: BrowserProfile,
    browser_version: str | None,
) -> None:
    """Впроваджує відбиток у контекст (init-скрипт). Решта — через :func:`profile_context_options`."""
    await context.add_init_script(build_init_script(profile, browser_version))


def warn_on_environment_drift(
    profile: BrowserProfile, *, configured_locale: str, current_os: ClaimedOs | None = None
) -> None:
    """Попереджає, якщо реальне оточення розходиться з профілем (профіль при цьому НЕ змінюється)."""
    here = current_os or host_os()
    if profile.claimed_os is not here:
        _log.warning(
            "Профіль заявляє іншу ОС, ніж хост: можливі розбіжності (шрифти, GPU, рендеринг)",
            extra={"profile_os": profile.claimed_os.value, "host_os": here.value},
        )
    if profile.locale.lower() != configured_locale.lower():
        _log.warning(
            "Мова профілю відрізняється від ALIWEB_LOCALE; діє мова профілю (середовище не змінюється)",
            extra={"profile_locale": profile.locale, "configured": configured_locale},
        )


class BrowserForgeStealthProfileGenerator:
    """Реалізує порт ``ProfileGenerator`` поверх browserforge-stealth."""

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
        """Новий реалістичний профіль. Відкидає нереалістичні зразки й генерує, доки не вийде хороший."""
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

