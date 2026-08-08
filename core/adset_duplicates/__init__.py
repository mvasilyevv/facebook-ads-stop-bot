"""Планирование и защищённый запуск быстрого дублирования adset-структур."""

from core.adset_duplicates.service import (
    PREVIEW_TTL_SECONDS,
    AccountMetadata,
    AdsetDuplicateError,
    DuplicateSource,
    StoredDuplicatePreview,
    build_duplicate_preview,
    create_duplicate_task,
    get_duplicate_task,
    load_duplicate_source,
    load_stored_preview,
    resolve_duplicate_source_hierarchy,
    save_stored_preview,
    serialize_duplicate_task,
)

__all__ = [
    "PREVIEW_TTL_SECONDS",
    "AccountMetadata",
    "AdsetDuplicateError",
    "DuplicateSource",
    "StoredDuplicatePreview",
    "build_duplicate_preview",
    "create_duplicate_task",
    "get_duplicate_task",
    "load_duplicate_source",
    "load_stored_preview",
    "resolve_duplicate_source_hierarchy",
    "save_stored_preview",
    "serialize_duplicate_task",
]
