# -*- coding: utf-8 -*-
"""Секретные токены не должны попадать в access-log."""
from app.log_redaction import redact_path


def test_tracking_tokens_are_redacted():
    assert redact_path("/t/secret-token") == "/t/***"
    assert redact_path("/track?number=ДГУ-1&token=secret") == "/track?***"
    assert redact_path("/success?number=ДГУ-1&token=secret") == "/success?***"
    assert redact_path("/track") == "/track"
    assert redact_path("/success") == "/success"


def test_normal_paths_are_not_changed():
    assert redact_path("/staff") == "/staff"
    assert redact_path("/staff/ticket/1") == "/staff/ticket/1"
    assert redact_path("/?qr=room214") == "/?qr=room214"
