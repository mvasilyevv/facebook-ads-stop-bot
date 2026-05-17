# Проверяем что Plan/PlanRun импортируются и имеют ожидаемые поля.
from core.models import Plan, PlanRun


def test_plan_columns():
    cols = {c.name for c in Plan.__table__.columns}
    assert {
        "id",
        "name",
        "schema_version",
        "steps",
        "is_active",
        "created_at",
        "updated_at",
    } <= cols


def test_planrun_columns():
    cols = {c.name for c in PlanRun.__table__.columns}
    assert {
        "id",
        "plan_id",
        "profile_id",
        "variables",
        "status",
        "started_at",
        "finished_at",
        "step_log",
        "error_message",
    } <= cols
