# -*- coding: utf-8 -*-
"""Публичный API помощника, работающего по правилам и базе знаний."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..assistant import analyze
from ..config import ASSISTANT_RATE_LIMIT, ASSISTANT_RATE_WINDOW_SECONDS
from ..database import get_db
from ..models import Category
from ..ratelimit import client_ip, is_limited, register_event

router = APIRouter()


class AnalyzeRequest(BaseModel):
    text: str = Field(default="", max_length=5000)


@router.post("/assistant/analyze")
def analyze_text(payload: AnalyzeRequest, request: Request, db: Session = Depends(get_db)):
    """Классифицирует текст до отправки заявки. Ничего не сохраняет и не меняет."""
    rate_key = f"assistant:{client_ip(request)}"
    if is_limited(rate_key, ASSISTANT_RATE_LIMIT, ASSISTANT_RATE_WINDOW_SECONDS):
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов к помощнику. Подождите минуту и попробуйте снова.",
        )
    register_event(rate_key, ASSISTANT_RATE_WINDOW_SECONDS)

    result = analyze(db, payload.text)

    category_name = result.get("suggested_category")
    category = (
        db.scalar(select(Category).where(Category.name == category_name))
        if category_name
        else None
    )
    result["suggested_category_id"] = category.id if category else None
    return result
