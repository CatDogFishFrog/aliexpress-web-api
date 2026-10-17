from __future__ import annotations

from typing import Protocol

from patchright.async_api import Page

from aliweb.domain.catalog import Product, ProductRef, SearchPage, SearchQuery, Seller, SellerRef


class SearchExtractor(Protocol):
    async def extract(self, page: Page, query: SearchQuery) -> SearchPage:
        """Сторінка видачі вже відкрита → ``SearchPage`` зі «спрощеними товарами»."""
        ...


class ProductExtractor(Protocol):
    async def extract(self, page: Page, ref: ProductRef) -> Product:
        """Сторінка товару вже відкрита → повний ``Product``."""
        ...


class SellerExtractor(Protocol):
    async def extract(self, page: Page, ref: SellerRef) -> Seller:
        """Сторінка продавця вже відкрита → повний ``Seller``."""
        ...
