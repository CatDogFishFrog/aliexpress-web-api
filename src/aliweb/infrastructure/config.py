"""Configuration from environment variables and ``.env``.

This is the only module that reads ``os.environ`` (enforced by an architecture test). Other code
receives typed, immutable settings through constructor parameters (see ``bootstrap``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from dotenv import dotenv_values

from aliweb.domain.errors import ConfigurationError, SecretMissingError
from aliweb.domain.profile import ClaimedOs
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
    """Scraper browser settings; the scraper always uses patchright.

    See LoginMode for login browser settings.
    """

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
class HumanSettings:
    """Human-like page interaction (typing, mouse, scrolling). See ``browser/human.py``."""

    enabled: bool = True
    speed: float = 1.0  # Speed multiplier: >1 is faster, <1 is slower.


@dataclass(frozen=True, slots=True)
class FingerprintSettings:
    """Settings used to create an account profile; existing profiles are not changed."""

    claimed_os: ClaimedOs | None = None  # None means the host OS (see memory/05).


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
    session_probe_url: str | None = None  # None uses the login page, which redirects authenticated users.
    logout_url: str | None = None
    session_verify_ttl_s: float = 600.0


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path = Path("./data")
    session_secret: Secret | None = None
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    human: HumanSettings = field(default_factory=HumanSettings)
    fingerprint: FingerprintSettings = field(default_factory=FingerprintSettings)
    diagnostics: DiagnosticsSettings = field(default_factory=DiagnosticsSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    aliexpress: AliExpressSettings = field(default_factory=AliExpressSettings)

    def require_secret(self) -> Secret:
        """Return the encryption key or raise :class:`SecretMissingError` with guidance."""
        secret = self.session_secret
        if secret is None or len(secret) < MIN_SECRET_LENGTH:
            raise SecretMissingError(
                f"ALIWEB_SESSION_SECRET is missing or shorter than {MIN_SECRET_LENGTH} characters. "
                "Generate a key with `aliweb secret generate` and add it to .env."
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
        """Read settings. Precedence: ``environ`` (defaults to os.environ) > ``.env``.

        Values from ``.env`` are not added to ``os.environ`` and cannot leak into child processes,
        including the browser.
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
                timezone_id=_to_timezone(get("TIMEZONE")),
                viewport=_to_viewport(get("VIEWPORT"), defaults.browser.viewport),
                nav_timeout_ms=_to_int("NAV_TIMEOUT_MS", get("NAV_TIMEOUT_MS"), 45_000, minimum=1000),
                action_timeout_ms=_to_int("ACTION_TIMEOUT_MS", get("ACTION_TIMEOUT_MS"), 15_000, minimum=500),
                slow_mo_ms=_to_int("SLOW_MO_MS", get("SLOW_MO_MS"), 0, minimum=0),
                proxy=proxy,
            ),
            human=HumanSettings(
                enabled=_to_bool("HUMAN_INPUT", get("HUMAN_INPUT"), True),
                speed=_to_float("HUMAN_SPEED", get("HUMAN_SPEED"), 1.0, minimum=0.3, maximum=3.0),
            ),
            fingerprint=FingerprintSettings(claimed_os=_to_claimed_os(get("FINGERPRINT_OS"))),
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
                session_probe_url=get("SESSION_PROBE_URL"),
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
    raise ConfigurationError(f"{ENV_PREFIX}{name}: expected true/false, got {raw!r}")


def _to_int(name: str, raw: str | None, default: int, *, minimum: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{ENV_PREFIX}{name}: expected an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigurationError(f"{ENV_PREFIX}{name}: value must be >= {minimum}")
    return value


_TIMEZONE_RE = re.compile(r"^[A-Za-z]+(?:[_+-]?[A-Za-z0-9]+)*(?:/[A-Za-z0-9_+-]+)*$")


def _to_timezone(raw: str | None) -> str | None:
    if raw is not None and not _TIMEZONE_RE.fullmatch(raw):
        raise ConfigurationError(f"{ENV_PREFIX}TIMEZONE: expected an IANA identifier, e.g. Europe/Kyiv")
    return raw


def _to_claimed_os(raw: str | None) -> ClaimedOs | None:
    if raw is None or raw.lower() == "auto":
        return None
    try:
        return ClaimedOs(raw.lower())
    except ValueError as exc:
        allowed = ", ".join(["auto", *(m.value for m in ClaimedOs)])
        raise ConfigurationError(f"{ENV_PREFIX}FINGERPRINT_OS: allowed values: {allowed}") from exc


def _to_float(name: str, raw: str | None, default: float, *, minimum: float, maximum: float) -> float:
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{ENV_PREFIX}{name}: expected a number, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{ENV_PREFIX}{name}: value must be between {minimum} and {maximum}")
    return value


def _to_enum[E: StrEnum](name: str, raw: str | None, enum_type: type[E], default: E) -> E:
    if raw is None:
        return default
    try:
        return enum_type(raw.lower())
    except ValueError as exc:
        allowed = ", ".join(m.value for m in enum_type)
        raise ConfigurationError(f"{ENV_PREFIX}{name}: allowed values: {allowed}") from exc


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
            f"{ENV_PREFIX}VIEWPORT: expected WIDTHxHEIGHT or 'none', got {raw!r}"
        ) from exc
    if size[0] < 200 or size[1] < 200:
        raise ConfigurationError(f"{ENV_PREFIX}VIEWPORT: dimensions are too small")
    return size
