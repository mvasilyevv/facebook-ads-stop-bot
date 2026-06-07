# -*- coding: utf-8 -*-
"""Интеграция с NotebookLM (база знаний отдела) через CLI notebooklm-py."""

from core.notebooklm.client import (
    Notebook,
    NotebookLMClient,
    NotebookLMError,
    Source,
    resolve_notebooklm,
)
from core.notebooklm.kb_manifest import KbManifest, KbManifestEntry, sha256_of_file

__all__ = [
    "KbManifest",
    "KbManifestEntry",
    "Notebook",
    "NotebookLMClient",
    "NotebookLMError",
    "Source",
    "resolve_notebooklm",
    "sha256_of_file",
]
