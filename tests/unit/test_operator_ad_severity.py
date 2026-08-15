from core.operator.queries import _operator_ad_severity


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
