from __future__ import annotations

from typing import Protocol

from patchright.async_api import Page

from aliweb.domain.catalog import Product, ProductRef, SearchPage, SearchQuery, Seller, SellerRef


class SearchExtractor(Protocol):
    async def extract(self, page: Page, query: SearchQuery) -> SearchPage:
        """Parse an opened search results page into a ``SearchPage`` with simplified products."""
        ...


class ProductExtractor(Protocol):
    async def extract(self, page: Page, ref: ProductRef) -> Product:
        """Parse an opened product page into a full ``Product``."""
        ...


class SellerExtractor(Protocol):
    async def extract(self, page: Page, ref: SellerRef) -> Seller:
        """Parse an opened seller page into a full ``Seller``."""
        ...
