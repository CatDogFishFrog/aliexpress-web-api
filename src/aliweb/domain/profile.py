"""Профіль браузерного середовища акаунта («відбиток»).

Мета: кожен акаунт завжди працює в ОДНОМУ Й ТОМУ САМОМУ «середовищі» — той самий User-Agent,
відбиток (navigator, екран, відеокарта…), мова, часовий пояс, розмір вікна. Інакше сайт щодня бачить
той самий акаунт на «новому комп'ютері». Профіль зберігається разом із сесією в зашифрованому
файлі акаунта і переноситься при експорті/імпорті.

Домен не знає, як профіль створюється й застосовується (це справа ``infrastructure``): тут лише
дані та безпечні геттери.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

PROFILE_SCHEMA_VERSION = 1


class ClaimedOs(StrEnum):
    """Операційна система, яку «заявляє» профіль (вона ж у User-Agent та відбитку)."""

    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    """Постійне середовище акаунта.

    ``fingerprint`` — повний відбиток у форматі browserforge-stealth (navigator, screen, videoCard, headers…),
    непрозорий для домену. Версія Chromium у User-Agent у ньому — лише на момент створення: при
    кожному запуску вона підганяється під реальний рушій (як оновлюється справжній браузер).
    """

    created_at: datetime
    claimed_os: ClaimedOs
    locale: str  # напр. "en-US", "uk-UA"
    timezone_id: str | None  # IANA, напр. "Europe/Kyiv"; None — часовий пояс хоста
    viewport: tuple[int, int]  # внутрішній розмір вікна (CSS px), який емулюється
    fingerprint: Mapping[str, Any]
    schema_version: int = PROFILE_SCHEMA_VERSION

    # ── безпечні геттери (профіль міг прийти з файлу — не падаємо на відсутніх ключах) ──
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
        """Короткий підпис для списків: ``windows 1920x1080``."""
        width, height = self.screen_size
        return f"{self.claimed_os.value} {width}x{height}"
