from __future__ import annotations

from datetime import UTC, datetime

from apps.api.schemas.ads import AdActionResponse, AdBlockRequest, AdDetail, AdSummary
from apps.api.schemas.common import ControlFlagTarget, DecisionKind, ScopePresence, TrackingMode
from apps.api.services.control_flags import ControlFlagsService
from apps.api.services.state import ApiStore


class AdsService:
    def __init__(self, store: ApiStore, control_flags_service: ControlFlagsService) -> None:
        self._store = store
        self._control_flags_service = control_flags_service

    def list_ads(self) -> list[AdSummary]:
        return [AdSummary.model_validate(ad) for ad in self._store.ads.values()]

    def get_ad(self, fb_ad_id: str) -> AdDetail | None:
        return self._store.ads.get(fb_ad_id)

    def block_ad(self, fb_ad_id: str, payload: AdBlockRequest) -> AdActionResponse | None:
        ad = self._store.ads.get(fb_ad_id)
        if ad is None:
            return None
        now = datetime.now(tz=UTC)
        updated_ad = ad.model_copy(
            update={
                "tracking_mode": TrackingMode.MANUAL_BLOCK,
                "last_decision": DecisionKind.SKIPPED_BY_POLICY,
                "updated_at": now,
            }
        )
        self._store.ads[fb_ad_id] = updated_ad
        self._control_flags_service.create_or_replace_entity_flag(
            entity_type=ControlFlagTarget.AD,
            entity_external_id=fb_ad_id,
            tracking_mode=TrackingMode.MANUAL_BLOCK,
            reason=payload.reason,
            created_by=payload.created_by,
        )
        return AdActionResponse(
            message="Объявление переведено в ручную блокировку",
            ad=updated_ad,
        )

    def unblock_ad(self, fb_ad_id: str) -> AdActionResponse | None:
        ad = self._store.ads.get(fb_ad_id)
        if ad is None:
            return None
        now = datetime.now(tz=UTC)
        updated_ad = ad.model_copy(
            update={
                "tracking_mode": TrackingMode.TRACKED,
                "last_decision": DecisionKind.NO_ACTION,
                "scope_presence": ScopePresence.IN_SCOPE,
                "updated_at": now,
            }
        )
        self._store.ads[fb_ad_id] = updated_ad
        self._control_flags_service.remove_entity_flag(
            entity_type=ControlFlagTarget.AD, entity_external_id=fb_ad_id
        )
        return AdActionResponse(
            message="Объявление возвращено в отслеживание",
            ad=updated_ad,
        )
