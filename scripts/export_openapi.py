#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Экспортирует OpenAPI-схему FastAPI-приложения в frontend/openapi.json.

Использование:
    .venv/bin/python scripts/export_openapi.py

Схема генерируется из Pydantic response_model'ей роутеров — только у тех
endpoints, которые явно объявляют response_model. Endpoints без response_model
возвращают JSONResponse/dict и в схеме не будут иметь типизированных ответов.

Список таких endpoints выводится в stdout при запуске.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Корень проекта — родитель scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "frontend" / "openapi.json"

# Добавляем корень в sys.path чтобы работал import apps.api.main
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    from apps.api.main import create_app  # noqa: PLC0415

    app = create_app()

    # Собираем схему (FastAPI кэширует её после первого вызова)
    schema = app.openapi()

    # Ищем success-endpoints без типизированного тела. 202 с response_model и
    # намеренные 204 No Content являются полными OpenAPI-контрактами.
    paths = schema.get("paths", {})
    no_response_model: list[str] = []
    for path, path_item in sorted(paths.items()):
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            responses = operation.get("responses", {})
            successes = {
                str(code): response
                for code, response in responses.items()
                if str(code).isdigit() and 200 <= int(code) < 300
            }
            if not successes:
                no_response_model.append(f"{method.upper()} {path}")
                continue
            has_schema = any(
                "schema" in media
                for response in successes.values()
                for media in response.get("content", {}).values()
            )
            only_no_content = set(successes) == {"204"}
            if not has_schema and not only_no_content:
                no_response_model.append(f"{method.upper()} {path}")

    # Сохраняем
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"OpenAPI-схема сохранена: {OUTPUT_PATH}")
    print(
        f"Endpoints в схеме: {len(paths)} paths, {sum(len(v) for v in paths.values())} operations"
    )

    if no_response_model:
        print(f"\nEndpoints без response_model ({len(no_response_model)}) — типы не генерируются:")
        for item in no_response_model:
            print(f"  • {item}")
    else:
        print("\nВсе success-контракты имеют типизированное тело или явный 204 No Content.")


if __name__ == "__main__":
    main()
