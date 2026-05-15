# Сценарий: PlanAction сериализуется в dict и обратно, CampaignSpec валидируется, FBState отмечает выполненные шаги
from core.campaign_creator.plan_types import AdsetSpec, CampaignSpec, FBState, PlanAction


# Сценарий: round-trip сериализации PlanAction через to_dict/from_dict
def test_plan_action_roundtrip():
    a = PlanAction(step="set_geo", params={"countries": ["KE"]}, idempotent=True)
    assert PlanAction.from_dict(a.to_dict()) == a


# Сценарий: минимальная валидная CampaignSpec с одним AdsetSpec
def test_campaign_spec_minimal():
    spec = CampaignSpec(
        offer_code="KE_CR2",
        cabinet_id="act_123",
        pixel_id="PX",
        landing_url="https://x",
        countries=["KE"],
        daily_budget=50.0,
        attribution_days=7,
        budget_level="CBO",
        adsets=[
            AdsetSpec(
                name_suffix="A",
                creo_subfolder="1",
                headline="H",
                primary_text="P",
                creatives=["v1.mp4"],
            )
        ],
    )
    assert spec.adsets[0].creatives == ["v1.mp4"]


# Сценарий: FBState помечает индекс как выполненный
def test_fbstate_mark_done():
    s = FBState()
    s.mark_done(0)
    assert s.is_done(0)
    assert not s.is_done(1)
