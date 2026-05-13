"""Тесты на свёртку сырых событий в UserAction."""

from core.campaign_recorder.analyzer import denoise


def _ev(type_, xpath, ts, value=None, selectors=None, text="", label=None, heading=None):
    return {
        "type": type_,
        "xpath": xpath,
        "ts": ts,
        "value": value,
        "selector_candidates": selectors or [],
        "text": text,
        "label_text": label,
        "aria_label": None,
        "nearest_heading": heading,
        "tag": "div",
    }


def test_pointerdown_mousedown_click_collapse_to_single_click():
    """pointerdown + mousedown + click на одном элементе в пределах 200мс → один click."""
    events = [
        _ev("pointerdown", "/x", 1.00, selectors=["s1"]),
        _ev("mousedown", "/x", 1.05, selectors=["s1"]),
        _ev("click", "/x", 1.10, selectors=["s1"], text="OK"),
    ]
    actions = denoise(events)
    assert len(actions) == 1
    assert actions[0].kind == "click"


def test_consecutive_input_collapses_to_single_fill():
    """Подряд input на одном поле → один fill с финальным значением."""
    events = [
        _ev("input", "/i", 2.0, value="a", selectors=["s2"]),
        _ev("input", "/i", 2.1, value="ab", selectors=["s2"]),
        _ev("input", "/i", 2.2, value="abc", selectors=["s2"]),
    ]
    actions = denoise(events)
    assert len(actions) == 1
    assert actions[0].kind == "fill"
    assert actions[0].value == "abc"


def test_change_on_select_becomes_select_action():
    """change на <select> → select."""
    events = [{**_ev("change", "/s", 3.0, value="opt1", selectors=["s3"]), "tag": "select"}]
    actions = denoise(events)
    assert len(actions) == 1
    assert actions[0].kind == "select"
    assert actions[0].value == "opt1"


def test_noise_click_without_selectors_and_text_dropped():
    """Клик без selector_candidates и без text — отбрасывается."""
    events = [_ev("click", "/n", 4.0, selectors=[], text="")]
    actions = denoise(events)
    assert actions == []
