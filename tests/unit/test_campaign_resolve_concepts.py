# -*- coding: utf-8 -*-
"""Unit-тесты resolve_concepts_from_config — ЕДИНЫЙ источник концептов (money-CRIT).

Закрывает рассогласование preview != залив: воркер материализует РОВНО те файлы,
что назначены кампании в `block.concept_refs` (имена из upload-ответа), а не glob по
папке. validate считает len(concept_refs) — резолв обязан вернуть ровно столько же
концептов, иначе байер апрувит одно число ads, а заливается другое.

Контракт хранения: upload кладёт файл по имени в `{CAMPAIGN_UPLOAD_ROOT}/{upload_id}/{ref}`,
creo_root = upload_id → резолв читает `{upload_root}/{creo_root}/{ref}`.
"""

from __future__ import annotations

import pytest

from apps.campaign_creator_worker import resolve_concepts_from_config
from core.campaign_builder.config import (
    Account,
    AdsetConfig,
    Budget,
    CampaignBlock,
    CampaignConfig,
    Targeting,
)


def _block(key: str, kind: str, concept_refs: list[str], n_adsets: int = 2) -> CampaignBlock:
    adsets = [
        AdsetConfig(name=f"{{byer}} | s{i} | {{date}}", dir=f"a{i}", glob="*")
        for i in range(1, n_adsets + 1)
    ]
    return CampaignBlock(
        key=key,
        name="{byer} | {offer} | adset.pro | {date}",
        kind=kind,
        adsets=adsets,
        concept_refs=concept_refs,
    )


def _config(creo_root: str, blocks: list[CampaignBlock]) -> CampaignConfig:
    return CampaignConfig(
        account=Account(act_id="123456789", page_id="111", pixel_id="222"),
        offer_code="GH_CR",
        destination_link="https://example.shop/x",
        start_date="2026-06-18",
        creo_root=creo_root,
        # Дефолт COST_CAP требует bid_amount_cents — ставим явный таргет CPA.
        budget=Budget(daily_cents=300, bid_amount_cents=500),
        targeting=Targeting(countries=["GH"]),
        campaigns=blocks,
    )


# Контракт preview==залив: concept_refs=['a.jpg','b.jpg'] + те же файлы в media store →
# резолв возвращает РОВНО 2 концепта на блок (== len(concept_refs), == число validate).
def test_resolve_returns_one_concept_per_ref(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    upload_id = "abc123"
    media_dir = upload_root / upload_id
    media_dir.mkdir(parents=True)
    (media_dir / "a.jpg").write_bytes(b"img-a")
    (media_dir / "b.jpg").write_bytes(b"img-b")
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(upload_root))

    cfg = _config(upload_id, [_block("static", "image", ["a.jpg", "b.jpg"])])
    out = resolve_concepts_from_config(cfg)

    assert list(out.keys()) == ["static"]
    concepts = out["static"]
    # Ровно len(concept_refs) концептов → validate (len) и залив дают одно число.
    assert len(concepts) == len(cfg.campaigns[0].concept_refs) == 2
    assert {c.filename for c in concepts} == {"a.jpg", "b.jpg"}
    # Фото грузятся как bytes (не path).
    assert all(c.kind == "image" and c.content is not None for c in concepts)


# Видео-блок: каждый ref читается как path (ffmpeg по файлу), content=None.
def test_resolve_video_refs_as_path(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    media_dir = upload_root / "vid1"
    media_dir.mkdir(parents=True)
    (media_dir / "clip.mp4").write_bytes(b"\x00\x00")
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(upload_root))

    cfg = _config("vid1", [_block("video", "video", ["clip.mp4"])])
    out = resolve_concepts_from_config(cfg)

    concept = out["video"][0]
    assert concept.kind == "video"
    assert concept.content is None
    assert concept.path is not None and concept.path.endswith("clip.mp4")


# Резолв читает РОВНО назначенные refs, игнорируя лишние файлы в папке (НЕ glob).
def test_resolve_ignores_unassigned_files(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    media_dir = upload_root / "up"
    media_dir.mkdir(parents=True)
    (media_dir / "a.jpg").write_bytes(b"a")
    (media_dir / "b.jpg").write_bytes(b"b")
    (media_dir / "stray.jpg").write_bytes(b"stray")  # не назначен — должен игнорироваться
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(upload_root))

    cfg = _config("up", [_block("static", "image", ["a.jpg"])])
    out = resolve_concepts_from_config(cfg)

    assert len(out["static"]) == 1
    assert out["static"][0].filename == "a.jpg"


