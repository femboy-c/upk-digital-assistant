# -*- coding: utf-8 -*-
"""Общие настройки тестов: временная база, временные вложения, отключение CSRF."""
import os
import tempfile

os.environ.setdefault("UPK_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="upk-tests-"), "test.db"))
os.environ.setdefault("UPK_UPLOAD_DIR", tempfile.mkdtemp(prefix="upk-uploads-"))
os.environ.setdefault("UPK_DISABLE_CSRF", "1")
os.environ.setdefault("UPK_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("UPK_DISABLE_CONSENT", "1")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://upk.test")
# Тестовый клиент ходит по HTTP, поэтому Secure-cookie из .env отключаем.
os.environ.setdefault("SESSION_HTTPS_ONLY", "0")
