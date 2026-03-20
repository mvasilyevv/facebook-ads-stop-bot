from __future__ import annotations

from apps.api.schemas.decisions import DecisionItem
from apps.api.services.state import ApiStore


class DecisionsService:
    def __init__(self, store: ApiStore) -> None:
        self._store = store

    def list_decisions(self) -> list[DecisionItem]:
        return list(self._store.decisions)
