from __future__ import annotations

import pytest

from aliweb.application.accounts import AccountService
from aliweb.application.auth import AuthService
from aliweb.application.catalog import CatalogService
from aliweb.application.transfer import SessionTransferService
from aliweb.domain.accounts import AccountId, Credentials
from aliweb.domain.browser import BrowserChoice, BrowserKind, LoginMode
from aliweb.domain.catalog import (
    Product,
    ProductRef,
    SearchPage,
    SearchQuery,
    SimpleProduct,
)
from aliweb.domain.errors import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    DecryptionError,
    EmptyBundleError,
    MissingCredentialsError,
    NoActiveAccountError,
    RiskControlError,
)
from aliweb.domain.secret import Secret
from aliweb.infrastructure.crypto.scrypt_aesgcm import ScryptAesGcmCipher, ScryptParams
from tests.conftest import (
    FakeAuthenticator,
    FakeProfileGenerator,
    MemoryActiveStore,
    MemoryRepository,
    fast_cipher,
    make_account,
    make_session,
)

A, B = AccountId("a"), AccountId("b")
CREDS = Credentials("u@mail.com", Secret("password-1"))


@pytest.fixture
def accounts(
    repo: MemoryRepository, active: MemoryActiveStore, profiles: FakeProfileGenerator
) -> AccountService:
    return AccountService(repo, active, profiles)


class TestAccountService:
    async def test_first_added_becomes_active_second_does_not(self, accounts: AccountService) -> None:
        await accounts.add(A, CREDS)
        await accounts.add(B, CREDS)
        assert await accounts.get_active_id() == A

    async def test_duplicate_rejected(self, accounts: AccountService) -> None:
        await accounts.add(A)
        with pytest.raises(AccountAlreadyExistsError):
            await accounts.add(A)

    async def test_set_active_requires_existing(self, accounts: AccountService) -> None:
        with pytest.raises(AccountNotFoundError):
            await accounts.set_active(A)
        await accounts.add(A)
        await accounts.add(B)
        await accounts.set_active(B)
        assert await accounts.require_active_id() == B

    async def test_remove_clears_active(self, accounts: AccountService) -> None:
        await accounts.add(A)
        await accounts.remove(A)
        assert await accounts.get_active_id() is None
        with pytest.raises(NoActiveAccountError):
            await accounts.require_active_id()
        with pytest.raises(AccountNotFoundError):
            await accounts.remove(A)

    async def test_rename_keeps_data_and_moves_active(
        self, accounts: AccountService, repo: MemoryRepository
    ) -> None:
        await accounts.add(A, CREDS)
        repo.items[A] = repo.items[A].with_session(make_session(), now=repo.items[A].updated_at)
        await accounts.rename(A, B)
        assert A not in repo.items and repo.items[B].session is not None
        assert await accounts.get_active_id() == B

    async def test_rename_to_existing_rejected(self, accounts: AccountService) -> None:
        await accounts.add(A)
        await accounts.add(B)
        with pytest.raises(AccountAlreadyExistsError):
            await accounts.rename(A, B)

    async def test_stale_active_pointer_is_healed(
        self, accounts: AccountService, repo: MemoryRepository
    ) -> None:
        await accounts.add(A)
        repo.items.clear()  # файл видалили ззовні
        assert await accounts.get_active_id() is None

    async def test_summaries_hide_secrets(self, accounts: AccountService) -> None:
        await accounts.add(A, CREDS)
        (summary,) = await accounts.list_summaries()
        assert summary.masked_login == "u***@mail.com" and summary.is_active and not summary.has_session


