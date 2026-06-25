# -*- coding: utf-8 -*-
"""Unit-тесты execution-пайплайна создания кампании (без БД, без сети, без ffmpeg).

Покрывают:
- автоуникализацию: число копий = N (число adset'ов), детерминированный seed,
  распределение variant[i] → adset[i], adset i = K ads (1 на концепт);
- реальный execute поверх builder.plan_execution_steps: порядок
  campaign → adsets → upload → creatives → ads, статусы по launch_state,
  прогресс-колбэк после каждого шага, сбор created_meta_ids;
- классификацию ошибок воркера (permanent / transient / partial-create);
- статус-переходы run (queued → uniquifying → uploading → creating → succeeded|failed).

Всё внешнее (uniquify_image_bytes/video, MediaUploader, execute_graph_call) — замокано.
"""

from __future__ import annotations

import asyncio

import pytest

from core.campaign_builder import (
    Account,
    AdsetConfig,
    Budget,
    CampaignBlock,
    CampaignConfig,
    LaunchState,
    Targeting,
    build_campaign_spec,
)
from core.campaign_builder.execute import (
    PartialCreateError,
    classify_execution_error,
    execute_campaign_spec,
)
from core.campaign_builder.uniquify import (
    ConceptInput,
    UniquifiedAd,
    build_uniquification_plan,
    uniquify_concepts,
)
from core.meta_api.errors import (
    PermanentError,
    RateLimitedError,
    SessionUnavailableError,
    TemporaryError,
)

# ---------------------- фикстуры ----------------------


def _account() -> Account:
    return Account(act_id="123456789", page_id="111", pixel_id="222")


def _image_block(n_adsets: int = 3) -> CampaignBlock:
    """Image-кампания с n_adsets adset'ами."""
    adsets = [
        AdsetConfig(name="{byer} | {offer} | static | s%d | {date}" % i, dir=f"a{i}", glob="*.jpg")
        for i in range(1, n_adsets + 1)
    ]
    return CampaignBlock(
        key="static",
        name="{byer} | {offer} | static | adset.pro | {date}",
        kind="image",
        adsets=adsets,
    )


def _config(block: CampaignBlock | None = None, **overrides) -> CampaignConfig:
    base = dict(
        account=_account(),
        offer_code="GH_CR",
        destination_link="https://example.shop/x",
        start_date="2026-06-18",
        # Дефолт COST_CAP требует bid_amount_cents — ставим явный таргет CPA.
        budget=Budget(daily_cents=300, bid_amount_cents=500),
        targeting=Targeting(countries=["GH"]),
        campaigns=[block or _image_block()],
    )
    base.update(overrides)
    return CampaignConfig(**base)


def _concepts(kind: str, count: int) -> list[ConceptInput]:
    """K концептов одного типа."""
    return [
        ConceptInput(concept_id=f"c{i}", kind=kind, content=b"raw-%d" % i, filename=f"c{i}.jpg")
        for i in range(count)
    ]


# =====================================================================
#  3. Автоуникализация
# =====================================================================


# Число копий каждого концепта = числу adset'ов (default N).
def test_copies_per_concept_defaults_to_adset_count():
    block = _image_block(n_adsets=3)
    cfg = _config(block)
    concepts = _concepts("image", count=2)
    plan = build_uniquification_plan(cfg, block, concepts)
    # Каждый из 2 концептов → 3 варианта (по числу adset'ов).
    assert all(len(variants) == 3 for variants in plan.variants_by_concept.values())


# Явный copies_per_concept переопределяет дефолт.
def test_explicit_copies_per_concept_overrides():
    block = _image_block(n_adsets=3)
    cfg = _config(block, copies_per_concept=5)
    concepts = _concepts("image", count=2)
    plan = build_uniquification_plan(cfg, block, concepts)
    assert all(len(v) == 5 for v in plan.variants_by_concept.values())


# Распределение variant[i] → adset[i]: adset i получает по 1 варианту от каждого концепта.
def test_distribution_variant_i_to_adset_i():
    block = _image_block(n_adsets=3)
    cfg = _config(block)
    concepts = _concepts("image", count=2)
    plan = build_uniquification_plan(cfg, block, concepts)
    # adset i = K ads (по 1 на концепт), вариант с copy_index == i.
    assert len(plan.adsets) == 3
    for adset_index, adset in enumerate(plan.adsets):
        assert len(adset.ads) == 2  # K концептов
        for ad in adset.ads:
            assert ad.copy_index == adset_index


