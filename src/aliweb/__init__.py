"""aliweb is an unofficial AliExpress Web API built with patchright.

The public entry point is :class:`aliweb.api.AliExpressWebApi`.
Imports are lazy so ``import aliweb`` does not load browser dependencies.
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
