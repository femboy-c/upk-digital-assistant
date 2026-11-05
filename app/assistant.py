# -*- coding: utf-8 -*-
"""
Помощник колледжа, работающий по правилам.

Это НЕ внешняя языковая модель и не обученный ИИ: ответы формируются
по понятным правилам и проверенной базе знаний из таблицы knowledge_base.
Поэтому результат воспроизводим, не требует API-ключей и не отправляет
текст пользователя на сторонние сервисы.
"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import KnowledgeBase

DISCLAIMER = (
    "Ответы формируются по правилам и проверенной базе знаний колледжа, без "
    "внешней языковой модели. Помощник не заменяет заявку: он не меняет её статус "
    "и не закрывает автоматически."
)

KIND_LABELS = {
    "question": "Типовой вопрос",
    "malfunction": "Неисправность",
    "insufficient": "Недостаточно данных",
    "danger": "Опасная ситуация",
    "unknown": "Нет ответа в базе знаний",
}

# Слова, которые не помогают понять смысл запроса.
STOPWORDS = {
    "это", "как", "что", "где", "когда", "почему", "зачем", "куда", "кто",
    "мне", "нам", "вам", "его", "её", "ее", "их", "для", "или", "если",
    "так", "там", "тут", "вот", "уже", "ещё", "еще", "был", "была", "было",
    "быть", "есть", "можно", "нужно", "надо", "очень", "просто", "пожалуйста",
    "делать", "сделать", "подскажите", "скажите", "помогите", "вопрос",
}

# Признаки реальной опасности: проверяются раньше всех остальных правил.
DANGER_PATTERNS = [
    "искр", "запах гар", "запах горел", "горит провод", "горит розет",
    "пожар", "возгоран", "задым", "дым", "оголен", "удар током",
    "бьет током", "бьёт током", "короткое замыкан", "плавит", "оплавил",
    "затоп", "потоп", "прорыв труб", "льется вода", "льётся вода",
    "вода на полу", "пахнет горелым",
]

MALFUNCTION_PATTERNS = [
    "не работ", "сломал", "не включа", "не печата", "не подключа",
    "нет интернет", "не ловит", "течет", "течёт", "протека", "разбит",
    "треснул", "шатает", "скрип", "шум", "перегрев", "ошибк",
    "не открыва", "не закрыва", "застря", "порван", "отвалил", "мигает",
    "не светит", "перегорел", "не заряжа", "не реагир", "заклинило",
]

GENERIC_INSUFFICIENT = [
    "не работает", "не работают", "сломалось", "сломался", "помогите",
    "проблема", "что-то не работает", "непонятно", "все сломалось",
]

QUESTION_WORDS = {
    "как", "где", "куда", "почему", "зачем", "можно", "нужно", "что", "кто",
    "когда", "сколько", "какой", "какая", "какие", "есть",
}

# Порядок важен: «интернет-кабель» должен попасть в «Интернет», а не в электрику.
CATEGORY_RULES = [
    ("Интернет", ["интернет", "wifi", "wi-fi", "сеть", "сетев", "роутер", "патч"]),
    ("Электрика и освещение", ["розет", "электр", "свет", "лампа", "люстр", "провод", "выключател", "освещ"]),
    ("Мебель", ["стол", "стул", "шкаф", "двер", "окн", "парт", "полк", "замок", "ручк", "имуществ", "поврежд"]),
    ("Оборудование", ["проектор", "компьютер", "ноутбук", "принтер", "сканер", "монитор", "клавиатур", "мыш", "доск", "кондиционер", "техник"]),
    ("Уборка", ["гряз", "мусор", "пыл", "пятн", "разлит", "уборк", "убр"]),
]


def normalize(text: str) -> str:
    """Приводит текст к виду для сравнения: нижний регистр, без лишних знаков."""
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"wi[\s-]?fi", "wifi", text)
    text = re.sub(r"[^а-яa-z0-9\s-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    """Разбивает текст на значимые слова."""
    words = re.findall(r"[а-яa-z0-9]+", normalize(text))
    return [word for word in words if len(word) >= 3 and word not in STOPWORDS]


def _tokens_match(left: str, right: str) -> bool:
    """Простое сравнение с учётом окончаний: «розетка» ≈ «розетке»."""
    if left == right:
        return True
    return len(left) >= 4 and len(right) >= 4 and left[:4] == right[:4]


def _overlap(query_tokens: list[str], text: str) -> int:
    text_tokens = tokenize(text)
    score = 0
    for query_token in query_tokens:
        if any(_tokens_match(query_token, text_token) for text_token in text_tokens):
            score += 1
    return score


def find_knowledge(db: Session, text: str) -> tuple[KnowledgeBase | None, int]:
    """Ищет самый близкий проверенный ответ в базе знаний."""
    query_tokens = tokenize(text)
    if not query_tokens:
        return None, 0

    best: KnowledgeBase | None = None
    best_score = 0
    for row in db.scalars(select(KnowledgeBase)).all():
        question_score = _overlap(query_tokens, row.question)
        category_score = _overlap(query_tokens, row.category)
        score = question_score * 2 + category_score
        if score > best_score:
            best = row
            best_score = score

    # Одного случайного слова недостаточно: нужен заметный общий смысл.
    if best_score < 4:
        return None, best_score
    return best, best_score


def is_danger(text: str) -> bool:
    normalized = normalize(text)
    return any(pattern in normalized for pattern in DANGER_PATTERNS)


def is_malfunction(text: str) -> bool:
    normalized = normalize(text)
    return any(pattern in normalized for pattern in MALFUNCTION_PATTERNS)


def is_question(text: str) -> bool:
    if "?" in text:
        return True
    words = re.findall(r"[а-яa-z0-9]+", normalize(text))
    return any(word in QUESTION_WORDS for word in words)


def _has_object_hint(text: str) -> bool:
    """Есть ли в тексте конкретный объект (проектор, дверь, розетка и т.п.)."""
    normalized = normalize(text)
    return any(
        any(pattern in normalized for pattern in patterns)
        for _category, patterns in CATEGORY_RULES
    )


def is_insufficient(text: str, has_knowledge: bool) -> bool:
    normalized = normalize(text)
    words = tokenize(normalized)

    # Точные общие фразы вроде «не работает» без объекта.
    if normalized in GENERIC_INSUFFICIENT:
        return True

    # «Сломалось что-то» / «не работает вообще» — объекта нет, нужен вопрос.
    if len(words) <= 3 and not _has_object_hint(normalized):
        if any(phrase in normalized for phrase in GENERIC_INSUFFICIENT):
            return True

    # Короткая фраза без готового ответа почти всегда требует уточнения.
    return not has_knowledge and len(normalized) < 15 and len(words) <= 2


def suggest_category(text: str) -> str | None:
    normalized = normalize(text)
    for category, patterns in CATEGORY_RULES:
        if any(pattern in normalized for pattern in patterns):
            return category
    return None


def build_draft(text: str, kind: str) -> str:
    clean = text.strip().rstrip(".")
    if kind == "malfunction":
        return f"{clean}.\n\nПрошу проверить и устранить неисправность."
    if kind == "danger":
        return f"Опасная ситуация: {clean}.\n\nПрошу срочно проверить. Оборудование не трогаю."
    return ""


def _clarifying_question(category: str | None) -> str:
    if category:
        return (
            "Уточните, пожалуйста: что именно происходит с оборудованием, "
            "на каком этаже и в каком кабинете это находится и когда началось? "
            "Можно приложить фотографию."
        )
    return (
        "Уточните, пожалуйста: что именно не работает или что случилось, "
        "на каком этаже и в каком кабинете это находится? Можно приложить фотографию."
    )


def analyze(db: Session, text: str) -> dict:
    """Главная функция помощника: классифицирует текст и готовит ответ."""
    clean = (text or "").strip()
    kb, _score = find_knowledge(db, clean)
    category = suggest_category(clean)

    result = {
        "kind": "unknown",
        "kind_label": KIND_LABELS["unknown"],
        "answer": "",
        "source": "",
        "suggested_category": None,
        "draft": "",
        "clarifying_question": "",
        "can_create_ticket": True,
        "disclaimer": DISCLAIMER,
    }

    if not normalize(clean):
        result.update(
            kind="insufficient",
            kind_label=KIND_LABELS["insufficient"],
            answer="Опишите проблему хотя бы одним предложением — тогда помощник сможет подсказать.",
            clarifying_question=_clarifying_question(None),
        )
        return result

    if is_danger(clean):
        answer = (
            "Это потенциально опасно. Не трогайте оборудование, розетку или провод "
            "и не пытайтесь ремонтировать самостоятельно. Отойдите на безопасное "
            "расстояние, предупредите окружающих и немедленно обратитесь к "
            "ответственному сотруднику. При пожаре или угрозе жизни звоните в "
            "экстренную службу."
        )
        result.update(
            kind="danger",
            kind_label=KIND_LABELS["danger"],
            answer=answer,
            source=kb.source if kb else "Правила безопасности колледжа",
            suggested_category=category or "Прочее",
            draft=build_draft(clean, "danger"),
        )
        return result

    if is_insufficient(clean, kb is not None):
        result.update(
            kind="insufficient",
            kind_label=KIND_LABELS["insufficient"],
            answer="Пока недостаточно данных, чтобы дать точную инструкцию.",
            clarifying_question=_clarifying_question(category),
            suggested_category=category,
        )
        return result

    if is_malfunction(clean):
        answer = (
            "Похоже на неисправность. Проверьте только безопасное: включено ли "
            "питание, подключён ли кабель, нет ли на экране сообщения об ошибке. "
            "Не разбирайте оборудование. Если это не помогло — оформите заявку."
        )
        if kb:
            answer = f"{kb.answer}\n\n{answer}"
        result.update(
            kind="malfunction",
            kind_label=KIND_LABELS["malfunction"],
            answer=answer,
            source=kb.source if kb else "",
            suggested_category=category or "Прочее",
            draft=build_draft(clean, "malfunction"),
        )
        return result

    if kb:
        result.update(
            kind="question",
            kind_label=KIND_LABELS["question"],
            answer=kb.answer,
            source=kb.source,
            suggested_category=category,
        )
        return result

    if is_question(clean):
        result.update(
            kind="unknown",
            kind_label=KIND_LABELS["unknown"],
            answer=(
                "В проверенной базе знаний колледжа нет ответа на этот вопрос, поэтому "
                "не буду придумывать. Уточните вопрос или оформите заявку — "
                "сотрудник колледжа ответит."
            ),
            suggested_category=category,
        )
        return result

    result.update(
        kind="unknown",
        kind_label=KIND_LABELS["unknown"],
        answer=(
            "Не удалось уверенно понять запрос, и в проверенной базе знаний нет "
            "подходящего ответа. Опишите проблему подробнее или оформите заявку — "
            "сотрудник колледжа разберётся."
        ),
        suggested_category=category,
    )
    return result
