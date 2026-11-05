# -*- coding: utf-8 -*-
"""Поиск похожих заявок для объединения дублей.

Сравнение простое и предсказуемое: одинаковое помещение, открытая заявка
и похожий текст. Никаких внешних сервисов и моделей.
"""
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Ticket

FINAL_STATUSES = {"Решена", "Отклонена"}
SIMILARITY_THRESHOLD = 0.4
LOOKBACK_DAYS = 90
MAX_CANDIDATES = 200

_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)

# Слова, которые не помогают отличить одну проблему от другой.
_STOPWORDS = {
    "это", "как", "что", "где", "когда", "почему", "зачем", "куда", "кто",
    "мне", "нам", "вам", "его", "ее", "её", "их", "для", "или", "если",
    "так", "там", "тут", "вот", "уже", "еще", "ещё", "был", "была", "было",
    "быть", "есть", "можно", "нужно", "надо", "очень", "просто", "пожалуйста",
    "делать", "сделать", "подскажите", "скажите", "помогите", "вопрос",
    "кабинет", "кабинете", "коридор", "коридоре", "этаж", "этаже",
}


def _normalized(text: str) -> str:
    """Нижний регистр, без ё, служебных слов и коротких обрывков."""
    text = (text or "").lower().replace("ё", "е")
    words = [
        word
        for word in _WORD_RE.findall(text)
        if len(word) >= 3 and word not in _STOPWORDS
    ]
    return " ".join(words)


def text_similarity(first: str, second: str) -> float:
    """Оценка похожести двух описаний от 0 до 1 (коэффициент Дайса)."""
    left = _normalized(first)
    right = _normalized(second)
    if not left or not right:
        return 0.0

    left_words = set(left.split())
    right_words = set(right.split())
    total = len(left_words) + len(right_words)
    if not total:
        return 0.0

    common = len(left_words & right_words)
    return round(2 * common / total, 3)


def find_similar_tickets(
    db: Session,
    description: str,
    location_id: int | None,
    *,
    exclude_id: int | None = None,
    limit: int = 3,
) -> list[tuple[Ticket, float]]:
    """Возвращает открытые заявки в том же помещении с похожим текстом."""
    if not location_id or not (description or "").strip():
        return []

    since = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    query = select(Ticket).where(
        Ticket.location_id == location_id,
        Ticket.status.notin_(FINAL_STATUSES),
        Ticket.merged_into_id.is_(None),
        Ticket.created_at >= since,
    )
    if exclude_id is not None:
        query = query.where(Ticket.id != exclude_id)

    candidates = db.scalars(
        query.order_by(Ticket.created_at.desc()).limit(MAX_CANDIDATES)
    ).all()

    scored: list[tuple[Ticket, float]] = []
    for ticket in candidates:
        score = text_similarity(description, ticket.description)
        if score >= SIMILARITY_THRESHOLD:
            scored.append((ticket, score))

    scored.sort(key=lambda item: (-item[1], item[0].created_at))
    return scored[:limit]
