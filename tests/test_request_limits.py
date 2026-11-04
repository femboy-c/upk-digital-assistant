# -*- coding: utf-8 -*-
"""Ограничение размера HTTP-запроса."""
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.request_limits import RequestBodyLimitMiddleware


def build_app(limit: int = 10) -> FastAPI:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):
        body = await request.body()
        return {"size": len(body)}

    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=limit)
    return app


def test_small_request_passes():
    client = TestClient(build_app())
    response = client.post("/echo", content=b"12345")
    assert response.status_code == 200
    assert response.json() == {"size": 5}


def test_declared_content_length_is_rejected_before_body_read():
    client = TestClient(build_app())
    response = client.post("/echo", content=b"x" * 11)
    assert response.status_code == 413
    assert "слишком большой" in response.text


def test_chunked_request_is_stopped_while_streaming():
    client = TestClient(build_app())

    def chunks():
        yield b"x" * 6
        yield b"x" * 6

    response = client.post("/echo", content=chunks())
    assert response.status_code == 413
    assert "слишком большой" in response.text