class TestAuthService:
    @pytest.fixture
    def fake(self) -> FakeAuthenticator:
        return FakeAuthenticator()

    def make(
        self,
        accounts: AccountService,
        repo: MemoryRepository,
        fake: FakeAuthenticator,
        clock: list[float],
    ) -> AuthService:
        return AuthService(accounts, repo, fake, verify_ttl_s=100, monotonic=lambda: clock[0])

    async def test_login_requires_credentials(
        self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator
    ) -> None:
        await accounts.add(A)
        with pytest.raises(MissingCredentialsError):
            await self.make(accounts, repo, fake, [0]).login(A)

    async def test_login_saves_session_and_passes_mode(
        self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator
    ) -> None:
        await accounts.add(A, CREDS)
        mode = LoginMode(headless=False, browser=BrowserChoice(BrowserKind.MSEDGE))
        await self.make(accounts, repo, fake, [0]).login(A, mode=mode)
        assert repo.items[A].session is not None
        assert fake.login_calls[0].mode == mode and fake.login_calls[0].storage_state is None

    async def test_relogin_reuses_session_unless_fresh(
        self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator
    ) -> None:
        await accounts.add(A, CREDS)
        auth = self.make(accounts, repo, fake, [0])
        await auth.login(A)
        await auth.login(A)
        await auth.login(A, fresh=True)
        assert [c.storage_state is not None for c in fake.login_calls] == [False, True, False]

    async def test_ensure_session_ttl_and_relogin(
        self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator
    ) -> None:
        await accounts.add(A, CREDS)
        clock = [0.0]
        auth = self.make(accounts, repo, fake, clock)
        await auth.ensure_session(A)  # немає сесії → вхід
        assert len(fake.login_calls) == 1 and fake.validity_checks == 0
        await auth.ensure_session(A)  # у межах TTL → без перевірки
        assert fake.validity_checks == 0
        clock[0] = 500
        await auth.ensure_session(A)  # TTL минув → перевірка, сесія дійсна
        assert fake.validity_checks == 1 and len(fake.login_calls) == 1
        clock[0] = 1000
        fake.valid = False
        await auth.ensure_session(A)  # недійсна → повторний вхід
        assert len(fake.login_calls) == 2

    async def test_default_login_mode_is_none_scraper_mode(
        self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator
    ) -> None:
        await accounts.add(A, CREDS)
        await self.make(accounts, repo, fake, [0]).login(A)
        assert fake.login_calls[0].mode is None

    async def test_auto_relogin_uses_scraper_browser_never_a_real_browser(
        self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator
    ) -> None:
        """Автоматичний повторний вхід під час скрапінгу — лише браузер patchright."""
        await accounts.add(A, CREDS)
        await self.make(accounts, repo, fake, [0]).ensure_session(A, headless=False)
        mode = fake.login_calls[0].mode
        assert mode == LoginMode(headless=False) and mode.browser.is_bundled

    async def test_manual_login_does_not_need_credentials(
        self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator
    ) -> None:
        await accounts.add(A)  # акаунт без логіну/пароля
        mode = LoginMode(headless=False, manual=True)
        result = await self.make(accounts, repo, fake, [0]).login(A, mode=mode)
        assert result.session is not None and repo.items[A].session is not None
        assert fake.login_calls[0].credentials is None and fake.login_calls[0].mode == mode

    async def test_automatic_login_without_credentials_suggests_manual(
        self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator
    ) -> None:
        await accounts.add(A)
        with pytest.raises(MissingCredentialsError, match="--manual"):
            await self.make(accounts, repo, fake, [0]).login(A)

    async def test_already_authenticated_is_reported(
        self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator
    ) -> None:
        await accounts.add(A, CREDS)
        fake.already_authenticated = True
        result = await self.make(accounts, repo, fake, [0]).login(A)
        assert result.already_authenticated and repo.items[A].session is not None

    async def test_logout_clears_session_even_if_remote_fails(
        self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator
    ) -> None:
        await accounts.add(A, CREDS)
        auth = self.make(accounts, repo, fake, [0])
        await auth.login(A)
        fake.logout_error = RiskControlError("boom")
        await auth.logout(A)
        assert repo.items[A].session is None and repo.items[A].credentials is not None

    async def test_check_without_session_is_false(
        self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator
    ) -> None:
        await accounts.add(A, CREDS)
        assert await self.make(accounts, repo, fake, [0]).check(A) is False


