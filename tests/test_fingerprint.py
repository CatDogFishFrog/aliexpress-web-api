"""Профіль середовища: реалістичність, підгонка під реальний рушій, опції контексту, генератор."""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any

import pytest

from aliweb.domain.errors import ProfileGenerationError
from aliweb.domain.profile import BrowserProfile, ClaimedOs
from aliweb.infrastructure.browser import fingerprint as fingerprint_module
from aliweb.infrastructure.browser.fingerprint import (
    BrowserForgeStealthProfileGenerator,
    build_init_script,
    build_user_agent,
    chromium_brands,
    client_hint_headers,
    derive_viewport,
    normalize_languages,
    parse_browser_version,
    profile_context_options,
    realism_issues,
    retarget,
    sec_ch_ua,
    warn_on_environment_drift,
)
from aliweb.infrastructure.browser.session import build_context_options
from aliweb.infrastructure.config import BrowserSettings, FingerprintSettings
from tests.conftest import make_profile

REAL_VERSION = "156.0.8078.4"  # версія Chrome for Testing з логу користувача


class TestChromiumBrands:
    """Алгоритм GREASE-брендів Chromium звірено з відомими реальними значеннями sec-ch-ua."""

    @pytest.mark.parametrize(
        ("major", "expected"),
        [
            (120, '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'),
            (124, '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'),
            (131, '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'),
            (133, '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"'),
            (147, '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"'),
        ],
    )
    def test_known_real_world_values(self, major: int, expected: str) -> None:
        assert sec_ch_ua(chromium_brands(major)) == expected

    def test_full_version_list_uses_full_versions(self) -> None:
        brands = dict(chromium_brands(156, REAL_VERSION))
        assert brands["Chromium"] == REAL_VERSION and brands["Google Chrome"] == REAL_VERSION
        assert any(v.endswith(".0.0.0") for n, v in brands.items() if n.startswith("Not"))

    @pytest.mark.parametrize("major", range(100, 175))
    def test_always_three_brands_with_chrome_and_chromium(self, major: int) -> None:
        names = [n for n, _ in chromium_brands(major)]
        assert len(names) == 3 and "Chromium" in names and "Google Chrome" in names
        assert not any("Brave" in n for n in names)


class TestUserAgentAndVersions:
    def test_user_agent_per_os(self) -> None:
        assert "Windows NT 10.0; Win64; x64" in build_user_agent(ClaimedOs.WINDOWS, 156)
        assert "Macintosh; Intel Mac OS X 10_15_7" in build_user_agent(ClaimedOs.MACOS, 156)
        assert "X11; Linux x86_64" in build_user_agent(ClaimedOs.LINUX, 156)
        assert build_user_agent(ClaimedOs.WINDOWS, 156).endswith("Chrome/156.0.0.0 Safari/537.36")

    def test_parse_browser_version(self) -> None:
        assert parse_browser_version(REAL_VERSION) == (156, REAL_VERSION)
        assert parse_browser_version("141.0.3537.71") == (141, "141.0.3537.71")
        assert parse_browser_version("141") == (141, "141.0.0.0")
        ua = "Mozilla/5.0 Chrome/147.0.0.0 Safari/537.36"
        assert parse_browser_version(None, ua) == (147, "147.0.0.0")


class TestLanguages:
    @pytest.mark.parametrize(
        ("locale", "languages", "accept"),
        [
            ("en-US", ["en-US", "en"], "en-US,en;q=0.9"),
            ("uk-UA", ["uk-UA", "uk", "en-US", "en"], "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7"),
            ("en-GB", ["en-GB", "en-US", "en"], "en-GB,en-US;q=0.9,en;q=0.8"),
            ("de", ["de", "en-US", "en"], "de,en-US;q=0.9,en;q=0.8"),
        ],
    )
    def test_chrome_style_languages(self, locale: str, languages: list[str], accept: str) -> None:
        assert normalize_languages(locale) == (languages, accept)

    def test_no_whitespace_in_languages(self) -> None:
        for locale in ("uk-UA", "ru-UA", "pt-BR", "zh-CN"):
            assert all(lang == lang.strip() for lang in normalize_languages(locale)[0])


