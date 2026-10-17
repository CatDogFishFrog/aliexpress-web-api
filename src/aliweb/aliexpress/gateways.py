"""Реалізації портів ``CatalogGateway``/``SellerGateway`` через скрапінг.

Тут лише оркестрація: взяти автентифіковану сторінку → відкрити URL → віддати екстрактору.
Уся специфіка сайту — в екстракторах, URL-білдері та селекторах.
"""

from __future__ import annotations

from aliweb.aliexpress.extractors.base import ProductExtractor, SearchExtractor, SellerExtractor
from aliweb.aliexpress.navigation import open_checked
from aliweb.aliexpress.urls import AliExpressUrls
from aliweb.domain.catalog import Product, ProductRef, SearchPage, SearchQuery, Seller, SellerRef
from aliweb.infrastructure.browser.page_provider import AuthenticatedPageProvider


class ScrapingCatalogGateway:
    def __init__(
        self,
        provider: AuthenticatedPageProvider,
        urls: AliExpressUrls,
        search_extractor: SearchExtractor,
        product_extractor: ProductExtractor,
    ) -> None:
        self._provider = provider
        self._urls = urls
        self._search = search_extractor
        self._product = product_extractor

    async def search(self, query: SearchQuery) -> SearchPage:
        async with self._provider.acquire() as session:
            await open_checked(session, self._urls.search(query))
            return await self._search.extract(session.page, query)

    async def get_product(self, ref: ProductRef) -> Product:
        async with self._provider.acquire() as session:
            await open_checked(session, ref.url)
            return await self._product.extract(session.page, ref)


class ScrapingSellerGateway:
    def __init__(self, provider: AuthenticatedPageProvider, extractor: SellerExtractor) -> None:
        self._provider = provider
        self._extractor = extractor

    async def get_seller(self, ref: SellerRef) -> Seller:
        async with self._provider.acquire() as session:
            await open_checked(session, ref.url)
            return await self._extractor.extract(session.page, ref)
