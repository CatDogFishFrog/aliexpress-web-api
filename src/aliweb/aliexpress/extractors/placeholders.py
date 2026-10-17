"""Заглушки екстракторів (каркас). Замінити реалізаціями після дослідження сайту.

Як реалізувати (див. memory/09-extending-guide.md):
  1. Відкрийте сторінку з ``aliweb debug open <url> --headful`` і збережіть дамп.
  2. Знайдіть, звідки брати дані (вбудований JSON у <script>, XHR-відповіді чи DOM).
  3. Реалізуйте клас тут (або в новому модулі) за протоколом з ``base.py``.
  4. Додайте тест на ЗБЕРЕЖЕНОМУ дампі сторінки (без мережі й браузера, де це можливо).
  5. Підмініть заглушку у ``bootstrap.py``.
"""

from __future__ import annotations

from patchright.async_api import Page

from aliweb.domain.catalog import Product, ProductRef, SearchPage, SearchQuery, Seller, SellerRef
from aliweb.domain.errors import ExtractorNotImplementedError


class PlaceholderSearchExtractor:
    async def extract(self, page: Page, query: SearchQuery) -> SearchPage:
        raise ExtractorNotImplementedError("Екстрактор видачі ще не реалізований")


class PlaceholderProductExtractor:
    async def extract(self, page: Page, ref: ProductRef) -> Product:
        raise ExtractorNotImplementedError("Екстрактор сторінки товару ще не реалізований")


class PlaceholderSellerExtractor:
    async def extract(self, page: Page, ref: SellerRef) -> Seller:
        raise ExtractorNotImplementedError("Екстрактор сторінки продавця ще не реалізований")
