# -*- coding: utf-8 -*-
"""Безопасный разбор значений из форм.

Пользователь может прислать в фильтр или скрытое поле любую строку,
поэтому int() нельзя вызывать напрямую: ValueError превратился бы в 500.
"""


def as_int(value: str | None) -> int | None:
    """Превращает строку в число или возвращает None, не выбрасывая исключение."""
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
