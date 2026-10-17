"""Моделі каталогу: товари, продавці, пошук.

ПОЛІТИКА ГНУЧКОСТІ (важливо для майбутніх змін, див. memory/08-api-and-models.md):
  * Ідентифікатор сутності — її канонічне посилання (:class:`ProductRef`, :class:`SellerRef`).
  * Обов'язкові лише ``ref`` і назва. Усе інше — ``None``/порожнє за замовчуванням: сторінка
    може не віддавати дане поле, і це не помилка.
  * Нові поля додаються ЛИШЕ як необов'язкові (з default) — зворотна сумісність.
  * Неперевірені/нестабільні дані кладемо в ``extra``; коли поле стабілізується —
    «підвищуємо» його до типізованого поля.
  * «Простий» варіант (``Simple*``) — те, що видно у видачі/списку без відкриття сторінки;
    «повний» містить посилання на простий (композиція), а не дублює поля.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aliweb.domain.urls import canonicalize_url, extract_item_id


# ── Базові значення ────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str  # ISO-4217, напр. "USD"

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


@dataclass(frozen=True, slots=True)
class Rating:
    """Оцінка (зазвичай 0..5) і кількість відгуків/оцінок, якщо відомі."""

    value: Decimal | None = None
    count: int | None = None


# ── Посилання-ідентифікатори ───────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ProductRef:
    """Ідентифікатор товару = канонічне посилання (див. :func:`canonicalize_url`).

    Відкрите питання: чи є варіант (SKU) частиною ідентичності — наразі ні, варіанти
    описані у :class:`Product.variants`.
    """

    url: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", canonicalize_url(self.url))

    @property
    def item_id(self) -> str | None:
        """Допоміжний числовий id з посилання (може бути ``None``). Не ідентифікатор."""
        return extract_item_id(self.url)

    def __str__(self) -> str:
        return self.url


@dataclass(frozen=True, slots=True)
class SellerRef:
    """Ідентифікатор продавця = канонічне посилання на його магазин/сторінку."""

    url: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", canonicalize_url(self.url))

    def __str__(self) -> str:
        return self.url


# ── Продавці ───────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class SimpleSeller:
    """Те, що про продавця відомо без відкриття його сторінки (напр. з картки товару)."""

    ref: SellerRef
    name: str
    rating: Rating | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Seller:
    """Повна інформація зі сторінки продавця."""

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


# ── Товари ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class SimpleProduct:
    """«Спрощений товар» — лише дані, доступні зі сторінки пошуку/списку."""

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
    """Варіант товару (SKU): колір, розмір тощо. Структура навмисно вільна."""

    name: str
    options: Mapping[str, str] = field(default_factory=dict)
    price: Money | None = None
    in_stock: bool | None = None
    image_url: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Product:
    """Повна інформація зі сторінки товару."""

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


# ── Пошук ──────────────────────────────────────────────────────────────────
class SortOrder(StrEnum):
    """Порядок сортування видачі. Набір може розширюватись після дослідження сайту."""

    RELEVANCE = "relevance"
    ORDERS = "orders"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    NEWEST = "newest"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Запит пошуку. ``filters`` — вільні фільтри, які інтерпретує адаптер."""

    text: str
    page: int = 1
    sort: SortOrder | None = None
    filters: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Текст пошукового запиту порожній")
        if self.page < 1:
            raise ValueError("Номер сторінки має бути >= 1")

    def with_page(self, page: int) -> SearchQuery:
        return replace(self, page=page)


@dataclass(frozen=True, slots=True)
class SearchPage:
    """Одна сторінка видачі."""

    query: SearchQuery
    products: tuple[SimpleProduct, ...]
    total_pages: int | None = None
    has_next: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)

    def next_query(self) -> SearchQuery | None:
        """Запит на наступну сторінку або ``None``, якщо її немає."""
        return self.query.with_page(self.query.page + 1) if self.has_next else None
