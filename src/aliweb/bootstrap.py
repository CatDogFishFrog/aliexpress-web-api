"""Composition root — ЄДИНЕ місце, де конкретні класи зв'язуються з абстракціями.

Правила:
  * Нова залежність підключається тут, а не імпортується «зсередини» сервісів.
  * Жодна бізнес-логіка тут не живе — лише складання графа об'єктів.
"""

from __future__ import annotations

from aliweb.aliexpress.authenticator import AliExpressAuthenticator
from aliweb.aliexpress.extractors.placeholders import (
    PlaceholderProductExtractor,
    PlaceholderSearchExtractor,
    PlaceholderSellerExtractor,
)
from aliweb.aliexpress.gateways import ScrapingCatalogGateway, ScrapingSellerGateway
from aliweb.aliexpress.login_flow import PasswordLoginFlow
from aliweb.aliexpress.selectors import SELECTORS
from aliweb.aliexpress.session_probe import SessionProbe
from aliweb.aliexpress.slider import SliderSolver
from aliweb.aliexpress.urls import AliExpressUrls
from aliweb.api import AliExpressWebApi
from aliweb.application.accounts import AccountService
from aliweb.application.auth import AuthService
from aliweb.application.catalog import CatalogService, SellerService
from aliweb.application.ports.auth import TwoFactorPrompt
from aliweb.application.transfer import SessionTransferService
from aliweb.infrastructure.browser.debugger import ProviderPageDebugger
from aliweb.infrastructure.browser.page_provider import AuthenticatedPageProvider
from aliweb.infrastructure.browser.pool import BrowserPool
from aliweb.infrastructure.browser.session import BrowserSessionFactory
from aliweb.infrastructure.config import Settings
from aliweb.infrastructure.crypto.scrypt_aesgcm import ScryptAesGcmCipher, make_cipher
from aliweb.infrastructure.diagnostics.collector import DiagnosticsCollector
from aliweb.infrastructure.interaction.console import ConsoleTwoFactorPrompt
from aliweb.infrastructure.logging_setup import configure_logging
from aliweb.infrastructure.redaction import Redactor
from aliweb.infrastructure.storage.file_repository import (
    FileAccountRepository,
    FileActiveAccountStore,
)


def build_api(
    settings: Settings,
    *,
    two_factor: TwoFactorPrompt | None = None,
    redactor: Redactor | None = None,
) -> AliExpressWebApi:
    """Збирає повний граф. Потребує ``ALIWEB_SESSION_SECRET`` (шифрування сховища).

    Args:
        two_factor: джерело 2FA-коду (за замовчуванням — консоль).
        redactor: спільний маскувальник секретів (за замовчуванням створюється новий).
    """
    secret = settings.require_secret()
    redactor = redactor or Redactor()
    redactor.register(secret)
    configure_logging(settings.logging, logs_dir=settings.logs_dir, redactor=redactor)

    # ── сховище ──
    cipher = ScryptAesGcmCipher(secret)
    repository = FileAccountRepository(settings.accounts_dir, cipher)
    active_store = FileActiveAccountStore(settings.data_dir / "active_account")

    # ── браузер і діагностика ──
    diagnostics = DiagnosticsCollector(settings.diagnostics, settings.diagnostics_dir, redactor)
    pool = BrowserPool(settings.browser)
    sessions = BrowserSessionFactory(pool, settings.browser, diagnostics, redactor)

    # ── адаптер AliExpress ──
    flow = PasswordLoginFlow(
        SELECTORS.login,
        SliderSolver(SELECTORS.login),
        two_factor or ConsoleTwoFactorPrompt(),
        settings.aliexpress,
        redactor,
        selector_timeout_ms=settings.browser.action_timeout_ms,
    )
    authenticator = AliExpressAuthenticator(
        sessions, flow, SessionProbe(SELECTORS.login, settings.aliexpress), settings.aliexpress
    )

    # ── прикладні сервіси ──
    accounts = AccountService(repository, active_store)
    auth = AuthService(
        accounts, repository, authenticator, verify_ttl_s=settings.aliexpress.session_verify_ttl_s
    )
    transfer = SessionTransferService(repository, cipher, make_cipher)

    provider = AuthenticatedPageProvider(accounts, auth, repository, sessions)
    urls = AliExpressUrls(settings.aliexpress.base_url)
    catalog_gateway = ScrapingCatalogGateway(
        provider, urls, PlaceholderSearchExtractor(), PlaceholderProductExtractor()
    )
    seller_gateway = ScrapingSellerGateway(provider, PlaceholderSellerExtractor())

    return AliExpressWebApi(
        accounts=accounts,
        auth=auth,
        transfer=transfer,
        catalog=CatalogService(catalog_gateway),
        sellers=SellerService(seller_gateway),
        debug=ProviderPageDebugger(provider, diagnostics),
        _close=pool.aclose,
    )
