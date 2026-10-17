from __future__ import annotations

import pytest

from aliweb.aliexpress.navigation import CHALLENGE_URL_RE
from aliweb.aliexpress.session_probe import looks_like_login_url
from aliweb.aliexpress.signals import (
    LOGIN_RESPONSE_URL_RE,
    classify_login_ret,
    decide_login_success,
    extract_ret,
    read_cookie,
)
from aliweb.aliexpress.slider import ease_in_out
from aliweb.aliexpress.urls import AliExpressUrls
from aliweb.domain.catalog import SearchQuery, SortOrder


class TestSignals:
    def test_success_and_risk(self) -> None:
        assert classify_login_ret(["SUCCESS::ok"]).success
        assert classify_login_ret("success::ok").success
        verdict = classify_login_ret(["FAIL_SYS_USER_VALIDATE::x", "SUCCESS::y"])
        assert verdict.risk and not verdict.success

    @pytest.mark.parametrize("ret", [None, [], "FAIL::nope"])
    def test_no_signal(self, ret: object) -> None:
        verdict = classify_login_ret(ret)
        assert not verdict.risk and not verdict.success

    def test_extract_ret_locations(self) -> None:
        assert extract_ret({"ret": ["A"]}) == ["A"]
        assert extract_ret({"data": {"ret": "B"}}) == "B"
        assert extract_ret({"x": 1}) is None

    def test_decide(self) -> None:
        assert decide_login_success(has_ticket=True)
        assert decide_login_success(xman_rotated=True)
        assert decide_login_success(response_success=True)
        assert not decide_login_success()
        assert not decide_login_success(has_ticket=True, response_risk=True)  # ризик перекриває все

    def test_read_cookie(self) -> None:
        cookies = [{"name": "a", "value": ""}, {"name": "a", "value": "1"}, {"name": "b", "value": "2"}]
        assert read_cookie(cookies, "a") == "1" and read_cookie(cookies, "zzz") == ""

    def test_login_url_regex(self) -> None:
        assert LOGIN_RESPONSE_URL_RE.search("https://x/user/buyer/login/loginByPwd?a=1")
        assert not LOGIN_RESPONSE_URL_RE.search("https://x/user/buyer/login/loginByPwdReport")


def test_url_heuristics() -> None:
    assert looks_like_login_url("https://login.aliexpress.com/?x=1")
    assert looks_like_login_url("https://passport.aliexpress.com/")
    assert not looks_like_login_url("https://www.aliexpress.com/item/1.html")
    assert CHALLENGE_URL_RE.search("https://x/punish?x5secdata=1")


def test_slider_easing_is_monotonic_and_bounded() -> None:
    values = [ease_in_out(i / 25) for i in range(26)]
    assert values[0] == 0 and values[-1] == pytest.approx(1)
    assert values == sorted(values)


class TestSearchUrls:
    urls = AliExpressUrls("https://www.aliexpress.com/")

    def test_basic(self) -> None:
        url = self.urls.search(SearchQuery("usb c cable", page=2))
        assert url == "https://www.aliexpress.com/wholesale?SearchText=usb+c+cable&page=2"

    def test_sort_and_filters(self) -> None:
        url = self.urls.search(SearchQuery("x", sort=SortOrder.PRICE_ASC, filters={"minPrice": "5"}))
        assert "SortType=price_asc" in url and "minPrice=5" in url

    def test_relevance_adds_no_sort_param(self) -> None:
        assert "SortType" not in self.urls.search(SearchQuery("x", sort=SortOrder.RELEVANCE))
