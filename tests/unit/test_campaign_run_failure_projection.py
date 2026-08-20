from types import SimpleNamespace

import pytest

from apps.api.routers.v1.campaigns_create import (
    _campaign_run_failure_class,
    _public_run_progress,
)
from core.campaign_builder.execute import _ProgressState


def _controls(*, available: bool, reason: str) -> SimpleNamespace:
    return SimpleNamespace(resume=SimpleNamespace(available=available, reason=reason))


@pytest.mark.parametrize(
    ("expected", "outcome", "state", "task_reason", "control_available", "control_reason"),
    [
        (
            "manual_review",
            "UNKNOWN",
            "unknown",
            "partial_or_ack_lost",
            False,
            "external_boundary_crossed",
        ),
        (
            "safe_retry",
            "REJECTED",
            "failed",
            "creator_dependencies_unavailable",
            True,
            "pre_external_checkpoint_available",
        ),
        (
            "invalid_config",
            "REJECTED",
            "failed",
            "invalid_config",
            False,
            "checkpoint_reason_not_resumable",
        ),
        (
            "invalid_media",
            "REJECTED",
            "failed",
            "permanent_pre_external_failure",
            False,
            "media_checkpoint_incomplete",
        ),
        (
            "unavailable",
            "REJECTED",
            "failed",
            "permanent_pre_external_failure",
            False,
            "checkpoint_reason_not_resumable",
        ),
    ],
)
def test_campaign_failure_projection_preserves_operator_recovery_class(
    expected,
    outcome,
    state,
    task_reason,
    control_available,
    control_reason,
) -> None:
    assert (
        _campaign_run_failure_class(
            run_status="failed",
            task_outcome=outcome,
            task_state=state,
            task_reason=task_reason,
            external_started=False,
            controls=_controls(available=control_available, reason=control_reason),
        )
        == expected
    )


def test_public_progress_drops_arbitrary_fields_including_public_shaped_counts() -> None:
    projected = _public_run_progress(
        run_status="failed",
        value={
            "stage": "failed",
            "completed": 2,
            "total": 3,
            "reason": "internal_reason",
            "traceback": "secret",
        },
    )

    assert projected.model_dump() == {
        "stage": "failed",
        "completed": None,
        "total": None,
    }


def test_public_progress_projects_the_real_worker_snapshot_shape() -> None:
    checkpoint = _ProgressState(
        stage="creating",
        campaigns_done=1,
        adsets_done=2,
        uploads_done=4,
        creatives_done=3,
        ads_done=2,
        total_ads=6,
        campaign_create_attempted=True,
    ).snapshot()

    projected = _public_run_progress(run_status="creating", value=checkpoint)

    assert projected.model_dump() == {
        "stage": "creating",
        "completed": 2,
        "total": 6,
    }


# Воркер уже доказал, что повтор той же задачи вернёт тот же отказ, и записал
# это оператору словами. Карточка «Доступен безопасный повтор» рядом с такой
# причиной противоречит сама себе, поэтому класс не выводится из наличия
# контрольной точки: контрольная точка взята нарочно доступной.
def test_proven_unretryable_rejection_never_offers_a_safe_retry() -> None:
    assert (
        _campaign_run_failure_class(
            run_status="failed",
            task_outcome="REJECTED",
            task_state="failed",
            task_reason="browser_rejection_not_retryable",
            external_started=False,
            controls=_controls(available=True, reason="pre_external_checkpoint_available"),
        )
        == "unavailable"
    )


def test_missing_campaign_task_is_unavailable_not_a_false_manual_result() -> None:
    assert (
        _campaign_run_failure_class(
            run_status="failed",
            task_outcome=None,
            task_state=None,
            task_reason="",
            external_started=False,
            controls=_controls(available=False, reason="campaign_task_missing"),
        )
        == "unavailable"
    )
