"""aliweb — неофіційний AliExpress Web API (скрапінг через patchright).

Публічна точка входу — фасад :class:`aliweb.api.AliExpressWebApi`.
Імпорти виконуються ліниво, щоб `import aliweb` не тягнув браузерні залежності.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aliweb.api import AliExpressWebApi

__all__ = ["AliExpressWebApi", "__version__"]
__version__ = "0.2.1"


def __getattr__(name: str) -> object:
    if name == "AliExpressWebApi":
        from aliweb.api import AliExpressWebApi

        return AliExpressWebApi
    raise AttributeError(name)
