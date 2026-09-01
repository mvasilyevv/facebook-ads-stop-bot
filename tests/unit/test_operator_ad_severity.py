from core.operator.queries import _approaching_only_clauses, _operator_ad_severity
from core.scanner.status import DELIVERY_DISABLED_STATUSES


def test_disapproved_is_critical_even_without_metrics() -> None:
    assert (
        _operator_ad_severity(
            delivery_status="DISAPPROVED",
            alert_state="normal",
            data_state="unavailable",
        )
        == "critical"
    )


def test_with_issues_and_review_statuses_need_attention() -> None:
    for status in ("WITH_ISSUES", "PENDING_REVIEW", "IN_REVIEW"):
        assert (
            _operator_ad_severity(
                delivery_status=status,
                alert_state="normal",
                data_state="ready",
            )
            == "warning"
        )


def test_confirmed_active_row_remains_ok() -> None:
    assert (
        _operator_ad_severity(
            delivery_status="ACTIVE",
            alert_state="normal",
            data_state="ready",
        )
        == "ok"
    )


def test_approaching_only_delivery_clause_excludes_confirmed_inactive_not_unconfirmed() -> None:
    """Issue 352 regression: the SQL feed behind "approaching stop" used to

    require `delivery_status = 'ACTIVE'`, which drops NULL/unconfirmed status
    the same as a confirmed-inactive one. The fixed clause must exclude only
    the statuses Meta actually confirmed as inactive.
    """
    clauses = _approaching_only_clauses()
    delivery_clause = next(clause for clause in clauses if "delivery_status" in clause)

    assert "NOT IN" in delivery_clause
    assert "= 'ACTIVE'" not in delivery_clause
    for status in DELIVERY_DISABLED_STATUSES:
        assert f"'{status}'" in delivery_clause
    # ACTIVE and an unrecognized/unconfirmed status must not be excluded.
    assert "'ACTIVE'" not in delivery_clause
