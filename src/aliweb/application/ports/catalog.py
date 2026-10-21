from __future__ import annotations

from typing import Protocol

from aliweb.domain.catalog import Product, ProductRef, SearchPage, SearchQuery, Seller, SellerRef


class CatalogGateway(Protocol):
    """Search and product access, implemented by scraping in ``aliweb.aliexpress``."""

    async def search(self, query: SearchQuery) -> SearchPage: ...

    async def get_product(self, ref: ProductRef) -> Product: ...


class SellerGateway(Protocol):
    async def get_seller(self, ref: SellerRef) -> Seller: ...
