"""Створення та застосування постійного профілю браузерного середовища акаунта.

Підхід — як у Microsoft-Rewards-Script (fingerprint-generator + fingerprint-injector), перенесений на
Python (``browserforge``) і доповнений виправленнями реалістичності, знайденими при аналізі вибірки
з ~450 згенерованих відбитків:

  * ``sec-ch-ua`` у згенерованих заголовках у ~15 % випадків містив «Brave» при UA «Chrome» →
    заголовки й ``navigator.userAgentData`` будуються з реального списку брендів Chromium
    (алгоритм GREASE із Chromium, звірений із відомими значеннями для версій 120/124/131/133/147);
  * ``deviceMemory`` 16/32 (справжній Chrome віддає максимум 8) → обмежуємо до 8;
  * програмні GPU («Basic Render Driver»), невідповідність ОС (macOS із ``Linux x86_64``, Linux із
    ``armv81``), нестандартні DPR → такі зразки відкидаються й генеруються заново;
  * ``navigator.languages`` із пробілом (``' uk-UA'``) та нетиповий ``Accept-Language`` →
    нормалізуються до формату Chrome (``uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7``);
  * версія Chrome у User-Agent підганяється під РЕАЛЬНИЙ рушій при кожному запуску.

Профіль зберігається в акаунті й незмінний: щодня та сама ОС, UA, екран, GPU, мова, часовий пояс.
"""

from __future__ import annotations

import copy
import json
import logging
import random
import re
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast

from browserforge.fingerprints import Fingerprint, FingerprintGenerator, Screen
from browserforge.injectors.utils import InjectFunction
from patchright.async_api import BrowserContext

from aliweb.domain.errors import ProfileGenerationError
from aliweb.domain.profile import BrowserProfile, ClaimedOs
from aliweb.infrastructure.config import FingerprintSettings

_log = logging.getLogger(__name__)

# ── Chromium: бренди та User-Agent ─────────────────────────────────────────
_GREASE_CHARS = (" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_")
_GREASE_VERSIONS = ("8", "99", "24")
_BRAND_ORDERS = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))
_PRODUCT_BRAND = "Google Chrome"

_OS_PLATFORM = {  # navigator.platform
    ClaimedOs.WINDOWS: "Win32",
    ClaimedOs.MACOS: "MacIntel",
    ClaimedOs.LINUX: "Linux x86_64",
}
_OS_UACH_PLATFORM = {  # navigator.userAgentData.platform / sec-ch-ua-platform
    ClaimedOs.WINDOWS: "Windows",
    ClaimedOs.MACOS: "macOS",
    ClaimedOs.LINUX: "Linux",
}
_UA_SYSTEM_TOKEN = {
    ClaimedOs.WINDOWS: "Windows NT 10.0; Win64; x64",
    ClaimedOs.MACOS: "Macintosh; Intel Mac OS X 10_15_7",
    ClaimedOs.LINUX: "X11; Linux x86_64",
}
#: Висота інтерфейсу браузера (вкладки+адресний рядок) за ОС — для виведення розміру вікна. Наближення.
_BROWSER_UI_HEIGHT = {ClaimedOs.WINDOWS: 85, ClaimedOs.MACOS: 79, ClaimedOs.LINUX: 88}
_ALLOWED_DPR: dict[ClaimedOs, frozenset[float]] = {
    ClaimedOs.WINDOWS: frozenset({1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0}),
    ClaimedOs.MACOS: frozenset({1.0, 2.0}),
    ClaimedOs.LINUX: frozenset({1.0, 1.25, 1.5, 2.0}),
}
_SOFTWARE_GPU_RE = re.compile(r"basic render|swiftshader|llvmpipe|software|microsoft basic", re.IGNORECASE)
#: Старі інтегровані GPU Intel: їх не ставили в машини з великою кількістю потоків.
_OLD_INTEL_IGPU_RE = re.compile(
    r"UHD Graphics 620|HD Graphics (?:[456]\d{2,3})|Iris\(TM\) Graphics 6100", re.IGNORECASE
)
_OLD_IGPU_MAX_THREADS = 8
_FOREIGN_BROWSER_UA_RE = re.compile(r"Edg/|OPR/|Brave|Firefox|YaBrowser", re.IGNORECASE)
_FULL_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def host_os() -> ClaimedOs:
    if sys.platform == "win32":
        return ClaimedOs.WINDOWS
    if sys.platform == "darwin":
        return ClaimedOs.MACOS
    return ClaimedOs.LINUX


