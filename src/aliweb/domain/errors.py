"""Project error hierarchy.

All expected failures derive from :class:`AliWebError`, allowing API consumers to catch one base
exception. External exceptions (such as patchright and ``OSError``) are translated into domain
errors at the relevant adapter boundary.
"""

from __future__ import annotations


class AliWebError(Exception):
    """Base project error.

    ``diagnostics_dir`` is set when a diagnostic page dump was saved for the error.
    """

    diagnostics_dir: str | None = None


# ── Configuration ─────────────────────────────────────────────────────────
class ConfigurationError(AliWebError):
    """Invalid or incomplete configuration."""


class InvalidLoginModeError(ConfigurationError):
    """Unsupported login mode and browser combination."""


class SecretMissingError(ConfigurationError):
    """ALIWEB_SESSION_SECRET is missing or too short."""


# ── Cryptography and storage ───────────────────────────────────────────────
class CryptoError(AliWebError):
    """Base encryption error."""


class DecryptionError(CryptoError):
    """Decryption failed: incorrect key, corrupted data, or an unrelated file."""


class StorageError(AliWebError):
    """Account storage read or write error."""


# ── Accounts ──────────────────────────────────────────────────────────────
class AccountError(AliWebError):
    """Base account management error."""


class InvalidAccountIdError(AccountError):
    """Account identifier does not match the required format."""


class AccountNotFoundError(AccountError):
    """Account was not found."""


class AccountAlreadyExistsError(AccountError):
    """An account with this identifier already exists."""


class NoActiveAccountError(AccountError):
    """No active account is selected."""


class MissingCredentialsError(AccountError):
    """This action requires login credentials, but none are saved for the account."""


class EmptyBundleError(AccountError):
    """Imported file contains neither a session nor credentials."""


# ── Authentication ────────────────────────────────────────────────────────
class AuthenticationError(AliWebError):
    """Base authentication error."""


class LoginFailedError(AuthenticationError):
    """Login failed because there were no strong authentication signals."""


class CaptchaChallengeError(LoginFailedError):
    """An anti-bot challenge (such as a slider) was not solved."""


class RiskControlError(CaptchaChallengeError):
    """Rejected by AliExpress risk control (RGV587/FAIL_SYS_*).

    ``cooldown_hours`` is the recommended wait before another attempt.
    """

    def __init__(self, message: str, *, cooldown_hours: int = 48) -> None:
        super().__init__(message)
        self.cooldown_hours = cooldown_hours


class TwoFactorRequiredError(AuthenticationError):
    """A 2FA code is required, but interactive input is unavailable."""


class SessionExpiredError(AuthenticationError):
    """Saved session is no longer valid."""


class ManualLoginTimeoutError(AuthenticationError):
    """Manual login did not complete before the timeout."""


# ── Scraping ──────────────────────────────────────────────────────────────
class ScrapingError(AliWebError):
    """Base scraping error."""


class BrowserClosedError(ScrapingError):
    """Browser window or page closed before the operation completed, possibly by the user."""


class NavigationError(ScrapingError):
    """Failed to open the page."""


class PageStructureError(ScrapingError):
    """Page structure did not match expectations; the site layout may have changed."""


class ProfileGenerationError(AliWebError):
    """Failed to create a realistic browser profile."""


class ExtractorNotImplementedError(ScrapingError):
    """Extractor is not implemented yet."""
