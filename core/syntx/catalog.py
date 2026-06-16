# -*- coding: utf-8 -*-
"""Каталог моделей syntx — парсинг GET /ai + GET /ai/models и удобные выборки.

Зачем: не хардкодить model_type в коде и валидировать лимиты файлов/типы медиа
ДО отправки (иначе ловим 400/422 от API). `from_api` склеивает два ответа:
- /ai          → ai_name → scope (image/video/audio/text/tools-*).
- /ai/models   → строки моделей (model_type, default, settings.*).

См. core/syntx/client.py::list_models — там это всё собирается живьём.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.syntx.models import (
    SCOPE_IMAGE,
    SCOPE_VIDEO,
    ModelInfo,
)


@dataclass(slots=True, frozen=True)
class ModelCatalog:
    """Снимок каталога моделей syntx (на момент запроса)."""

    models: tuple[ModelInfo, ...]
    # ai_name -> scope (из /ai). Полезно знать scope даже для ai без моделей.
    ai_scopes: dict[str, str]

    @classmethod
    def from_api(
        cls,
        ai_payload: list[dict[str, Any]] | None,
        models_payload: list[dict[str, Any]] | None,
    ) -> ModelCatalog:
        """Собрать каталог из ответов /ai и /ai/models."""
        ai_scopes: dict[str, str] = {}
        for a in ai_payload or []:
            if isinstance(a, dict) and a.get("value"):
                ai_scopes[str(a["value"])] = str(a.get("scope") or "")
        models: list[ModelInfo] = []
        for row in models_payload or []:
            if not isinstance(row, dict):
                continue
            info = ModelInfo.from_models_row(row)
            if not info.ai_name or not info.model_type:
                continue
            scope = ai_scopes.get(info.ai_name)
            if scope:
                # ModelInfo frozen → пересобираем со scope.
                info = ModelInfo(
                    ai_name=info.ai_name,
                    model_type=info.model_type,
                    label=info.label,
                    default=info.default,
                    scope=scope,
                    allowed_media_types=info.allowed_media_types,
                    accepted_file_types=info.accepted_file_types,
                    file_count_limit=info.file_count_limit,
                    get_cost_params=info.get_cost_params,
                )
            models.append(info)
        return cls(models=tuple(models), ai_scopes=dict(ai_scopes))

    # ====================== выборки ======================

    def for_scope(self, scope: str) -> tuple[ModelInfo, ...]:
        return tuple(m for m in self.models if m.scope == scope)

    def image_models(self) -> tuple[ModelInfo, ...]:
        return self.for_scope(SCOPE_IMAGE)

    def video_models(self) -> tuple[ModelInfo, ...]:
        return self.for_scope(SCOPE_VIDEO)

    def models_for_ai(self, ai_name: str) -> tuple[ModelInfo, ...]:
        return tuple(m for m in self.models if m.ai_name == ai_name)

    def find(self, ai_name: str, model_type: str) -> ModelInfo | None:
        for m in self.models:
            if m.ai_name == ai_name and m.model_type == model_type:
                return m
        return None

    def default_for(self, ai_name: str) -> ModelInfo | None:
        """Дефолтная модель внутри ai_name (флаг default=True), иначе первая."""
        rows = self.models_for_ai(ai_name)
        for m in rows:
            if m.default:
                return m
        return rows[0] if rows else None

    def ai_names_for_scope(self, scope: str) -> tuple[str, ...]:
        return tuple(name for name, sc in self.ai_scopes.items() if sc == scope)