def good_fingerprint() -> dict[str, Any]:
    """Мінімальний «хороший» відбиток Windows для перевірки правил реалістичності."""
    return {
        "navigator": {
            "userAgent": build_user_agent(ClaimedOs.WINDOWS, 147),
            "platform": "Win32",
            "vendor": "Google Inc.",
            "deviceMemory": 8,
            "hardwareConcurrency": 8,
            "userAgentData": {"platform": "Windows", "platformVersion": "15.0.0"},
        },
        "screen": {
            "width": 1920,
            "height": 1080,
            "availWidth": 1920,
            "availHeight": 1040,
            "devicePixelRatio": 1,
        },
        "videoCard": {"renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)"},
    }


class TestRealismRules:
    def test_good_fingerprint_passes(self) -> None:
        assert realism_issues(good_fingerprint(), ClaimedOs.WINDOWS) == []

    @pytest.mark.parametrize(
        ("mutation", "fragment"),
        [
            (lambda f: f["navigator"].update(platform="Linux x86_64"), "platform"),
            (lambda f: f["navigator"]["userAgentData"].update(platform="Linux"), "userAgentData"),
            (lambda f: f["navigator"].update(userAgent=f["navigator"]["userAgent"] + " Edg/147"), "Chrome"),
            (lambda f: f["navigator"].update(deviceMemory=0.5), "deviceMemory"),
            (lambda f: f["navigator"].update(hardwareConcurrency=1), "hardwareConcurrency"),
            (lambda f: f["navigator"].update(vendor="Apple Computer, Inc."), "vendor"),
            (lambda f: f["screen"].update(devicePixelRatio=1.1458), "DPR"),
            (lambda f: f["screen"].update(width=1024, height=600), "екран"),
            (lambda f: f["screen"].update(availWidth=2000), "доступна"),
            (lambda f: f["videoCard"].update(renderer="Microsoft Basic Render Driver"), "GPU"),
            (lambda f: f["videoCard"].update(renderer="Google SwiftShader"), "GPU"),
            (
                lambda f: (
                    f["navigator"].update(hardwareConcurrency=20),
                    f["videoCard"].update(renderer="ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11)"),
                ),
                "потоків",
            ),
        ],
    )
    def test_unrealistic_samples_are_rejected(self, mutation: Any, fragment: str) -> None:
        fp = good_fingerprint()
        mutation(fp)
        issues = realism_issues(fp, ClaimedOs.WINDOWS)
        assert any(fragment in issue for issue in issues), issues

    def test_macos_with_linux_platform_rejected(self) -> None:
        fp = good_fingerprint()
        fp["navigator"]["userAgentData"]["platform"] = "macOS"
        assert realism_issues(fp, ClaimedOs.MACOS)  # navigator.platform лишився Win32


class TestViewport:
    def test_viewport_is_outer_window_minus_browser_ui(self) -> None:
        fp = {"screen": {"outerWidth": 1920, "outerHeight": 1040, "width": 1920, "height": 1080}}
        assert derive_viewport(fp, ClaimedOs.WINDOWS) == (1920, 1040 - 85)
        assert derive_viewport(fp, ClaimedOs.MACOS) == (1920, 1040 - 79)

    def test_window_is_never_wider_or_taller_than_the_screen(self) -> None:
        """Регресія: зразок macOS мав outerWidth=1524 при екрані 1512 — вікно виходило за екран."""
        fp = {"screen": {"width": 1512, "height": 982, "outerWidth": 1524, "outerHeight": 1100}}
        width, height = derive_viewport(fp, ClaimedOs.MACOS)
        assert width <= 1512 and height <= 982

    def test_falls_back_to_avail_or_screen_and_never_tiny(self) -> None:
        assert derive_viewport({"screen": {"availWidth": 1536, "availHeight": 824}}, ClaimedOs.LINUX) == (
            1536,
            824 - 88,
        )
        assert derive_viewport({}, ClaimedOs.WINDOWS)[0] >= 1000