def chromium_brands(major: int, full_version: str | None = None) -> list[tuple[str, str]]:
    """Список брендів ``navigator.userAgentData.brands`` рівно так, як його будує Chromium.

    Сід — мажорна версія: від неї залежать «GREASE»-бренд (``Not-A.Brand``, ``Not(A:Brand``…), його версія
    та порядок записів. Якщо передано ``full_version`` — повертає ``fullVersionList``.
    """
    seed = major
    grease_name = f"Not{_GREASE_CHARS[seed % 11]}A{_GREASE_CHARS[(seed + 1) % 11]}Brand"
    grease_version = _GREASE_VERSIONS[seed % 3]
    version = str(major) if full_version is None else full_version
    order = _BRAND_ORDERS[seed % 6]
    slots: list[tuple[str, str] | None] = [None, None, None]
    slots[order[0]] = (grease_name, grease_version if full_version is None else f"{grease_version}.0.0.0")
    slots[order[1]] = ("Chromium", version)
    slots[order[2]] = (_PRODUCT_BRAND, version)
    return [slot for slot in slots if slot is not None]


def sec_ch_ua(brands: list[tuple[str, str]]) -> str:
    return ", ".join(f'"{name}";v="{version}"' for name, version in brands)


def build_user_agent(claimed_os: ClaimedOs, major: int) -> str:
    """Скорочений (reduced) UA сучасного Chrome: ``Chrome/<major>.0.0.0``."""
    return (
        f"Mozilla/5.0 ({_UA_SYSTEM_TOKEN[claimed_os]}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


def parse_browser_version(version: str | None, fallback_ua: str = "") -> tuple[int, str]:
    """``(major, full_version)``. Якщо версія рушія невідома — береться з наявного UA."""
    if version and _FULL_VERSION_RE.match(version):
        return int(version.split(".")[0]), version
    if version and version.split(".")[0].isdigit():
        return int(version.split(".")[0]), f"{int(version.split('.')[0])}.0.0.0"
    match = re.search(r"Chrome/(\d+)", fallback_ua)
    major = int(match.group(1)) if match else 140
    return major, f"{major}.0.0.0"


# ── Мови ───────────────────────────────────────────────────────────────────
def normalize_languages(locale: str) -> tuple[list[str], str]:
    """``(navigator.languages, Accept-Language)`` у форматі Chrome.

    ``uk-UA`` → ``['uk-UA','uk','en-US','en']`` і ``uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7``;
    ``en-GB`` → ``['en-GB','en-US','en']``; ``en-US`` → ``['en-US','en']``.
    """
    base = locale.split("-")[0]
    if base == "en":
        languages = [locale, *(["en-US"] if locale != "en-US" else []), "en"]
    else:
        languages = [locale, *([base] if base != locale else []), "en-US", "en"]
    unique = list(dict.fromkeys(languages))
    parts = [unique[0]] + [f"{lang};q={max(1.0 - 0.1 * i, 0.1):.1f}" for i, lang in enumerate(unique[1:], 1)]
    return unique, ",".join(parts)


# ── Реалістичність ─────────────────────────────────────────────────────────
def realism_issues(fingerprint: Mapping[str, Any], claimed_os: ClaimedOs) -> list[str]:
    """Чому відбиток вважається нереалістичним (порожній список — усе гаразд)."""
    issues: list[str] = []
    navigator: Mapping[str, Any] = fingerprint.get("navigator") or {}
    screen: Mapping[str, Any] = fingerprint.get("screen") or {}
    gpu: Mapping[str, Any] = fingerprint.get("videoCard") or {}
    uad: Mapping[str, Any] = navigator.get("userAgentData") or {}
    ua = str(navigator.get("userAgent", ""))

    if "Chrome/" not in ua or _FOREIGN_BROWSER_UA_RE.search(ua):
        issues.append("UA не є чистим Chrome")
    if navigator.get("platform") != _OS_PLATFORM[claimed_os]:
        issues.append(f"navigator.platform={navigator.get('platform')!r} не відповідає {claimed_os.value}")
    if uad.get("platform") != _OS_UACH_PLATFORM[claimed_os]:
        issues.append("userAgentData.platform не відповідає ОС")
    renderer = str(gpu.get("renderer", ""))
    if not renderer or _SOFTWARE_GPU_RE.search(renderer):
        issues.append(f"програмний або порожній GPU: {renderer!r}")
    dpr = screen.get("devicePixelRatio")
    if not isinstance(dpr, (int, float)) or dpr not in _ALLOWED_DPR[claimed_os]:
        issues.append(f"нестандартний DPR {dpr!r}")
    width, height = screen.get("width", 0), screen.get("height", 0)
    if width < 1280 or height < 720:
        issues.append(f"занадто малий екран {width}x{height}")
    if screen.get("availWidth", 0) > width or screen.get("availHeight", 0) > height:
        issues.append("доступна область більша за екран")
    memory = navigator.get("deviceMemory")
    if not isinstance(memory, (int, float)) or memory < 2:
        issues.append(f"deviceMemory={memory!r} нереалістична для десктопа")
    cores = navigator.get("hardwareConcurrency")
    if not isinstance(cores, int) or not 2 <= cores <= 64:
        issues.append(f"hardwareConcurrency={cores!r}")
    if isinstance(cores, int) and cores > _OLD_IGPU_MAX_THREADS and _OLD_INTEL_IGPU_RE.search(renderer):
        issues.append(f"{cores} потоків неможливі зі старим інтегрованим GPU ({renderer[:40]!r})")
    if navigator.get("vendor") != "Google Inc.":
        issues.append("navigator.vendor не Google Inc.")
    return issues


def derive_viewport(fingerprint: Mapping[str, Any], claimed_os: ClaimedOs) -> tuple[int, int]:
    """Внутрішній розмір вікна: максимізоване вікно мінус інтерфейс браузера."""
    screen: Mapping[str, Any] = fingerprint.get("screen") or {}
    screen_w = int(screen.get("width") or 0) or 10_000
    screen_h = int(screen.get("height") or 0) or 10_000
    outer_w = int(screen.get("outerWidth") or screen.get("availWidth") or screen.get("width") or 1366)
    outer_h = int(screen.get("outerHeight") or screen.get("availHeight") or screen.get("height") or 768)
    width = min(max(1000, outer_w), screen_w)  # вікно не ширше за екран
    height = min(max(600, outer_h - _BROWSER_UI_HEIGHT[claimed_os]), screen_h)
    return width, height


def _normalize(
    raw: Mapping[str, Any], *, claimed_os: ClaimedOs, locale: str, rng: random.Random
) -> dict[str, Any]:
    """Виправляє відомі вади згенерованого відбитка. Версія Chrome тут не чіпається (див. retarget)."""
    fp = copy.deepcopy(dict(raw))
    navigator = fp.setdefault("navigator", {})
    languages, accept_language = normalize_languages(locale)
    navigator["language"] = languages[0]
    navigator["languages"] = languages
    navigator["webdriver"] = False
    memory = navigator.get("deviceMemory")
    if isinstance(memory, (int, float)):
        navigator["deviceMemory"] = min(memory, 8)  # Chrome округлює/обмежує до 8 ГБ
    navigator["maxTouchPoints"] = 0  # десктоп без сенсорного екрана

    screen = fp.setdefault("screen", {})
    if not screen.get("outerWidth"):
        screen["outerWidth"] = screen.get("availWidth") or screen.get("width")
    if not screen.get("outerHeight"):
        screen["outerHeight"] = screen.get("availHeight") or screen.get("height")
    for outer, full in (("outerWidth", "width"), ("outerHeight", "height")):
        if screen.get(outer) and screen.get(full):
            screen[outer] = min(screen[outer], screen[full])  # у даних трапляється outer > screen
    # window.inner* інжектор все одно пропускає (0), реальні значення дає емуляція viewport.

    # Ці розділи browserforge-інжектор НЕ застосовує (використовує лише navigator, screen, videoCard,
    # кодеки, батарею, mockWebRTC, slim). Тримати їх — роздувати файл і створювати хибне враження покриття
    # (у зразках там трапляються сліди інших браузерів, напр. плагін «Brave …»).
    for unused in ("pluginsData", "multimediaDevices", "fonts"):
        fp.pop(unused, None)
    fp["mockWebRTC"] = False
    fp["slim"] = False
    fp["headers"] = {"Accept-Language": accept_language}  # решту додає retarget під реальну версію
    _ = rng  # зарезервовано: майбутня варіативність (напр. висота інтерфейсу)
    return fp


# ── Підгонка під реальний рушій і застосування ─────────────────────────────
def retarget(profile: BrowserProfile, browser_version: str | None) -> dict[str, Any]:
    """Копія відбитка з UA, брендами й client hints, узгодженими з РЕАЛЬНОЮ версією рушія.

    Справжній браузер оновлюється — і його UA теж: тому версію не «заморожуємо» в профілі.
    Усе інше (ОС, екран, GPU, ядра, пам'ять…) лишається незмінним.
    """
    fp = copy.deepcopy(dict(profile.fingerprint))
    navigator = fp.setdefault("navigator", {})
    major, full = parse_browser_version(browser_version, str(navigator.get("userAgent", "")))
    claimed_os = profile.claimed_os

    user_agent = build_user_agent(claimed_os, major)
    navigator["userAgent"] = user_agent
    navigator["appVersion"] = user_agent.removeprefix("Mozilla/")
    navigator["platform"] = _OS_PLATFORM[claimed_os]

    previous = navigator.get("userAgentData") or {}
    navigator["userAgentData"] = {
        "brands": [{"brand": n, "version": v} for n, v in chromium_brands(major)],
        "mobile": False,
        "platform": _OS_UACH_PLATFORM[claimed_os],
        "architecture": previous.get("architecture") or "x86",
        "bitness": previous.get("bitness") or "64",
        "model": "",
        "platformVersion": previous.get("platformVersion") or "15.0.0",
        "uaFullVersion": full,
        "fullVersionList": [{"brand": n, "version": v} for n, v in chromium_brands(major, full)],
    }
    fp["headers"] = {**client_hint_headers(profile, major), "User-Agent": user_agent}
    return fp


def client_hint_headers(profile: BrowserProfile, major: int) -> dict[str, str]:
    """HTTP-заголовки, які справжній Chrome шле сам (low-entropy client hints) + Accept-Language.

    Високо-ентропійні hints (full-version-list, platform-version, arch…) НЕ додаємо: справжній браузер
    відправляє їх лише на запит сервера (Accept-CH), а статичні extra-заголовки пішли б у кожен запит.
    """
    _, accept_language = normalize_languages(profile.locale)
    return {
        "accept-language": accept_language,
        "sec-ch-ua": sec_ch_ua(chromium_brands(major)),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": f'"{_OS_UACH_PLATFORM[profile.claimed_os]}"',
    }


def profile_context_options(profile: BrowserProfile, browser_version: str | None) -> dict[str, Any]:
    """Параметри ``browser.new_context``, що фіксують середовище з профілю."""
    fp = retarget(profile, browser_version)
    major, _ = parse_browser_version(browser_version, profile.user_agent)
    screen_w, screen_h = profile.screen_size
    options: dict[str, Any] = {
        "user_agent": fp["navigator"]["userAgent"],
        "locale": profile.locale,
        "viewport": {"width": profile.viewport[0], "height": profile.viewport[1]},
        "screen": {"width": screen_w, "height": screen_h},
        "device_scale_factor": profile.device_scale_factor,
        "color_scheme": "light",
        "is_mobile": False,
        "has_touch": False,
        "extra_http_headers": client_hint_headers(profile, major),
    }
    if profile.timezone_id:
        options["timezone_id"] = profile.timezone_id
    return options


class _Dumpable:
    """Аналог ``browserforge.Fingerprint`` для ``InjectFunction`` (потрібен лише ``dumps``)."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._json = json.dumps(data, ensure_ascii=False)

    def dumps(self) -> str:
        return self._json


def build_init_script(profile: BrowserProfile, browser_version: str | None) -> str:
    """JS, що підміняє navigator/screen/WebGL/userAgentData тощо ще до скриптів сторінки."""
    return InjectFunction(cast(Fingerprint, _Dumpable(retarget(profile, browser_version))))


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


# ── Генератор ──────────────────────────────────────────────────────────────
class BrowserForgeProfileGenerator:
    """Реалізує порт ``ProfileGenerator`` поверх browserforge."""

    def __init__(
        self,
        settings: FingerprintSettings,
        *,
        locale: str,
        timezone_id: str | None,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_attempts: int = 60,
    ) -> None:
        self._claimed_os = settings.claimed_os or host_os()
        self._locale = locale
        self._timezone_id = timezone_id
        self._rng = rng or random.Random()  # noqa: S311 — не криптографія
        self._clock = clock
        self._max_attempts = max_attempts
        self._generator: FingerprintGenerator | None = None

    def generate(self) -> BrowserProfile:
        """Новий реалістичний профіль. Відкидає нереалістичні зразки й генерує, доки не вийде хороший."""
        if self._generator is None:
            self._generator = FingerprintGenerator()
        rejected: list[str] = []
        for _ in range(self._max_attempts):
            raw = json.loads(
                self._generator.generate(
                    browser=("chrome",),
                    os=(self._claimed_os.value,),
                    device="desktop",
                    locale=(self._locale,),
                    screen=Screen(min_width=1280, min_height=720),
                ).dumps()
            )
            fingerprint = _normalize(raw, claimed_os=self._claimed_os, locale=self._locale, rng=self._rng)
            issues = realism_issues(fingerprint, self._claimed_os)
            if not issues:
                return BrowserProfile(
                    created_at=self._clock(),
                    claimed_os=self._claimed_os,
                    locale=self._locale,
                    timezone_id=self._timezone_id,
                    viewport=derive_viewport(fingerprint, self._claimed_os),
                    fingerprint=fingerprint,
                )
            rejected.extend(issues)
        raise ProfileGenerationError(
            f"За {self._max_attempts} спроб не вдалося отримати реалістичний профіль для "
            f"{self._claimed_os.value}. Типові причини відхилення: {sorted(set(rejected))[:4]}"
        )