# Каждый ad ссылается на свой концепт и уникальный код креатива OFFER_CRxxx.
def test_ad_codes_unique_and_offer_prefixed():
    block = _image_block(n_adsets=2)
    cfg = _config(block)
    concepts = _concepts("image", count=2)
    plan = build_uniquification_plan(cfg, block, concepts)
    codes = [ad.code for adset in plan.adsets for ad in adset.ads]
    assert len(codes) == len(set(codes))  # все коды уникальны
    assert all(c.startswith("GH_CR_CR") for c in codes)


# Детерминированный seed: один и тот же (concept_id, i) даёт один seed → идемпотентный retry.
def test_seed_deterministic():
    block = _image_block(n_adsets=3)
    cfg = _config(block)
    concepts = _concepts("image", count=1)
    p1 = build_uniquification_plan(cfg, block, concepts)
    p2 = build_uniquification_plan(cfg, block, concepts)
    seeds1 = [ad.seed for adset in p1.adsets for ad in adset.ads]
    seeds2 = [ad.seed for adset in p2.adsets for ad in adset.ads]
    assert seeds1 == seeds2
    # Разные (concept_id, i) → разные seed.
    assert len(set(seeds1)) == len(seeds1)


# uniquify_concepts для фото зовёт image-уникализатор по разу на (концепт × копию).
def test_uniquify_concepts_image_calls(monkeypatch):
    calls: list[tuple] = []

    def fake_image(content, *, source_name, copy_index, creative_index, run_slug):
        calls.append((source_name, copy_index, creative_index))
        return b"jpeg-%s-%d" % (source_name.encode(), copy_index)

    monkeypatch.setattr("core.campaign_builder.uniquify.uniquify_image_bytes", fake_image)

    block = _image_block(n_adsets=2)
    cfg = _config(block)
    concepts = _concepts("image", count=2)
    plan = build_uniquification_plan(cfg, block, concepts)

    async def run():
        return await uniquify_concepts(cfg, block, concepts, plan)

    materialized = asyncio.run(run())
    # 2 концепта × 2 копии = 4 уникализированных байта.
    assert len(calls) == 4
    # У каждого ad появились байты варианта.
    all_ads = [a for adset in materialized for a in adset.ads]
    assert all(isinstance(a.media_bytes, bytes) and a.media_bytes for a in all_ads)


# =====================================================================
#  4. Реальный execute поверх плана шагов
# =====================================================================


class _FakeClient:
    """Замоканный MetaApiClient.execute_graph_call с авто-ID по endpoint."""

    def __init__(self, fail_on: str | None = None, error: Exception | None = None):
        self.calls: list[dict] = []
        self._counter = 0
        self._fail_on = fail_on
        self._error = error or PermanentError("boom")

    async def execute_graph_call(self, *, method, endpoint, body_json=None, ad_account_id=None):
        self.calls.append(
            {"method": method, "endpoint": endpoint, "body": body_json, "act": ad_account_id}
        )
        # endswith: '/ads' иначе матчит и '/adsets'.
        if self._fail_on and endpoint.endswith(self._fail_on):
            raise self._error
        self._counter += 1
        kind = "obj"
        if "campaigns" in endpoint:
            kind = "camp"
        elif "adsets" in endpoint:
            kind = "adset"
        elif "adcreatives" in endpoint:
            kind = "creative"
        elif endpoint.endswith("/ads"):
            kind = "ad"
        return {"id": f"{kind}-{self._counter}"}


class _FakeUploader:
    """Замоканный MediaUploader."""

    def __init__(self):
        self.images: list[bytes] = []

    async def upload_image(self, ad_account_id, image_bytes, *, filename="upload.jpg", **kw):
        self.images.append(image_bytes)
        return f"hash-{len(self.images)}"

    async def upload_video_from_bytes(self, ad_account_id, video_bytes, *, filename="upload.mp4"):
        self.images.append(video_bytes)
        return f"vid-{len(self.images)}"


def _patch_uniquify(monkeypatch):
    """Заглушка image-уникализатора — не трогаем PIL."""
    monkeypatch.setattr(
        "core.campaign_builder.uniquify.uniquify_image_bytes",
        lambda content, **kw: b"jpeg-bytes",
    )


