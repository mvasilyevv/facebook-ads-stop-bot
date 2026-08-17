from apps.api.routers.v1.schemas.campaigns_create import PresetIn


def _preset(**overrides):
    values = {"name": "GH Aviator", "countries": ["GH"], "daily_budget": "8.99"}
    values.update(overrides)
    return PresetIn(**values)


def test_preset_carries_the_bid_it_requires() -> None:
    """COST_CAP без `bid_amount` не собирается вовсе.

    Заготовка без ставки была неполна ровно в том поле, которое обязательно:
    оператор загружал пресет и всё равно вводил ставку руками.
    """
    assert _preset(bid_strategy="COST_CAP", bid_amount="1.20").bid_amount == "1.20"


def test_preset_carries_strategy_and_display_link() -> None:
    preset = _preset(bid_strategy="LOWEST_COST_WITHOUT_CAP", display_link="play.ghana.com")

    assert preset.bid_strategy == "LOWEST_COST_WITHOUT_CAP"
    assert preset.display_link == "play.ghana.com"


def test_preset_defaults_stay_empty_not_zero() -> None:
    """Пустая ставка — «в заготовке нет», а не подтверждённый ноль."""
    preset = _preset()

    assert preset.bid_amount == ""
    assert preset.display_link == ""
    assert preset.bid_strategy == "COST_CAP"
