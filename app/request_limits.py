# -*- coding: utf-8 -*-
"""Ограничение размера HTTP-запроса.

Защищает от загрузки огромных файлов и «медленных» тел запроса, которые
могли бы исчерпать память или диск. Проверяется и Content-Length, и
фактически прочитанные байты (на случай chunked-запросов).
"""
from fastapi import HTTPException
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared:
            try:
                if int(declared) > self.max_bytes:
                    response = PlainTextResponse(
                        "Запрос слишком большой. Уменьшите размер файла.",
                        status_code=413,
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                pass

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # HTTPException внутри приложения отрисуется штатной
                    # HTML-страницей ошибки, а не как 500.
                    raise HTTPException(
                        status_code=413,
                        detail="Запрос слишком большой. Уменьшите размер файла.",
                    )
            return message

        await self.app(scope, limited_receive, send)
