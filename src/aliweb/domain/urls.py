"""Канонізація посилань на сутності AliExpress.

Посилання — це ідентифікатор товару/продавця (рішення проєкту). Щоб одне й те саме
посилання не мало кількох «облич», видаляємо трекінгові параметри, фрагмент, нормалізуємо
схему/хост і порядок параметрів.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aliweb.domain.errors import AliWebError

_TRACKING_PARAMS = frozenset(
    {
        "spm", "scm", "scm_id", "scm-url", "pvid", "gps-id", "gatewayadapt", "btsid",
        "ws_ab_test", "search_p4p_id", "aem_p4p_detail", "utparam", "utparam-url",
        "curpageloguid", "_evo_buckets", "sourcetype", "algo_pvid", "algo_exp_id",
    }
)  # fmt: skip
_TRACKING_PREFIXES = ("utm_", "algo_", "pdp_", "aff_")
_HOST_RE = re.compile(r"(^|\.)aliexpress\.(?:com|[a-z]{2})$")
_ITEM_ID_RE = re.compile(r"/item/(\d+)\.html")


class InvalidEntityUrlError(AliWebError, ValueError):
    """Посилання не є коректним посиланням AliExpress."""


def canonicalize_url(raw: str) -> str:
    """Повертає канонічний вигляд посилання AliExpress.

    Raises:
        InvalidEntityUrlError: якщо це не http(s)-посилання на домен aliexpress.*.
    """
    parts = urlsplit(raw.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in {"http", "https"} or not _HOST_RE.search(host):
        raise InvalidEntityUrlError(f"Не посилання AliExpress: {raw!r}")

    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PREFIXES)
    )
    return urlunsplit(("https", host, parts.path or "/", urlencode(query), ""))


def extract_item_id(url: str) -> str | None:
    """Допоміжно: числовий id з ``/item/<id>.html``. НЕ є ідентифікатором сутності."""
    match = _ITEM_ID_RE.search(urlsplit(url).path)
    return match.group(1) if match else None
