# -*- coding: utf-8 -*-
"""Конфигурация тестов."""

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "UOGaDCkFFfSv7XMSdwQq_rqmossFFl8wSG7z69_5nO0=")
os.environ.setdefault(
    "ENCRYPTION_KEY_VERIFY",
    "gAAAAABqZwkRi9J37pVDxsdD0LHKWe_L6EkbhQVu1yKi_N43MdYL_I1IV_-5gsOOBXzCRMY9phj3dpLhDtQCsDcJPQKhEQjiRNeb6RuubyvM6vuxf6dgr30=",
)
os.environ.setdefault("TMA_SESSION_SECRET", "ci_tma_session_secret_0123456789abcdef")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "ci:test_token")
os.environ.setdefault("API_KEY", "ci_api_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "ci_anthropic_key")
os.environ.setdefault("OPENAI_API_KEY", "ci_openai_key")

import pytest
from cryptography.fernet import Fernet

from core.config import get_settings
from core.crypto import _VERIFY_PLAINTEXT

sys.dont_write_bytecode = True


# Одноразовые тестовые секреты для прогона на машине, где в окружении нет ни
# одного боевого ключа (#255). До этого ключи держал единственным местом
# env-блок verify.yml, поэтому набор был зелёным только в CI и в той оболочке
# оператора, куда секреты уже экспортированы: в свежей оболочке или в worktree
# те же восемь модулей падали EncryptionKeyMissingError и отсутствием
# TMA_SESSION_SECRET. Набор, красный «из коробки», перестают запускать локально
# — и единственная честная проверка снова приезжает уже после посадки коммита.
#
# Дефолты ставятся здесь, а не фикстурой: `get_settings` в `core/config.py` —
# ленивый синглтон, и первый же импорт прикладного модуля при сборе тестов
# материализует `Settings`. После этого переменные окружения не перечитываются.
#
# `setdefault`, а не присваивание: окружение оператора и env-блок CI перебивают
# дефолт. Ключ читаемо расшифровывается в «fb-agent-tests-only-encryption!!» —
# боевым его не спутать, и боевой в репозиторий по-прежнему не попадает.
_TEST_ENCRYPTION_KEY = "ZmItYWdlbnQtdGVzdHMtb25seS1lbmNyeXB0aW9uISE="
_TEST_TMA_SESSION_SECRET = "fb-agent-tests-only-tma-session-secret"


def _apply_test_secret_defaults() -> None:
    """Проставляет тестовые секреты, которых нет в окружении.

    Пара `ENCRYPTION_KEY` и `ENCRYPTION_KEY_VERIFY` берётся целиком либо из
    окружения, либо из дефолта. Выпустить verify-токен под чужой ключ значило бы
    своими руками починить ту самую fail-closed проверку соответствия ключа,
    которую этот токен и охраняет: подмена ключа перестала бы отличаться от
    штатного запуска.
    """
    os.environ.setdefault("TMA_SESSION_SECRET", _TEST_TMA_SESSION_SECRET)

    if os.environ.get("ENCRYPTION_KEY"):
        return

    os.environ["ENCRYPTION_KEY"] = _TEST_ENCRYPTION_KEY
    # Токен считается из ключа, а не копируется константой: закрытый набор
    # спрашивают у источника, копия расходится с ним молча.
    os.environ["ENCRYPTION_KEY_VERIFY"] = (
        Fernet(_TEST_ENCRYPTION_KEY.encode()).encrypt(_VERIFY_PLAINTEXT.encode()).decode()
    )


_apply_test_secret_defaults()


@pytest.fixture(autouse=True)
def _disable_api_key_auth(monkeypatch):
    """H-3: тесты не шлют X-API-Key — отключаем enforcement глобально.

    Прод secure-by-default (require_api_key=True). Enforcement как таковой
    проверяется отдельным unit-тестом test_api_key_auth.py со своим settings.
    """
    monkeypatch.setattr(get_settings(), "require_api_key", False, raising=False)
