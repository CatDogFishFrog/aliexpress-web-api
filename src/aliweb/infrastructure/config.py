"""Конфігурація з змінних середовища та ``.env``.

Єдине місце, де читається ``os.environ`` (арх-тест це перевіряє). Решта коду отримує
типізовані незмінні налаштування через параметри конструкторів (див. ``bootstrap``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from dotenv import dotenv_values

from aliweb.domain.errors import ConfigurationError, SecretMissingError
from aliweb.domain.secret import Secret

ENV_PREFIX = "ALIWEB_"
MIN_SECRET_LENGTH = 16


class DumpPolicy(StrEnum):
    ON_ERROR = "on_error"
    ALWAYS = "always"
    NEVER = "never"


@dataclass(frozen=True, slots=True)
class ProxySettings:
    server: str
    username: str | None = None
    password: Secret | None = None


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    """Налаштування браузера СКРАПЕРА (завжди браузер patchright). Браузер для входу — див. LoginMode."""

    headless: bool = True
    locale: str = "en-US"
    timezone_id: str | None = None
    viewport: tuple[int, int] | None = (1366, 768)  # None = no_viewport
    nav_timeout_ms: int = 45_000
    action_timeout_ms: int = 15_000
    slow_mo_ms: int = 0
    proxy: ProxySettings | None = None
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiagnosticsSettings:
    policy: DumpPolicy = DumpPolicy.ON_ERROR
    keep: int = 50
    screenshot_full_page: bool = True


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    level: str = "INFO"
    json: bool = False
    to_file: bool = True


@dataclass(frozen=True, slots=True)
class AliExpressSettings:
    base_url: str = "https://www.aliexpress.com"
    login_url: str = "https://login.aliexpress.com/"
    session_probe_url: str = "https://www.aliexpress.com/p/order/index.html"
    logout_url: str | None = None
    session_verify_ttl_s: float = 600.0


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path = Path("./data")
    session_secret: Secret | None = None
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    diagnostics: DiagnosticsSettings = field(default_factory=DiagnosticsSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    aliexpress: AliExpressSettings = field(default_factory=AliExpressSettings)

    def require_secret(self) -> Secret:
        """Ключ шифрування або :class:`SecretMissingError` з підказкою, що робити."""
        secret = self.session_secret
        if secret is None or len(secret) < MIN_SECRET_LENGTH:
            raise SecretMissingError(
                f"ALIWEB_SESSION_SECRET не задано або коротший за {MIN_SECRET_LENGTH} символів. "
                "Згенеруйте ключ: `aliweb secret generate` і додайте його в .env"
            )
        return secret

    @property
    def accounts_dir(self) -> Path:
        return self.data_dir / "accounts"

    @property
    def diagnostics_dir(self) -> Path:
        return self.data_dir / "diagnostics"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        dotenv_path: Path | str | None = ".env",
    ) -> Settings:
        """Читає налаштування. Пріоритет: ``environ`` (за замовчуванням os.environ) > ``.env``.

        ``.env`` не потрапляє в ``os.environ`` (не «протікає» в дочірні процеси, зокрема браузер).
        """
        merged: dict[str, str] = {}
        if dotenv_path is not None and Path(dotenv_path).is_file():
            merged.update({k: v for k, v in dotenv_values(dotenv_path).items() if v is not None})
        merged.update(os.environ if environ is None else environ)
        return cls._from_mapping(merged)

    @classmethod
    def _from_mapping(cls, env: Mapping[str, str]) -> Settings:
        def get(name: str) -> str | None:
            value = env.get(ENV_PREFIX + name)
            return value.strip() if value and value.strip() else None

        proxy_server = get("PROXY_SERVER")
        proxy_password = get("PROXY_PASSWORD")
        proxy = (
            ProxySettings(
                server=proxy_server,
                username=get("PROXY_USERNAME"),
                password=Secret(proxy_password) if proxy_password else None,
            )
            if proxy_server
            else None
        )
        secret = get("SESSION_SECRET")
        defaults = cls()
        return cls(
            data_dir=Path(get("DATA_DIR") or defaults.data_dir),
            session_secret=Secret(secret) if secret else None,
            browser=BrowserSettings(
                headless=_to_bool("HEADLESS", get("HEADLESS"), True),
                locale=get("LOCALE") or defaults.browser.locale,
                timezone_id=get("TIMEZONE"),
                viewport=_to_viewport(get("VIEWPORT"), defaults.browser.viewport),
                nav_timeout_ms=_to_int("NAV_TIMEOUT_MS", get("NAV_TIMEOUT_MS"), 45_000, minimum=1000),
                action_timeout_ms=_to_int("ACTION_TIMEOUT_MS", get("ACTION_TIMEOUT_MS"), 15_000, minimum=500),
                slow_mo_ms=_to_int("SLOW_MO_MS", get("SLOW_MO_MS"), 0, minimum=0),
                proxy=proxy,
            ),
            diagnostics=DiagnosticsSettings(
                policy=_to_enum("DUMP_POLICY", get("DUMP_POLICY"), DumpPolicy, DumpPolicy.ON_ERROR),
                keep=_to_int("DUMP_KEEP", get("DUMP_KEEP"), 50, minimum=1),
                screenshot_full_page=_to_bool("SCREENSHOT_FULL_PAGE", get("SCREENSHOT_FULL_PAGE"), True),
            ),
            logging=LoggingSettings(
                level=(get("LOG_LEVEL") or "INFO").upper(),
                json=_to_bool("LOG_JSON", get("LOG_JSON"), False),
                to_file=_to_bool("LOG_FILE", get("LOG_FILE"), True),
            ),
            aliexpress=AliExpressSettings(
                base_url=(get("BASE_URL") or defaults.aliexpress.base_url).rstrip("/"),
                login_url=get("LOGIN_URL") or defaults.aliexpress.login_url,
                session_probe_url=get("SESSION_PROBE_URL") or defaults.aliexpress.session_probe_url,
                logout_url=get("LOGOUT_URL"),
                session_verify_ttl_s=float(
                    _to_int("SESSION_VERIFY_TTL_S", get("SESSION_VERIFY_TTL_S"), 600, minimum=0)
                ),
            ),
        )


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _to_bool(name: str, raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigurationError(f"{ENV_PREFIX}{name}: очікується true/false, отримано {raw!r}")


def _to_int(name: str, raw: str | None, default: int, *, minimum: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{ENV_PREFIX}{name}: очікується ціле число, отримано {raw!r}") from exc
    if value < minimum:
        raise ConfigurationError(f"{ENV_PREFIX}{name}: значення має бути >= {minimum}")
    return value


def _to_enum[E: StrEnum](name: str, raw: str | None, enum_type: type[E], default: E) -> E:
    if raw is None:
        return default
    try:
        return enum_type(raw.lower())
    except ValueError as exc:
        allowed = ", ".join(m.value for m in enum_type)
        raise ConfigurationError(f"{ENV_PREFIX}{name}: допустимі значення: {allowed}") from exc


def _to_viewport(raw: str | None, default: tuple[int, int] | None) -> tuple[int, int] | None:
    if raw is None:
        return default
    if raw.lower() == "none":
        return None
    try:
        width, _, height = raw.lower().partition("x")
        size = (int(width), int(height))
    except ValueError as exc:
        raise ConfigurationError(
            f"{ENV_PREFIX}VIEWPORT: формат ШИРИНАxВИСОТА або 'none', отримано {raw!r}"
        ) from exc
    if size[0] < 200 or size[1] < 200:
        raise ConfigurationError(f"{ENV_PREFIX}VIEWPORT: розміри надто малі")
    return size
