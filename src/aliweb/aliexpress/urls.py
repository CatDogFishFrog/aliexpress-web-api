"""Побудова URL AliExpress (пошук тощо). Чисті функції без браузера."""

from __future__ import annotations

from urllib.parse import urlencode

from aliweb.domain.catalog import SearchQuery, SortOrder

# Значення параметра SortType. НЕ ПЕРЕВІРЕНО на живому сайті (див. memory/11-open-questions.md).
_SORT_PARAM: dict[SortOrder, str] = {
    SortOrder.ORDERS: "total_tranpro_desc",
    SortOrder.PRICE_ASC: "price_asc",
    SortOrder.PRICE_DESC: "price_desc",
    SortOrder.NEWEST: "create_desc",
}


class AliExpressUrls:
    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    def search(self, query: SearchQuery) -> str:
        params: dict[str, str] = {"SearchText": query.text.strip(), "page": str(query.page)}
        if query.sort is not None and query.sort in _SORT_PARAM:
            params["SortType"] = _SORT_PARAM[query.sort]
        params.update(query.filters)  # вільні фільтри адаптера (напр. minPrice/maxPrice)
        return f"{self._base}/wholesale?{urlencode(params)}"
