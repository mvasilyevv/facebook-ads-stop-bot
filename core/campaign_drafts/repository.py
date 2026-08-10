"""PostgreSQL adapter for the single owner campaign draft."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from core.campaign_drafts.contracts import CampaignDraftDocument, CampaignDraftState

MAX_CAMPAIGN_DRAFT_BYTES = 256 * 1024


class CampaignDraftConflictError(RuntimeError):
    """Optimistic-lock conflict that remains a valid mutable Python exception."""

    def __init__(self, expected_revision: int, actual_revision: int | None) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        # BaseException.args must preserve the constructor signature so the
        # exception remains serializable by workers and diagnostics tooling.
        super().__init__(expected_revision, actual_revision)

    def __str__(self) -> str:
        return (
            "campaign draft revision conflict: "
            f"expected={self.expected_revision}, actual={self.actual_revision}"
        )


class CampaignDraftTooLargeError(ValueError):
    pass


def _serialized_state(state: CampaignDraftState) -> str:
    payload = json.dumps(
        state.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(payload.encode("utf-8")) > MAX_CAMPAIGN_DRAFT_BYTES:
        raise CampaignDraftTooLargeError("campaign draft exceeds 256 KiB")
    return payload


def _document(row) -> CampaignDraftDocument:
    return CampaignDraftDocument(
        revision=int(row.revision),
        state=CampaignDraftState.model_validate(row.state),
        updated_at=row.updated_at,
    )


class CampaignDraftRepository:
    """Deep module: strict contract, CAS and singleton storage behind four methods."""

    async def load(self, connection: AsyncConnection) -> CampaignDraftDocument | None:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT revision, state, updated_at
                    FROM campaign_draft
                    WHERE singleton_key = 'owner'
                    """
                )
            )
        ).first()
        return _document(row) if row is not None else None

    async def save(
        self,
        connection: AsyncConnection,
        *,
        expected_revision: int,
        state: CampaignDraftState,
    ) -> CampaignDraftDocument:
        current = (
            await connection.execute(
                text(
                    """
                    SELECT revision
                    FROM campaign_draft
                    WHERE singleton_key = 'owner'
                    FOR UPDATE
                    """
                )
            )
        ).first()
        actual_revision = int(current.revision) if current is not None else None
        if actual_revision != (expected_revision or None):
            raise CampaignDraftConflictError(expected_revision, actual_revision)

        payload = _serialized_state(state)
        if current is None:
            row = (
                await connection.execute(
                    text(
                        """
                        INSERT INTO campaign_draft(singleton_key, state, revision)
                        VALUES ('owner', CAST(:state AS JSONB), 1)
                        RETURNING revision, state, updated_at
                        """
                    ),
                    {"state": payload},
                )
            ).first()
        else:
            row = (
                await connection.execute(
                    text(
                        """
                        UPDATE campaign_draft
                        SET state = CAST(:state AS JSONB),
                            revision = revision + 1,
                            updated_at = clock_timestamp()
                        WHERE singleton_key = 'owner'
                          AND revision = :expected_revision
                        RETURNING revision, state, updated_at
                        """
                    ),
                    {"state": payload, "expected_revision": expected_revision},
                )
            ).first()
        if row is None:  # defensive: the FOR UPDATE lock makes this unreachable.
            raise CampaignDraftConflictError(expected_revision, actual_revision)
        return _document(row)

    async def delete(self, connection: AsyncConnection, *, expected_revision: int) -> bool:
        current = (
            await connection.execute(
                text(
                    """
                    SELECT revision
                    FROM campaign_draft
                    WHERE singleton_key = 'owner'
                    FOR UPDATE
                    """
                )
            )
        ).first()
        if current is None:
            if expected_revision == 0:
                return False
            raise CampaignDraftConflictError(expected_revision, None)
        actual_revision = int(current.revision)
        if actual_revision != expected_revision:
            raise CampaignDraftConflictError(expected_revision, actual_revision)
        await connection.execute(text("DELETE FROM campaign_draft WHERE singleton_key = 'owner'"))
        return True

    async def clear_if_revision(
        self,
        connection: AsyncConnection,
        *,
        revision: int | None,
    ) -> bool:
        """Clear only the exact draft whose immutable config was queued."""

        if revision is None:
            return False
        result = await connection.execute(
            text(
                """
                DELETE FROM campaign_draft
                WHERE singleton_key = 'owner' AND revision = :revision
                """
            ),
            {"revision": revision},
        )
        return result.rowcount == 1


campaign_drafts = CampaignDraftRepository()

__all__ = [
    "MAX_CAMPAIGN_DRAFT_BYTES",
    "CampaignDraftConflictError",
    "CampaignDraftRepository",
    "CampaignDraftTooLargeError",
    "campaign_drafts",
]
