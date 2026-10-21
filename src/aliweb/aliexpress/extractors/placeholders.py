"""Placeholder extractors. Replace these with implementations after studying the site.

Implementation guide: see memory/09-extending-guide.md.
    1. Open a page with ``aliweb debug open <url> --headful`` and save the dump.
    2. Find the data source (embedded JSON in <script>, XHR responses, or the DOM).
    3. Implement the class here or in a new module using the protocol in ``base.py``.
    4. Add a test using the saved page dump, without network or browser access where possible.
    5. Replace the placeholder in ``bootstrap.py``.
"""

from __future__ import annotations

from patchright.async_api import Page

from aliweb.domain.catalog import Product, ProductRef, SearchPage, SearchQuery, Seller, SellerRef
from aliweb.domain.errors import ExtractorNotImplementedError


class PlaceholderSearchExtractor:
    async def extract(self, page: Page, query: SearchQuery) -> SearchPage:
        raise ExtractorNotImplementedError("Search results extractor is not implemented")


class PlaceholderProductExtractor:
    async def extract(self, page: Page, ref: ProductRef) -> Product:
        raise ExtractorNotImplementedError("Product page extractor is not implemented")


class PlaceholderSellerExtractor:
    async def extract(self, page: Page, ref: SellerRef) -> Seller:
        raise ExtractorNotImplementedError("Seller page extractor is not implemented")
