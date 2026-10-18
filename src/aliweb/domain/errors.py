"""Ієрархія помилок проєкту.

Правило: усі «очікувані» збої піднімаються як нащадки :class:`AliWebError`,
щоб споживач API міг ловити одну базову помилку. Сторонні винятки (patchright,
OSError тощо) перетворюються на доменні на межі відповідного адаптера.
"""

from __future__ import annotations


class AliWebError(Exception):
    """Базова помилка проєкту.

    ``diagnostics_dir`` заповнюється, коли для помилки вдалося зберегти дамп сторінки.
    """

    diagnostics_dir: str | None = None


# ── Конфігурація ───────────────────────────────────────────────────────────
class ConfigurationError(AliWebError):
    """Некоректна або неповна конфігурація."""


class InvalidLoginModeError(ConfigurationError):
    """Недопустима комбінація режиму/браузера для входу."""


class SecretMissingError(ConfigurationError):
    """Не задано (або надто короткий) ключ шифрування ALIWEB_SESSION_SECRET."""


# ── Криптографія та сховище ────────────────────────────────────────────────
class CryptoError(AliWebError):
    """Базова помилка шифрування."""


class DecryptionError(CryptoError):
    """Не вдалося розшифрувати: хибний ключ, пошкоджений або чужий файл."""


class StorageError(AliWebError):
    """Помилка читання/запису сховища акаунтів."""


# ── Акаунти ────────────────────────────────────────────────────────────────
class AccountError(AliWebError):
    """Базова помилка керування акаунтами."""


class InvalidAccountIdError(AccountError):
    """Ідентифікатор акаунта не відповідає формату."""


class AccountNotFoundError(AccountError):
    """Акаунт не знайдено."""


class AccountAlreadyExistsError(AccountError):
    """Акаунт із таким ідентифікатором уже існує."""


class NoActiveAccountError(AccountError):
    """Активний акаунт не обрано."""


class MissingCredentialsError(AccountError):
    """Для дії потрібні логін/пароль, але вони не збережені в акаунті."""


class EmptyBundleError(AccountError):
    """Імпортований файл не містить ні сесії, ні облікових даних."""


# ── Автентифікація ─────────────────────────────────────────────────────────
class AuthenticationError(AliWebError):
    """Базова помилка входу."""


class LoginFailedError(AuthenticationError):
    """Вхід не вдався (немає сильних сигналів автентифікації)."""


class CaptchaChallengeError(LoginFailedError):
    """Антибот-виклик (слайдер тощо) не подолано."""


class RiskControlError(CaptchaChallengeError):
    """Відхилено системою ризик-контролю AliExpress (RGV587/FAIL_SYS_*).

    ``cooldown_hours`` — рекомендована пауза перед наступною спробою.
    """

    def __init__(self, message: str, *, cooldown_hours: int = 48) -> None:
        super().__init__(message)
        self.cooldown_hours = cooldown_hours


class TwoFactorRequiredError(AuthenticationError):
    """Потрібен код 2FA, але інтерактивне введення недоступне."""


class SessionExpiredError(AuthenticationError):
    """Збережена сесія вже недійсна."""


class ManualLoginTimeoutError(AuthenticationError):
    """Ручний вхід не завершено за відведений час."""


# ── Скрапінг ───────────────────────────────────────────────────────────────
class ScrapingError(AliWebError):
    """Базова помилка скрапінгу."""


class BrowserClosedError(ScrapingError):
    """Вікно/сторінку браузера закрито до завершення операції (напр. користувачем)."""


class NavigationError(ScrapingError):
    """Не вдалося відкрити сторінку."""


class PageStructureError(ScrapingError):
    """Структура сторінки не збіглася з очікуваною (змінилась верстка?)."""


class ExtractorNotImplementedError(ScrapingError):
    """Екстрактор ще не реалізований (каркас)."""