class TestTransfer:
    @pytest.fixture
    def service(self, repo: MemoryRepository, profiles: FakeProfileGenerator) -> SessionTransferService:
        return SessionTransferService(
            repo,
            fast_cipher(),
            lambda s: ScryptAesGcmCipher(s, ScryptParams(12, 8, 1)),
            profiles,
        )

    async def test_export_excludes_password_by_default(
        self, service: SessionTransferService, repo: MemoryRepository
    ) -> None:
        repo.items[A] = make_account("a", session=make_session())
        blob = await service.export_bytes(A)
        repo.items.clear()
        account = await service.import_bytes(blob)
        assert account.credentials is None and account.session == make_session()

    async def test_include_credentials_roundtrip(
        self, service: SessionTransferService, repo: MemoryRepository
    ) -> None:
        repo.items[A] = make_account("a", session=make_session())
        blob = await service.export_bytes(A, include_credentials=True)
        account = await service.import_bytes(blob, as_id=B)
        assert account.id == B and account.credentials is not None

    async def test_overwrite_policy_and_password_preserved(
        self, service: SessionTransferService, repo: MemoryRepository
    ) -> None:
        repo.items[A] = make_account("a", session=make_session("old"))
        blob = await service.export_bytes(A)  # без пароля
        repo.items[A] = make_account("a", session=make_session("old"))
        with pytest.raises(AccountAlreadyExistsError):
            await service.import_bytes(blob)
        merged = await service.import_bytes(blob, overwrite=True)
        assert merged.credentials is not None  # пароль існуючого акаунта не стертий

    async def test_passphrase_protects_bundle(
        self, service: SessionTransferService, repo: MemoryRepository
    ) -> None:
        repo.items[A] = make_account("a", session=make_session())
        blob = await service.export_bytes(A, passphrase=Secret("transfer-passphrase-1"))
        with pytest.raises(DecryptionError):
            await service.import_bytes(blob)  # ключ із .env не підходить
        repo.items.clear()
        assert (await service.import_bytes(blob, passphrase=Secret("transfer-passphrase-1"))).id == A

    async def test_vault_blob_is_not_importable_as_bundle(self, service: SessionTransferService) -> None:
        """Різний AAD: файл сховища не можна підсунути як експортний пакет."""
        from aliweb.application.codec import encode_account
        from aliweb.infrastructure.storage.file_repository import VAULT_AAD

        blob = fast_cipher().encrypt(encode_account(make_account("a")), aad=VAULT_AAD)
        with pytest.raises(DecryptionError):
            await service.import_bytes(blob)

    async def test_empty_bundle_rejected(
        self, service: SessionTransferService, repo: MemoryRepository
    ) -> None:
        from dataclasses import replace

        repo.items[A] = replace(make_account("a"), credentials=None, session=None)
        blob = await service.export_bytes(A)
        with pytest.raises(EmptyBundleError):
            await service.import_bytes(blob, as_id=B)


class FakeGateway:
    def __init__(self, pages: int) -> None:
        self.pages = pages
        self.requested: list[int] = []

    async def search(self, query: SearchQuery) -> SearchPage:
        self.requested.append(query.page)
        item = SimpleProduct(ref=ProductRef(f"https://aliexpress.com/item/{query.page}.html"), title="t")
        return SearchPage(query=query, products=(item,), has_next=query.page < self.pages)

    async def get_product(self, ref: ProductRef) -> Product:
        return Product(summary=SimpleProduct(ref=ref, title="t"))


class TestCatalogService:
    async def test_iter_pages_follows_navigation(self) -> None:
        gateway = FakeGateway(pages=3)
        pages = [p async for p in CatalogService(gateway).iter_pages(SearchQuery("x"))]
        assert [p.query.page for p in pages] == [1, 2, 3] and gateway.requested == [1, 2, 3]

    async def test_iter_pages_respects_max_pages(self) -> None:
        gateway = FakeGateway(pages=10)
        pages = [p async for p in CatalogService(gateway).iter_pages(SearchQuery("x"), max_pages=2)]
        assert len(pages) == 2 and gateway.requested == [1, 2]

    async def test_max_pages_validated(self) -> None:
        with pytest.raises(ValueError, match="max_pages"):
            _ = [p async for p in CatalogService(FakeGateway(1)).iter_pages(SearchQuery("x"), max_pages=0)]
