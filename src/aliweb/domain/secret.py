"""Secret string wrapper that prevents values from leaking into logs or reprs."""

from __future__ import annotations

import hmac


class Secret:
    """Hold a sensitive value. ``repr`` and ``str`` always mask it.

    Access the value only through explicit :meth:`reveal` calls, making each use visible in code review.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    def __hash__(self) -> int:
        raise TypeError("Secret values are not hashable")

    def __reduce__(self) -> str:
        raise TypeError("Secret values cannot be pickled")
