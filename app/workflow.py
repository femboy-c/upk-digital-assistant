# -*- coding: utf-8 -*-
"""Допустимые переходы между статусами заявки.

Текущий статус всегда разрешён: сотрудник может изменить исполнителя или
добавить заметку, не меняя состояние заявки. Возврат из итоговых статусов
разрешён только администратору.
"""
from .models import STATUSES

STATUS_TRANSITIONS: dict[str, set[str]] = {
    "Новая": {"На рассмотрении", "Назначен исполнитель", "Отклонена"},
    "На рассмотрении": {
        "Назначен исполнитель",
        "Нужны уточнения",
        "Решена",
        "Отклонена",
    },
    "Назначен исполнитель": {"В работе", "Нужны уточнения", "Решена", "Отклонена"},
    "В работе": {"Решена", "Нужны уточнения", "Отклонена"},
    "Нужны уточнения": {
        "На рассмотрении",
        "Назначен исполнитель",
        "В работе",
        "Решена",
        "Отклонена",
    },
    "Решена": set(),
    "Отклонена": set(),
}

# Возврат из итоговых статусов — только администратор.
ADMIN_REOPEN: dict[str, set[str]] = {
    "Решена": {"В работе"},
    "Отклонена": {"На рассмотрении"},
}

# В эти статусы нельзя войти без назначенного исполнителя.
ASSIGNEE_REQUIRED_STATUSES = {"Назначен исполнитель", "В работе"}


def allowed_transitions(current_status: str, role: str) -> set[str]:
    """Множество статусов, в которые можно перейти из текущего."""
    targets = set(STATUS_TRANSITIONS.get(current_status, set()))
    if role == "admin":
        targets |= ADMIN_REOPEN.get(current_status, set())
    return targets


def allowed_status_options(current_status: str, role: str) -> list[str]:
    """Текущий статус плюс допустимые, в порядке общего списка STATUSES."""
    targets = allowed_transitions(current_status, role)
    return [status for status in STATUSES if status == current_status or status in targets]
