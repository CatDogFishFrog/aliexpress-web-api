"""Обгортка для секретних рядків, що не «протікає» в логи та repr."""

from __future__ import annotations

import hmac


class Secret:
    """Тримає чутливе значення. ``repr``/``str`` завжди маскують його.

    Доступ до значення — лише явним :meth:`reveal`, щоб кожне використання було помітним
    під час код-рев'ю.
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
        raise TypeError("Secret не хешується (щоб не потрапляти в множини/ключі словників)")

    def __reduce__(self) -> str:
        raise TypeError("Secret не можна серіалізувати через pickle")
