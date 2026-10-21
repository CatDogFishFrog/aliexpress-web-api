"""Catalog models: products, sellers, and search.

Flexibility policy (see memory/08-api-and-models.md):
    * An entity is identified by its canonical URL (:class:`ProductRef`, :class:`SellerRef`).
    * Only ``ref`` and the name are required. Other fields default to ``None`` or empty values because
        a page may omit them.
    * New fields must be optional (with defaults) to preserve compatibility.
    * Unverified or unstable data belongs in ``extra``; promote it to a typed field once stable.
    * A ``Simple*`` model contains data visible in search results without opening a page; a full model
        references the simple model instead of duplicating its fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aliweb.domain.urls import canonicalize_url, extract_item_id


# ── Shared values ──────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str  # ISO-4217, e.g. "USD"

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


@dataclass(frozen=True, slots=True)
class Rating:
    """Rating (usually 0..5) and review count, when available."""

    value: Decimal | None = None
    count: int | None = None


# ── URL identifiers ───────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ProductRef:
    """Product identifier represented by its canonical URL (see :func:`canonicalize_url`).

    Open question: whether a variant (SKU) belongs to the identity. Currently it does not; variants
    are described by :class:`Product.variants`.
    """

    url: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", canonicalize_url(self.url))

    @property
    def item_id(self) -> str | None:
        """Optional numeric ID parsed from the URL; it is not the identifier."""
        return extract_item_id(self.url)

    def __str__(self) -> str:
        return self.url


@dataclass(frozen=True, slots=True)
class SellerRef:
    """Seller identifier represented by the canonical URL of its store or page."""

    url: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", canonicalize_url(self.url))

    def __str__(self) -> str:
        return self.url


# ── Sellers ────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class SimpleSeller:
    """Seller information available without opening the seller page, such as from a product card."""

    ref: SellerRef
    name: str
    rating: Rating | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Seller:
    """Full information from a seller page."""

    summary: SimpleSeller
    description: str | None = None
    followers: int | None = None
    positive_feedback_rate: Decimal | None = None
    open_since: str | None = None
    products: tuple[SimpleProduct, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> SellerRef:
        return self.summary.ref


# ── Products ───────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class SimpleProduct:
    """A simplified product containing only data available in search results or lists."""

    ref: ProductRef
    title: str
    price: Money | None = None
    original_price: Money | None = None
    image_url: str | None = None
    rating: Rating | None = None
    sold_count: int | None = None
    shipping_info: str | None = None
    seller: SimpleSeller | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProductVariant:
    """Product variant (SKU), such as a color or size. The structure is intentionally flexible."""

    name: str
    options: Mapping[str, str] = field(default_factory=dict)
    price: Money | None = None
    in_stock: bool | None = None
    image_url: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Product:
    """Full information from a product page."""

    summary: SimpleProduct
    description: str | None = None
    images: tuple[str, ...] = ()
    variants: tuple[ProductVariant, ...] = ()
    specifications: Mapping[str, str] = field(default_factory=dict)
    seller: Seller | SimpleSeller | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> ProductRef:
        return self.summary.ref


# ── Search ─────────────────────────────────────────────────────────────────
class SortOrder(StrEnum):
    """Search result sort order; options may expand as the site is studied."""

    RELEVANCE = "relevance"
    ORDERS = "orders"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    NEWEST = "newest"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Search query. ``filters`` contains arbitrary filters interpreted by the adapter."""

    text: str
    page: int = 1
    sort: SortOrder | None = None
    filters: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Search query cannot be empty")
        if self.page < 1:
            raise ValueError("Page number must be >= 1")

    def with_page(self, page: int) -> SearchQuery:
        return replace(self, page=page)


@dataclass(frozen=True, slots=True)
class SearchPage:
    """A single search results page."""

    query: SearchQuery
    products: tuple[SimpleProduct, ...]
    total_pages: int | None = None
    has_next: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)

    def next_query(self) -> SearchQuery | None:
        """Query for the next page, or ``None`` if there is no next page."""
        return self.query.with_page(self.query.page + 1) if self.has_next else None
