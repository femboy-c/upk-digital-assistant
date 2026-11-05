# -*- coding: utf-8 -*-
"""Необязательная проверка вложений через ClamAV.

Если CLAMAV_HOST не задан, проверка пропускается. Если задан, но сканер
недоступен или вернул ошибку, загрузка блокируется: лучше отказать в файле,
чем принять потенциально опасный.
"""
import io
import logging

from .config import CLAMAV_HOST, CLAMAV_PORT

logger = logging.getLogger(__name__)


class AntivirusError(Exception):
    """Сканер недоступен или вернул непонятный ответ."""


class VirusFoundError(Exception):
    """ClamAV нашёл сигнатуру вредоносного файла."""


def scan_bytes(data: bytes) -> None:
    """Проверяет байты файла. Без настроенного ClamAV ничего не делает."""
    if not CLAMAV_HOST:
        return

    try:
        import clamd

        client = clamd.ClamdNetworkSocket(
            host=CLAMAV_HOST,
            port=CLAMAV_PORT,
            timeout=10,
        )
        result = client.instream(io.BytesIO(data))
        status, signature = result.get("stream", ("ERROR", "no response"))
        if status == "FOUND":
            raise VirusFoundError(signature or "unknown")
        if status != "OK":
            raise AntivirusError(f"clamd returned {status}")
    except VirusFoundError:
        raise
    except Exception as error:
        # Не логируем содержимое файла и не раскрываем его пользователю.
        logger.warning("ClamAV scan failed: %s", type(error).__name__)
        raise AntivirusError("antivirus unavailable") from error