# Явный copies_per_concept != числу adset'ов не ломает execute: раскладка
# выравнивается на число adset'ов spec'а (нет IndexError/рассинхрона).
def test_execute_aligns_copies_to_spec_adsets(monkeypatch):
    _patch_uniquify(monkeypatch)
    block = _image_block(n_adsets=2)
    # copies_per_concept=5, но spec всегда имеет 2 adset'а → execute создаёт ровно 2.
    cfg = _config(block, copies_per_concept=5)
    spec = build_campaign_spec(cfg)
    concepts = _concepts("image", count=2)
    client = _FakeClient()
    uploader = _FakeUploader()

    async def run():
        return await execute_campaign_spec(
            cfg,
            spec,
            concepts_by_campaign={block.key: concepts},
            client=client,
            uploader=uploader,
        )

    result = asyncio.run(run())
    assert len(result.created_meta_ids["adsets"]) == 2
    # 2 adset × 2 концепта = 4 ad.
    assert len(result.created_meta_ids["ads"]) == 4


# Полный успешный прогон: campaign → adsets → upload → creatives → ads, прогресс растёт.
def test_execute_full_success(monkeypatch):
    _patch_uniquify(monkeypatch)
    block = _image_block(n_adsets=2)
    cfg = _config(block)
    spec = build_campaign_spec(cfg)
    concepts = _concepts("image", count=2)
    client = _FakeClient()
    uploader = _FakeUploader()
    progress_log: list[dict] = []

    async def on_progress(snapshot):
        progress_log.append(dict(snapshot))

    async def run():
        return await execute_campaign_spec(
            cfg,
            spec,
            concepts_by_campaign={block.key: concepts},
            client=client,
            uploader=uploader,
            on_progress=on_progress,
        )

    result = asyncio.run(run())
    # 1 campaign + 2 adsets + (2 adsets × 2 concepts) creatives + столько же ads.
    assert len(result.created_meta_ids["campaigns"]) == 1
    assert len(result.created_meta_ids["adsets"]) == 2
    assert len(result.created_meta_ids["creatives"]) == 4
    assert len(result.created_meta_ids["ads"]) == 4
    # Прогресс прошёл все стадии.
    stages = {p["stage"] for p in progress_log}
    assert {"uploading", "creating"} <= stages


# launch_state=campaign_paused: кампания PAUSED, adset'ы и ads ACTIVE.
def test_execute_launch_state_campaign_paused(monkeypatch):
    _patch_uniquify(monkeypatch)
    block = _image_block(n_adsets=1)
    cfg = _config(block, launch_state=LaunchState.CAMPAIGN_PAUSED)
    spec = build_campaign_spec(cfg)
    concepts = _concepts("image", count=1)
    client = _FakeClient()
    uploader = _FakeUploader()

    async def run():
        return await execute_campaign_spec(
            cfg,
            spec,
            concepts_by_campaign={block.key: concepts},
            client=client,
            uploader=uploader,
        )

    asyncio.run(run())
    camp_calls = [c for c in client.calls if "campaigns" in c["endpoint"]]
    adset_calls = [c for c in client.calls if "adsets" in c["endpoint"]]
    ad_calls = [c for c in client.calls if c["endpoint"].endswith("/ads")]
    assert camp_calls[0]["body"]["status"] == "PAUSED"
    assert adset_calls[0]["body"]["status"] == "ACTIVE"
    assert ad_calls[0]["body"]["status"] == "ACTIVE"


# launch_state=all_paused: всё PAUSED.
def test_execute_launch_state_all_paused(monkeypatch):
    _patch_uniquify(monkeypatch)
    block = _image_block(n_adsets=1)
    cfg = _config(block, launch_state=LaunchState.ALL_PAUSED)
    spec = build_campaign_spec(cfg)
    concepts = _concepts("image", count=1)
    client = _FakeClient()
    uploader = _FakeUploader()

    async def run():
        return await execute_campaign_spec(
            cfg,
            spec,
            concepts_by_campaign={block.key: concepts},
            client=client,
            uploader=uploader,
        )

    asyncio.run(run())
    adset_calls = [c for c in client.calls if "adsets" in c["endpoint"]]
    ad_calls = [c for c in client.calls if c["endpoint"].endswith("/ads")]
    assert adset_calls[0]["body"]["status"] == "PAUSED"
    assert ad_calls[0]["body"]["status"] == "PAUSED"


