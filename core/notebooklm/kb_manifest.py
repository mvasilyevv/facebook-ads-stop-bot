# -*- coding: utf-8 -*-
"""Манифест синхронизации docs → NotebookLM: идемпотентность по sha256.

Заливаем источник в ноутбук только если его содержимое изменилось (sha256) или он
ещё не залит. Ключ дедупа — пара (notebook_id, relative_path): один файл может жить
в разных ноутбуках. Запись атомарна (temp+rename), как в core/creatives.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

_REQUIRED_KEYS = {"path", "sha256", "notebook_id"}


class KbManifestError(RuntimeError):
    """Ошибка чтения/записи манифеста KB."""


@dataclass(frozen=True)
class KbManifestEntry:
    """Одна синхронизированная пара файл↔ноутбук."""

    path: str
    sha256: str
    notebook_id: str
    title: str = ""
    synced_at: str = ""


def sha256_of_file(path: Path) -> str:
    """Потоковый sha256 содержимого файла (как _md5_of_file в video_uniquifier)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class KbManifest:
    """Состояние синхронизации: (notebook_id, path) → запись."""

    def __init__(
        self,
        entries: list[KbManifestEntry] | None = None,
        *,
        manifest_path: Path | None = None,
    ) -> None:
        self._by_key: dict[tuple[str, str], KbManifestEntry] = {}
        for entry in entries or []:
            self._by_key[(entry.notebook_id, entry.path)] = entry
        self._manifest_path = manifest_path

    @classmethod
    def load(cls, manifest_path: Path) -> KbManifest:
        """Читает манифест; отсутствующий файл → пустой манифест."""
        if not manifest_path.exists():
            return cls(manifest_path=manifest_path)
        try:
            data = json.loads(manifest_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise KbManifestError(f"битый манифест {manifest_path}: {exc}") from exc

        raw_entries = data.get("entries", []) if isinstance(data, dict) else []
        entries: list[KbManifestEntry] = []
        for item in raw_entries:
            if isinstance(item, dict) and _REQUIRED_KEYS.issubset(item):
                entries.append(
                    KbManifestEntry(
                        path=str(item["path"]),
                        sha256=str(item["sha256"]),
                        notebook_id=str(item["notebook_id"]),
                        title=str(item.get("title") or ""),
                        synced_at=str(item.get("synced_at") or ""),
                    )
                )
        return cls(entries, manifest_path=manifest_path)

    def is_synced(self, notebook_id: str, path: str, sha256: str) -> bool:
        """True, если этот файл с этим sha256 уже залит в этот ноутбук."""
        entry = self._by_key.get((notebook_id, str(path)))
        return entry is not None and entry.sha256 == sha256

    def mark(
        self,
        *,
        notebook_id: str,
        path: str,
        sha256: str,
        title: str = "",
        synced_at: str = "",
    ) -> None:
        """Фиксирует факт заливки (перезаписывает прежнюю запись пары)."""
        key = (notebook_id, str(path))
        self._by_key[key] = KbManifestEntry(
            path=str(path),
            sha256=sha256,
            notebook_id=notebook_id,
            title=title,
            synced_at=synced_at,
        )

    def entries(self) -> list[KbManifestEntry]:
        """Все записи, стабильно отсортированные."""
        return sorted(self._by_key.values(), key=lambda e: (e.notebook_id, e.path))

    def to_dict(self) -> dict:
        """Сериализуемое представление."""
        return {"version": 1, "entries": [asdict(e) for e in self.entries()]}

    def save(self, manifest_path: Path | None = None) -> Path:
        """Атомарно пишет манифест (temp+rename). Возвращает путь."""
        target = manifest_path or self._manifest_path
        if target is None:
            raise KbManifestError("не задан путь манифеста для записи")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".tmp_{target.name}_{uuid4().hex}"
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(target)
        return target
