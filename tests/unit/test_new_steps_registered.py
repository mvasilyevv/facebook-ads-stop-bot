# -*- coding: utf-8 -*-
# Сценарий: 6 новых шагов плана зарегистрированы в STEP_REGISTRY.
def test_new_steps_in_registry():
    from core.campaign_creator.steps.registry import STEP_REGISTRY

    for name in [
        "duplicate_ad",
        "rename_ad",
        "reattach_creative",
        "duplicate_adset",
        "rename_adset",
        "switch_to_adset",
    ]:
        assert name in STEP_REGISTRY, f"{name} не зарегистрирован"


# Сценарий: класс шага имеет execute с тремя параметрами (page, ctx, params).
def test_new_steps_signature():
    import inspect

    from core.campaign_creator.steps.registry import STEP_REGISTRY

    for name in [
        "duplicate_ad",
        "rename_ad",
        "reattach_creative",
        "duplicate_adset",
        "rename_adset",
        "switch_to_adset",
    ]:
        sig = inspect.signature(STEP_REGISTRY[name].execute)
        params = list(sig.parameters.keys())
        # self + page + context/ctx + params
        assert len(params) >= 4, f"{name}: подпись {params!r}"