# Все вызовы адресуют явно заданный кабинет (act_id из config).
def test_execute_addresses_explicit_account(monkeypatch):
    _patch_uniquify(monkeypatch)
    block = _image_block(n_adsets=1)
    cfg = _config(block)
    spec = build_campaign_spec(cfg)
    concepts = _concepts("image", count=1)
    client = _FakeClient()
    uploader = _FakeUploader()

    async def run():
        return await execute_campaign_spec(
            cfg,
            spec,
            concepts_by_campaign={block.key: concepts},
            client=client,
            uploader=uploader,
        )

    asyncio.run(run())
    assert all(c["act"] == cfg.account.act for c in client.calls)


# Падение на середине (после создания кампании) → PartialCreateError с уже созданными id.
def test_execute_partial_create_raises_with_created_ids(monkeypatch):
    _patch_uniquify(monkeypatch)
    block = _image_block(n_adsets=1)
    cfg = _config(block)
    spec = build_campaign_spec(cfg)
    concepts = _concepts("image", count=1)
    # Падаем на создании adset (кампания уже создана).
    client = _FakeClient(fail_on="/adsets", error=PermanentError("adset rejected"))
    uploader = _FakeUploader()

    async def run():
        return await execute_campaign_spec(
            cfg,
            spec,
            concepts_by_campaign={block.key: concepts},
            client=client,
            uploader=uploader,
        )

    with pytest.raises(PartialCreateError) as ei:
        asyncio.run(run())
    assert ei.value.created_ids["campaigns"]  # кампания уже создана


# HIGH-2 money-safety: сбой НА POST campaign (ответ Meta потерян) → PartialCreateError
# (ack-lost: кампания могла создаться), даже если created пуст и причина transient.
# Повтор такого залива = дубль кампании, поэтому НЕ transient/requeue.
def test_execute_fail_on_campaign_post_is_partial_not_transient(monkeypatch):
    _patch_uniquify(monkeypatch)
    block = _image_block(n_adsets=1)
    cfg = _config(block)
    spec = build_campaign_spec(cfg)
    concepts = _concepts("image", count=1)
    client = _FakeClient(fail_on="/campaigns", error=SessionUnavailableError("no vision"))
    uploader = _FakeUploader()

    async def run():
        return await execute_campaign_spec(
            cfg,
            spec,
            concepts_by_campaign={block.key: concepts},
            client=client,
            uploader=uploader,
        )

    with pytest.raises(PartialCreateError) as ei:
        asyncio.run(run())
    # created пуст (id не вернулся), но POST инициирован → orphan на ручную проверку.
    assert ei.value.created_ids["campaigns"] == []
    assert ei.value.irreversible_attempted is True
    # Классификация: НЕ transient (иначе requeue → дубль), а partial.
    assert classify_execution_error(ei.value) == "partial"


# =====================================================================
#  Классификация ошибок
# =====================================================================


# Permanent-ошибки Meta → permanent (без retry).
@pytest.mark.parametrize("exc", [PermanentError("x"), ValueError("bad config")])
def test_classify_permanent(exc):
    assert classify_execution_error(exc) == "permanent"


# Transient-ошибки (сеть/rate-limit/session) → transient (requeue).
@pytest.mark.parametrize(
    "exc",
    [RateLimitedError("rl"), SessionUnavailableError("s"), TemporaryError("t")],
)
def test_classify_transient(exc):
    assert classify_execution_error(exc) == "transient"


# PartialCreateError → partial (mark_failed без retry, дубли недопустимы).
def test_classify_partial():
    exc = PartialCreateError("half done", created_ids={"campaigns": ["c1"]}, failed_step="adsets")
    assert classify_execution_error(exc) == "partial"


def test_uniquified_ad_dataclass_fields():
    """UniquifiedAd хранит концепт, copy_index, код и seed (контракт распределения)."""
    ad = UniquifiedAd(
        concept_id="c0", copy_index=0, code="GH_CR_CR001", seed="GH_CR:c0:0", media_bytes=None
    )
    assert ad.concept_id == "c0"
    assert ad.copy_index == 0
