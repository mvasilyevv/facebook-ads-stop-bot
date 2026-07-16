"""Планирование и draft-first API быстрого дублирования adset-структур."""

from core.adset_duplicates.service import (
    PREVIEW_TTL_SECONDS,
    AccountMetadata,
    AdsetDuplicateError,
    DuplicateSource,
    StoredDuplicatePreview,
    build_duplicate_preview,
    create_duplicate_draft,
    get_duplicate_task,
    load_duplicate_source,
    load_stored_preview,
    mark_preview_consumed,
    render_draft_notification,
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
    "create_duplicate_draft",
    "get_duplicate_task",
    "load_duplicate_source",
    "load_stored_preview",
    "mark_preview_consumed",
    "resolve_duplicate_source_hierarchy",
    "render_draft_notification",
    "save_stored_preview",
    "serialize_duplicate_task",
]
