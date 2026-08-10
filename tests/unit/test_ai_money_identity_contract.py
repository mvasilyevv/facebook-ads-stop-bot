from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ACTIVE_META_MONEY_TOOLS = (
    "core/ai_assistant/tools/meta/get_account_health.py",
    "core/ai_assistant/tools/meta/get_insights.py",
    "core/ai_assistant/tools/meta/get_offer_performance.py",
)
ACTIVE_OPERATOR_PROMPTS = (
    "core/ai_assistant/prompts/operator.md",
    "core/ai_assistant/prompts/analytics.md",
    "core/ai_assistant/prompts/skills/curator_case.md",
    "core/ai_assistant/prompts/skills/pulse_report.md",
    "core/ai_assistant/prompts/skills/web_chat.md",
)


def test_ai_meta_tools_never_invent_dollar_or_two_decimal_money() -> None:
    for relative_path in ACTIVE_META_MONEY_TOOLS:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "$" not in source, relative_path
        assert ":.2f" not in source, relative_path
        assert "format_major_money" in source, relative_path


def test_offer_performance_does_not_treat_meta_purchase_as_confirmed_deposit() -> None:
    source = (ROOT / "core/ai_assistant/tools/meta/get_offer_performance.py").read_text(
        encoding="utf-8"
    )

    assert "offsite_conversion.custom.deposit" not in source
    assert 'actions.get("purchase"' not in source


def test_operator_prompts_require_explicit_currency_identity() -> None:
    for relative_path in ACTIVE_OPERATOR_PROMPTS:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "$" not in source, relative_path

    analytics = (ROOT / "core/ai_assistant/prompts/analytics.md").read_text(encoding="utf-8")
    assert "Не предполагай USD" in analytics
    assert "mixed" in analytics
