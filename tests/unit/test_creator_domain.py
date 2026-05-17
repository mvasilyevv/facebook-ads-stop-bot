# Проверяем что enum PlanRunStatus содержит все требуемые значения.
from core.domain import PlanRunStatus


def test_plan_run_status_values():
    assert {s.value for s in PlanRunStatus} == {
        "queued",
        "running",
        "success",
        "failed",
        "requires_attention",
    }
