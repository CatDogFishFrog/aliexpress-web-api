"""Сценарії каталогу: пошук, навігація по видачі, товари, продавці."""

from __future__ import annotations

from collections.abc import AsyncIterator

from aliweb.application.ports.catalog import CatalogGateway, SellerGateway
from aliweb.domain.catalog import Product, ProductRef, SearchPage, SearchQuery, Seller, SellerRef


class CatalogService:
    def __init__(self, gateway: CatalogGateway) -> None:
        self._gateway = gateway

    async def search(self, query: SearchQuery) -> SearchPage:
        """Одна сторінка видачі (``query.page``)."""
        return await self._gateway.search(query)

    async def iter_pages(
        self, query: SearchQuery, *, max_pages: int | None = None
    ) -> AsyncIterator[SearchPage]:
        """Навігація по видачі: віддає сторінки по одній, поки є наступна."""
        if max_pages is not None and max_pages < 1:
            raise ValueError("max_pages має бути >= 1")
        yielded = 0
        current: SearchQuery | None = query
        while current is not None:
            page = await self._gateway.search(current)
            yield page
            yielded += 1
            if max_pages is not None and yielded >= max_pages:
                return
            current = page.next_query()

    async def get_product(self, ref: ProductRef) -> Product:
        """Повна інформація про товар зі сторінки товару."""
        return await self._gateway.get_product(ref)


class SellerService:
    def __init__(self, gateway: SellerGateway) -> None:
        self._gateway = gateway

    async def get_seller(self, ref: SellerRef) -> Seller:
        """Повна інформація зі сторінки продавця."""
        return await self._gateway.get_seller(ref)
