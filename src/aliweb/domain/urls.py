"""Canonicalization of AliExpress entity URLs.

URLs identify products and sellers. To give equivalent URLs one canonical form, remove tracking
parameters and fragments, normalize the scheme and host, and sort query parameters.
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
    """The URL is not a valid AliExpress URL."""


def canonicalize_url(raw: str) -> str:
    """Return the canonical form of an AliExpress URL.

    Raises:
        InvalidEntityUrlError: if this is not an HTTP(S) URL on an aliexpress.* domain.
    """
    parts = urlsplit(raw.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in {"http", "https"} or not _HOST_RE.search(host):
        raise InvalidEntityUrlError(f"Not an AliExpress URL: {raw!r}")

    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PREFIXES)
    )
    return urlunsplit(("https", host, parts.path or "/", urlencode(query), ""))


def extract_item_id(url: str) -> str | None:
    """Return the numeric ID from ``/item/<id>.html``; this is not the entity identifier."""
    match = _ITEM_ID_RE.search(urlsplit(url).path)
    return match.group(1) if match else None