# Пустой concept_refs → ValueError (нет концептов = невалидный залив, защита от пустой кампании).
def test_resolve_empty_refs_raises(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    (upload_root / "up").mkdir(parents=True)
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(upload_root))

    cfg = _config("up", [_block("static", "image", [])])
    with pytest.raises(ValueError, match="пустой concept_refs"):
        resolve_concepts_from_config(cfg)


# Назначенный ref, которого нет на диске → ValueError (рассинхрон upload/назначения).
def test_resolve_missing_file_raises(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    (upload_root / "up").mkdir(parents=True)
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(upload_root))

    cfg = _config("up", [_block("static", "image", ["ghost.jpg"])])
    with pytest.raises(ValueError, match="не найден"):
        resolve_concepts_from_config(cfg)


# Path traversal в ref срезается до имени файла (../etc/passwd → passwd, не выход из папки).
def test_resolve_strips_path_traversal(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    media_dir = upload_root / "up"
    media_dir.mkdir(parents=True)
    (media_dir / "a.jpg").write_bytes(b"a")
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(upload_root))

    # ref с traversal → Path(ref).name = 'a.jpg', читаем из media_dir.
    cfg = _config("up", [_block("static", "image", ["../../a.jpg"])])
    out = resolve_concepts_from_config(cfg)
    assert out["static"][0].filename == "a.jpg"


# Абсолютный creo_root (legacy/тест) берётся как есть, без префикса upload-root.
def test_resolve_absolute_creo_root(tmp_path):
    media_dir = tmp_path / "creo"
    media_dir.mkdir()
    (media_dir / "a.jpg").write_bytes(b"a")

    cfg = _config(str(media_dir), [_block("static", "image", ["a.jpg"])])
    out = resolve_concepts_from_config(cfg)
    assert len(out["static"]) == 1


# КОНТРАКТ-ТЕСТ preview==залив: число ads из validate (build_campaign_spec с
# concept_counts=len(concept_refs)) == число концептов, которое реально материализует
# воркер (резолв по тем же refs). Один список refs → одно число объектов на обеих
# сторонах (money-CRIT закрыт).
def test_validate_ad_count_matches_resolver(tmp_path, monkeypatch):
    from apps.api.routers.v1.schemas.campaigns_create import CampaignConfigIn
    from core.campaign_builder.builder import build_campaign_spec

    upload_root = tmp_path / "uploads"
    media_dir = upload_root / "up1"
    media_dir.mkdir(parents=True)
    for ref in ("a.jpg", "b.jpg"):
        (media_dir / ref).write_bytes(b"x")
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(upload_root))

    # Плоский вход фронта: 1 кампания, 2 adset, concept_refs=['a.jpg','b.jpg'].
    flat = {
        "act_id": "123",
        "page_id": "100",
        "pixel_id": "200",
        "offer_code": "GH_CR",
        "destination_link": "https://example.com",
        "daily_budget_cents": 20000,
        "bid_amount_cents": 500,  # дефолт COST_CAP требует таргет CPA
        "countries": ["DE"],
        "creo_root": "up1",
        "campaigns": [
            {"key": "static", "kind": "image", "adset_count": 2, "concept_refs": ["a.jpg", "b.jpg"]}
        ],
    }
    cfg_in = CampaignConfigIn.model_validate(flat)
    dom = cfg_in.to_domain()

    # Сторона validate: build_campaign_spec с раскладкой из concept_counts (=len refs).
    spec = build_campaign_spec(dom, concept_counts=cfg_in.concept_counts())
    validate_ads = sum(len(a.ads) for block in spec.campaigns for a in block.adsets)
    # K×N = 2 концепта × 2 adset = 4 ads.
    assert validate_ads == 4

    # Сторона залива: резолв возвращает ровно K концептов на блок → материализуется K×N.
    resolved = resolve_concepts_from_config(dom)
    k_concepts = len(resolved["static"])
    n_adsets = len(dom.campaigns[0].adsets)
    assert k_concepts == 2
    assert k_concepts * n_adsets == validate_ads