class TestRetarget:
    """UA і client hints підганяються під РЕАЛЬНИЙ рушій, решта середовища — незмінна."""

    def test_follows_real_engine_version(self) -> None:
        profile = make_profile()  # у файлі UA каже Chrome/147
        fp = retarget(profile, REAL_VERSION)
        assert "Chrome/156.0.0.0" in fp["navigator"]["userAgent"]
        uad = fp["navigator"]["userAgentData"]
        assert uad["uaFullVersion"] == REAL_VERSION and uad["platform"] == "Windows" and not uad["mobile"]
        assert {b["brand"] for b in uad["brands"]} >= {"Chromium", "Google Chrome"}
        assert fp["headers"]["sec-ch-ua"] == sec_ch_ua(chromium_brands(156))

    def test_does_not_mutate_the_stored_profile(self) -> None:
        profile = make_profile()
        before = copy.deepcopy(dict(profile.fingerprint))
        retarget(profile, REAL_VERSION)
        assert dict(profile.fingerprint) == before

    def test_environment_stays_identical_across_browser_updates(self) -> None:
        profile = make_profile()
        a, b = retarget(profile, "156.0.8078.4"), retarget(profile, "157.0.8100.2")
        assert a["navigator"]["userAgent"] != b["navigator"]["userAgent"]  # версія оновилась
        for key in ("hardwareConcurrency", "deviceMemory", "tag"):
            assert a["navigator"][key] == b["navigator"][key]  # а «залізо» — те саме
        assert a["screen"] == b["screen"] and a["videoCard"] == b["videoCard"]

    def test_unknown_version_keeps_major_from_profile(self) -> None:
        assert "Chrome/147.0.0.0" in retarget(make_profile(), None)["navigator"]["userAgent"]


