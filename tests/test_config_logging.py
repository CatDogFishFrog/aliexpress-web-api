from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from aliweb.domain.errors import ConfigurationError, SecretMissingError
from aliweb.domain.secret import Secret
from aliweb.infrastructure.browser.pool import build_launch_options
from aliweb.infrastructure.browser.session import build_context_options
from aliweb.infrastructure.config import (
    BrowserSettings,
    DumpPolicy,
    LoggingSettings,
    ProxySettings,
    Settings,
)
from aliweb.infrastructure.logging_setup import configure_logging
from aliweb.infrastructure.redaction import Redactor


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings.from_env({}, dotenv_path=None)
        assert s.browser.headless is True and s.browser.viewport == (1366, 768)
        assert s.diagnostics.policy is DumpPolicy.ON_ERROR and s.session_secret is None

    def test_parsing(self) -> None:
        env = {
            "ALIWEB_SESSION_SECRET": "x" * 20,
            "ALIWEB_HEADLESS": "No",
            "ALIWEB_VIEWPORT": "none",
            "ALIWEB_DUMP_POLICY": "ALWAYS",
            "ALIWEB_PROXY_SERVER": "http://p:8080",
            "ALIWEB_PROXY_PASSWORD": "pw-secret",
            "ALIWEB_BASE_URL": "https://aliexpress.us/",
            "ALIWEB_LOGOUT_URL": "  ",
        }
        s = Settings.from_env(env, dotenv_path=None)
        assert s.browser.headless is False and s.browser.viewport is None
        assert s.diagnostics.policy is DumpPolicy.ALWAYS
        assert s.browser.proxy and s.browser.proxy.password == Secret("pw-secret")
        assert s.aliexpress.base_url == "https://aliexpress.us" and s.aliexpress.logout_url is None
        assert s.require_secret() == Secret("x" * 20)

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("ALIWEB_HEADLESS", "maybe"),
            ("ALIWEB_NAV_TIMEOUT_MS", "abc"),
            ("ALIWEB_NAV_TIMEOUT_MS", "10"),
            ("ALIWEB_VIEWPORT", "wide"),
            ("ALIWEB_VIEWPORT", "10x10"),
            ("ALIWEB_DUMP_POLICY", "sometimes"),
        ],
    )
    def test_invalid_values_rejected(self, key: str, value: str) -> None:
        with pytest.raises(ConfigurationError):
            Settings.from_env({key: value}, dotenv_path=None)

    def test_secret_required_and_min_length(self) -> None:
        with pytest.raises(SecretMissingError):
            Settings.from_env({}, dotenv_path=None).require_secret()
        with pytest.raises(SecretMissingError):
            Settings.from_env({"ALIWEB_SESSION_SECRET": "short"}, dotenv_path=None).require_secret()

    def test_dotenv_is_overridden_by_environ_and_not_exported(self, tmp_path: Path) -> None:
        import os

        env_file = tmp_path / ".env"
        env_file.write_text("ALIWEB_LOCALE=uk-UA\nALIWEB_TIMEZONE=Europe/Kyiv\n", encoding="utf-8")
        s = Settings.from_env({"ALIWEB_LOCALE": "en-GB"}, dotenv_path=env_file)
        assert s.browser.locale == "en-GB" and s.browser.timezone_id == "Europe/Kyiv"
        assert "ALIWEB_TIMEZONE" not in os.environ  # .env не «протікає» в середовище процесу


class TestBrowserOptions:
    def test_launch_defaults_minimal_args(self) -> None:
        opts = build_launch_options(BrowserSettings(), headless=True)
        assert opts["headless"] is True and "channel" not in opts and "proxy" not in opts

    def test_launch_with_channel_and_proxy(self) -> None:
        settings = BrowserSettings(channel="chrome", proxy=ProxySettings("http://p:1", "u", Secret("pass1")))
        opts = build_launch_options(settings, headless=False)
        assert opts["channel"] == "chrome" and opts["headless"] is False
        assert opts["proxy"] == {"server": "http://p:1", "username": "u", "password": "pass1"}

    def test_context_options(self) -> None:
        state = {"cookies": [], "origins": []}
        opts = build_context_options(BrowserSettings(), state)
        assert opts["viewport"] == {"width": 1366, "height": 768} and opts["storage_state"] == state
        assert "user_agent" not in opts  # UA свідомо не підміняємо
        opts2 = build_context_options(BrowserSettings(viewport=None, timezone_id="Europe/Kyiv"), None)
        assert opts2["no_viewport"] is True and "storage_state" not in opts2
        assert opts2["timezone_id"] == "Europe/Kyiv"


class TestRedaction:
    def test_registered_and_patterns(self) -> None:
        r = Redactor()
        r.register(Secret("super-secret-value"))
        text = 'x super-secret-value; password=abc123 "token": "tok9" Bearer abc.def'
        out = r.redact(text)
        for leaked in ("super-secret-value", "abc123", "tok9", "abc.def"):
            assert leaked not in out

    def test_exact_mode_does_not_touch_patterns(self) -> None:
        r = Redactor()
        r.register("hunter22")
        assert r.redact_exact("password=zzz hunter22") == "password=zzz ***"

    def test_short_secrets_ignored(self) -> None:
        r = Redactor()
        r.register("ab")
        assert r.redact("abab") == "abab"


class TestLogging:
    def test_secrets_masked_in_message_extra_and_exception(self, tmp_path: Path) -> None:
        redactor = Redactor()
        redactor.register("TOPSECRET-123")
        logger = configure_logging(
            LoggingSettings(level="DEBUG", json=True, to_file=True), logs_dir=tmp_path, redactor=redactor
        )
        try:
            logger.info("msg TOPSECRET-123 %s", "TOPSECRET-123", extra={"ctx": "TOPSECRET-123", "n": 1})
            try:
                raise RuntimeError("boom TOPSECRET-123")
            except RuntimeError:
                logger.exception("failed")
            for handler in logger.handlers:
                handler.flush()
            raw = (tmp_path / "aliweb.log").read_text(encoding="utf-8")
            assert "TOPSECRET-123" not in raw
            first = json.loads(raw.splitlines()[0])
            assert first["ctx"] == "***" and first["n"] == 1 and first["level"] == "INFO"
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()

    def test_bad_level_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="рівень"):
            configure_logging(LoggingSettings(level="LOUD"), logs_dir=tmp_path, redactor=Redactor())
        logging.getLogger("aliweb").handlers.clear()
