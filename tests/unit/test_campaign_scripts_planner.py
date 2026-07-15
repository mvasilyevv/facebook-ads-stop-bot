from core.campaign_scripts.planner import _build_url_params


def test_planner_url_params_include_stable_meta_ad_id() -> None:
    params = _build_url_params(sub2="MV", ad_name="GH_CR1", cabinet_id="123")

    assert params.endswith("&sub8={{ad.id}}")
    assert params.count("sub8=") == 1