class TestContextOptions:
    def test_profile_pins_the_whole_environment(self) -> None:
        options = profile_context_options(make_profile(), REAL_VERSION)
        assert options["user_agent"].endswith("Chrome/156.0.0.0 Safari/537.36")
        assert options["locale"] == "en-US" and options["timezone_id"] == "Europe/Kyiv"
        assert options["viewport"] == {"width": 1536, "height": 731}
        assert options["screen"] == {"width": 1536, "height": 864}
        assert options["device_scale_factor"] == 1.25
        assert options["color_scheme"] == "light" and options["is_mobile"] is False

    def test_only_low_entropy_client_hints_are_sent_as_headers(self) -> None:
        headers = profile_context_options(make_profile(), REAL_VERSION)["extra_http_headers"]
        assert set(headers) == {"accept-language", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"}
        assert headers["sec-ch-ua-platform"] == '"Windows"' and headers["sec-ch-ua-mobile"] == "?0"
        assert "Brave" not in json.dumps(headers)

    def test_timezone_only_when_pinned(self) -> None:
        profile = make_profile()
        unpinned = BrowserProfile(
            created_at=profile.created_at,
            claimed_os=profile.claimed_os,
            locale=profile.locale,
            timezone_id=None,
            viewport=profile.viewport,
            fingerprint=profile.fingerprint,
        )
        assert "timezone_id" not in profile_context_options(unpinned, REAL_VERSION)

    def test_build_context_options_without_profile_is_plain_browser(self) -> None:
        options = build_context_options(BrowserSettings(), None)
        assert "user_agent" not in options and options["viewport"] == {"width": 1366, "height": 768}

    def test_profile_overrides_general_settings(self) -> None:
        settings = BrowserSettings(viewport=None, locale="de-DE", timezone_id="Europe/Berlin")
        options = build_context_options(settings, {"cookies": []}, make_profile(), REAL_VERSION)
        assert "no_viewport" not in options  # емуляція вікна з профілю, а не «справжнє» вікно
        assert options["locale"] == "en-US" and options["timezone_id"] == "Europe/Kyiv"
        assert options["storage_state"] == {"cookies": []}

    def test_header_helper_matches_profile_locale(self) -> None:
        assert client_hint_headers(make_profile(), 156)["accept-language"] == "en-US,en;q=0.9"


class TestInitScript:
    def test_script_embeds_retargeted_fingerprint(self) -> None:
        script = build_init_script(make_profile(), REAL_VERSION)
        assert "Chrome/156.0.0.0" in script and "Chrome/147.0.0.0" not in script
        assert REAL_VERSION in script  # uaFullVersion у high-entropy userAgentData
        assert script.lstrip().startswith("(") or "function" in script  # це JS


class TestGeneratorAdapter:
    def test_translates_generation_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Generator:
            def __init__(self, **_: Any) -> None:
                pass

            def generate(self) -> Any:
                raise fingerprint_module.bfs.GenerationError("no realistic sample")

        monkeypatch.setattr(fingerprint_module.bfs, "FingerprintGenerator", _Generator)
        generator = BrowserForgeStealthProfileGenerator(
            FingerprintSettings(ClaimedOs.WINDOWS), locale="uk-UA", timezone_id="Europe/Kyiv"
        )
        with pytest.raises(ProfileGenerationError, match="no realistic sample"):
            generator.generate()


class TestRealGeneratorOutput:
    """Перевіряє профілі, створені форком, через адаптер Aliweb для кожної ОС."""

    @pytest.mark.parametrize("claimed_os", list(ClaimedOs))
    def test_generated_profiles_are_coherent(self, claimed_os: ClaimedOs) -> None:
        generator = BrowserForgeStealthProfileGenerator(
            FingerprintSettings(claimed_os), locale="uk-UA", timezone_id="Europe/Kyiv"
        )
        for _ in range(8):
            profile = generator.generate()
            assert realism_issues(profile.fingerprint, claimed_os) == []
            fp = retarget(profile, REAL_VERSION)
            ua = fp["navigator"]["userAgent"]
            assert re.search(r"Chrome/156\.0\.0\.0", ua) and "Brave" not in json.dumps(fp)
            assert (
                fp["navigator"]["userAgentData"]["platform"]
                == {
                    ClaimedOs.WINDOWS: "Windows",
                    ClaimedOs.MACOS: "macOS",
                    ClaimedOs.LINUX: "Linux",
                }[claimed_os]
            )
            width, height = profile.viewport
            screen_w, screen_h = profile.screen_size
            assert 1000 <= width <= screen_w and 600 <= height <= screen_h
            assert profile.fingerprint["navigator"]["deviceMemory"] <= 8
            assert profile.device_scale_factor in {1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 3}
            options = profile_context_options(profile, REAL_VERSION)
            assert options["viewport"]["width"] == width

    def test_profiles_differ_between_accounts(self) -> None:
        generator = BrowserForgeStealthProfileGenerator(
            FingerprintSettings(ClaimedOs.WINDOWS), locale="en-US", timezone_id=None
        )
        gpus = {generator.generate().gpu_renderer for _ in range(12)}
        assert len(gpus) > 2  # різні акаунти — різні «комп'ютери»

    def test_profile_survives_json_roundtrip(self) -> None:
        profile = BrowserForgeStealthProfileGenerator(
            FingerprintSettings(ClaimedOs.WINDOWS), locale="en-US", timezone_id=None
        ).generate()
        assert json.loads(json.dumps(dict(profile.fingerprint))) == dict(profile.fingerprint)


class TestDriftWarnings:
    def test_warns_when_os_or_locale_differ(self, caplog: pytest.LogCaptureFixture) -> None:
        profile = make_profile()  # windows, en-US
        with caplog.at_level(logging.WARNING):
            warn_on_environment_drift(profile, configured_locale="uk-UA", current_os=ClaimedOs.LINUX)
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "іншу ОС" in text and "ALIWEB_LOCALE" in text

    def test_silent_when_consistent(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            warn_on_environment_drift(make_profile(), configured_locale="en-US", current_os=ClaimedOs.WINDOWS)
        assert caplog.records == []
