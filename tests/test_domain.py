from __future__ import annotations

from decimal import Decimal

import pytest

from aliweb.domain.accounts import AccountId, Credentials, mask_login
from aliweb.domain.browser import BrowserChoice, BrowserKind, LoginMode
from aliweb.domain.catalog import (
    Product,
    ProductRef,
    SearchPage,
    SearchQuery,
    SimpleProduct,
    SortOrder,
)
from aliweb.domain.errors import InvalidAccountIdError, InvalidLoginModeError
from aliweb.domain.secret import Secret
from aliweb.domain.urls import InvalidEntityUrlError, canonicalize_url, extract_item_id


class TestAccountId:
    def test_parse_normalizes(self) -> None:
        assert AccountId.parse("  Main-1 ").value == "main-1"

    @pytest.mark.parametrize("bad", ["", "-x", "a b", "../etc", "a/b", "x" * 33, "Ünï"])
    def test_rejects_unsafe(self, bad: str) -> None:
        with pytest.raises(InvalidAccountIdError):
            AccountId(bad)


class TestSecret:
    def test_repr_and_str_are_masked(self) -> None:
        secret = Secret("hunter2hunter2")
        assert "hunter2" not in repr(secret) and "hunter2" not in str(secret)
        assert "hunter2" not in repr(Credentials("a@b.c", secret))

    def test_equality_and_no_hash(self) -> None:
        assert Secret("abcd") == Secret("abcd")
        assert Secret("abcd") != Secret("abce")
        with pytest.raises(TypeError):
            hash(Secret("abcd"))

    def test_not_picklable(self) -> None:
        import pickle

        with pytest.raises(TypeError):
            pickle.dumps(Secret("abcd"))


@pytest.mark.parametrize(
    ("login", "expected"),
    [("user@mail.com", "u***@mail.com"), ("+380501234567", "***67"), ("x", "***")],
)
def test_mask_login(login: str, expected: str) -> None:
    assert mask_login(login) == expected


class TestUrls:
    def test_strips_tracking_fragment_and_sorts(self) -> None:
        url = "HTTP://WWW.AliExpress.com/item/1005001.html?spm=a.b&sku_id=9&utm_x=1&algo_pvid=z&b=2#frag"
        assert canonicalize_url(url) == "https://www.aliexpress.com/item/1005001.html?b=2&sku_id=9"

    def test_equivalent_links_collapse(self) -> None:
        a = ProductRef("https://aliexpress.com/item/1.html?spm=x")
        b = ProductRef("https://aliexpress.com/item/1.html")
        assert a == b and a.item_id == "1"

    @pytest.mark.parametrize(
        "bad",
        ["ftp://aliexpress.com/x", "https://evil.com/item/1.html", "https://aliexpress.com.evil.io/", "nope"],
    )
    def test_rejects_foreign_hosts(self, bad: str) -> None:
        with pytest.raises(InvalidEntityUrlError):
            canonicalize_url(bad)

    def test_store_subdomain_and_regional_tld_allowed(self) -> None:
        assert canonicalize_url("https://store.aliexpress.com/x") == "https://store.aliexpress.com/x"
        assert canonicalize_url("https://aliexpress.us/item/2.html").startswith("https://aliexpress.us")

    def test_extract_item_id_none(self) -> None:
        assert extract_item_id("https://aliexpress.com/store/123") is None


class TestCatalogModels:
    def test_search_query_validation(self) -> None:
        with pytest.raises(ValueError, match="порожній"):
            SearchQuery("   ")
        with pytest.raises(ValueError, match=">= 1"):
            SearchQuery("x", page=0)

    def test_next_query(self) -> None:
        query = SearchQuery("cable", sort=SortOrder.ORDERS)
        page = SearchPage(query=query, products=(), has_next=True)
        nxt = page.next_query()
        assert nxt is not None and nxt.page == 2 and nxt.sort is SortOrder.ORDERS
        assert SearchPage(query=query, products=()).next_query() is None

    def test_product_composes_summary(self) -> None:
        simple = SimpleProduct(ref=ProductRef("https://aliexpress.com/item/5.html"), title="T")
        assert Product(summary=simple).ref == simple.ref
        assert simple.price is None and dict(simple.extra) == {}
        _ = Decimal  # моделі не вимагають заповнення необов'язкових полів


class TestLoginMode:
    def test_default_is_headless_bundled_chromium(self) -> None:
        mode = LoginMode()
        assert mode.headless and mode.browser.kind is BrowserKind.CHROMIUM and mode.browser.is_bundled

    @pytest.mark.parametrize("kind", [BrowserKind.CHROME, BrowserKind.MSEDGE])
    def test_headful_real_browsers_allowed(self, kind: BrowserKind) -> None:
        assert LoginMode(headless=False, browser=BrowserChoice(kind)).browser.kind is kind

    def test_headful_patchright_and_custom_allowed(self) -> None:
        LoginMode(headless=False)
        LoginMode(headless=False, browser=BrowserChoice.custom("/x/brave"))

    @pytest.mark.parametrize(
        "browser",
        [BrowserChoice(BrowserKind.CHROME), BrowserChoice(BrowserKind.MSEDGE), BrowserChoice.custom("/x")],
    )
    def test_headless_only_on_patchright_browser(self, browser: BrowserChoice) -> None:
        with pytest.raises(InvalidLoginModeError, match="--headful"):
            LoginMode(headless=True, browser=browser)

    def test_browser_choice_path_rules(self) -> None:
        with pytest.raises(InvalidLoginModeError):
            BrowserChoice(BrowserKind.CUSTOM)  # шлях обов'язковий
        with pytest.raises(InvalidLoginModeError):
            BrowserChoice(BrowserKind.CHROME, "/x")  # шлях лише для CUSTOM
        assert str(BrowserChoice.custom("/x")) == "/x" and str(BrowserChoice()) == "chromium"
