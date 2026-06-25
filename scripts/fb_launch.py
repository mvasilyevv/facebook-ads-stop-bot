# -*- coding: utf-8 -*-
"""Универсальный движок залива FB-кампаний из YAML-конфига (gambling, по SOP campaign-launch.md).

Один движок на все заливы. Конкретика (кабинет/пиксель/страница/гео/бюджет/структура/
креативы) — в YAML-конфиге, не в коде. Канал — прямой gRPC stub ExecuteGraphCall БЕЗ
поля ad_account_id (proto отстал; primary-вкладка Vision = нужный кабинет), upload через
MediaUploader. Порядок: campaign → adsets → upload media → creatives → ads (без JSONPath).

Режимы:
    python scripts/fb_launch.py --config scripts/launch_configs/X.yaml          # spec-print (dry)
    python scripts/fb_launch.py --config X.yaml --go                            # боевое (PAUSED)
    python scripts/fb_launch.py --config X.yaml --go --only static              # одна кампания
    python scripts/fb_launch.py --config X.yaml --cleanup <id> [<id>...]        # снести объекты

Статус всех объектов — PAUSED. ACTIVE не ставим (байер сам unpause после ревью).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grpc
import yaml
from pydantic import BaseModel, Field, model_validator

from clients.python_grpc.v1 import meta_api_pb2, meta_api_pb2_grpc

# Движок и схема конфига вынесены в core.campaign_builder — CLI их переиспользует
# без форка логики (тела объектов, нейминг, валидация бюджета/таргета).
from core.campaign_builder import (
    Account,
    AdsetConfig,
    AdText,
    Attribution,
    Budget,
    CampaignBlock,
    CampaignConfig,
    LaunchState,
    Targeting,
    ad_body,
    adset_body,
    campaign_body,
    image_creative_body,
    render_name,
    url_tags_of,
    video_creative_body,
)
from core.meta_api.client import MetaApiClient
from core.meta_api.mutations._batch_helpers import (
    build_batch_payload,
    make_batch_entry,
    parse_batch_response,
)
from core.meta_api.upload import MediaUploader

NAV_MARKERS = ("Execution context was destroyed", "navigation", "Target closed", "destroyed")


# ====================== CLI-only схема (YAML disk-flow) ======================
# Account/Budget/Targeting/Attribution/AdText переиспользуются из campaign_builder.
# Здесь только то, что специфично для YAML-залива с диска: коды креативов из имён
# файлов (CodeResolver) и обёртка LaunchConfig, которая адаптируется к CampaignConfig.


class CreativeCodes(BaseModel):
    mode: str = "filename"  # filename | map | auto
    map: dict[str, str] = Field(default_factory=dict)  # base_suffix -> code
    auto_prefix: str = ""  # для mode=auto: GH_CR_CR -> GH_CR_CR001…


class AdsetCfg(BaseModel):
    name: str
    dir: str
    glob: str


class CampaignCfg(BaseModel):
    key: str
    name: str
    kind: str  # image | video
    adsets: list[AdsetCfg]

    @model_validator(mode="after")
    def _k(self):
        if self.kind not in ("image", "video"):
            raise ValueError("campaign.kind: image | video")
        return self


class LaunchConfig(BaseModel):
    account: Account
    offer_code: str
    byer_tag: str = "MV"
    objective: str = "OUTCOME_SALES"
    optimization_goal: str = "OFFSITE_CONVERSIONS"
    custom_event_type: str = "PURCHASE"
    special_ad_categories: list[str] = Field(default_factory=lambda: ["NONE"])
    destination_link: str
    cta: str = "PLAY_GAME"
    text_optimizations: str = "OPT_OUT"
    start_date: str  # YYYY-MM-DD (= следующий день по SOP)
    creo_root: str
    # budget обязателен: дефолт-стратегия COST_CAP требует bid_amount_cents (как в
    # core.campaign_builder.CampaignConfig) — пустого дефолта у money-конфига нет.
    budget: Budget
    targeting: Targeting
    attribution: Attribution = Field(default_factory=Attribution)
    ad_text: AdText = Field(default_factory=AdText)
    creative_codes: CreativeCodes = Field(default_factory=CreativeCodes)
    campaigns: list[CampaignCfg]

    @property
    def start_time(self) -> str:
        return f"{self.start_date}T00:00:00{self.account.tz_offset}"

    @property
    def date_label(self) -> str:
        # YYYY-MM-DD -> DD.MM (для имени кампании)
        y, m, d = self.start_date.split("-")
        return f"{d}.{m}"

    def render(self, template: str) -> str:
        return render_name(
            template, byer=self.byer_tag, offer=self.offer_code, date_label=self.date_label
        )

    def as_campaign_config(self) -> CampaignConfig:
        """Адаптер YAML-конфига к CampaignConfig для общих body-builder'ов.

        CLI всегда заливает PAUSED (байер сам unpause после ревью) — launch_state
        фиксирован ALL_PAUSED. campaigns/copies для спеки не нужны (CLI ходит по
        файлам с диска), но CampaignConfig требует непустой список — даём заглушку.
        """
        stub_block = CampaignBlock(
            key="cli",
            name="{byer} | {offer} | {date}",
            kind="image",
            adsets=[AdsetConfig(name="{byer} | {offer} | {date}", dir=".", glob="*")],
        )
        return CampaignConfig(
            account=self.account,
            offer_code=self.offer_code,
            byer_tag=self.byer_tag,
            objective=self.objective,
            optimization_goal=self.optimization_goal,
            custom_event_type=self.custom_event_type,
            special_ad_categories=self.special_ad_categories,
            destination_link=self.destination_link,
            cta=self.cta,
            text_optimizations=self.text_optimizations,
            start_date=self.start_date,
            creo_root=self.creo_root,
            budget=self.budget,
            targeting=self.targeting,
            attribution=self.attribution,
            ad_text=self.ad_text,
            campaigns=[stub_block],
            launch_state=LaunchState.ALL_PAUSED,
        )


# ====================== Код креатива из файла ======================


def _base_suffix(path: Path) -> str:
    """static_4962618_v1.jpg -> static_4962618 (отрезаем _vN)."""
    return re.sub(r"_v\d+$", "", path.stem)


def _copy_idx(path: Path) -> str:
    m = re.search(r"_v(\d+)$", path.stem)
    return m.group(1) if m else "1"


class CodeResolver:
    def __init__(self, cfg: CreativeCodes, offer_code: str):
        self.cfg = cfg
        self.offer = offer_code
        self._auto: dict[str, str] = {}
        self._auto_seq = 0

    def code_of(self, path: Path) -> str:
        base = _base_suffix(path)
        if self.cfg.mode == "map":
            if base not in self.cfg.map:
                raise ValueError(f"creative_codes.map нет ключа '{base}' (файл {path.name})")
            return self.cfg.map[base]
        if self.cfg.mode == "auto":
            if base not in self._auto:
                self._auto_seq += 1
                prefix = self.cfg.auto_prefix or f"{self.offer}_CR"
                self._auto[base] = f"{prefix}{self._auto_seq:03d}"
            return self._auto[base]
        # filename: имя файла (без _vN) уже есть код
        return base


# ====================== stub-канал ======================


async def graph_call(stub, method: str, endpoint: str, query_params: dict) -> dict:
    req = meta_api_pb2.ExecuteGraphCallRequest(
        session_id="",
        method=method.upper(),
        endpoint=endpoint,
        query_params={k: str(v) for k, v in query_params.items()},
    )
    resp = await stub.ExecuteGraphCall(req, timeout=60.0)
    if resp.HasField("error"):
        e = resp.error
        raise RuntimeError(f"Meta code={e.code} sub={e.subcode} {e.message} trace={e.fbtrace_id}")
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.response_json[:400]}")
    return json.loads(resp.response_json) if resp.response_json else {}


async def run_batch(stub, entries: list[dict], label: str) -> list[dict]:
    resp = await graph_call(stub, "POST", "/", {"batch": build_batch_payload(entries)})
    subs = parse_batch_response(resp, expected_count=len(entries))
    ok = sum(1 for s in subs if s["success"])
    print(f"  [{label}] {ok}/{len(entries)} ok")
    for s in subs:
        if not s["success"]:
            err = s.get("body") if isinstance(s.get("body"), dict) else s.get("error")
            print(f"    FAIL entry#{s['index']} code={s['code']} err={err}")
    return subs


def ids_from(subs: list[dict]) -> list[str | None]:
    out: list[str | None] = []
    for s in subs:
        body = s.get("body") if s["success"] else None
        out.append(str(body["id"]) if isinstance(body, dict) and body.get("id") else None)
    return out


# ====================== медиа ======================


def extract_thumbnail(video_path: Path, thumbs_dir: Path) -> Path:
    out = thumbs_dir / (video_path.stem + "_thumb.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "1.0",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"thumbnail не извлёкся: {video_path}")
    return out


async def upload_video_retry(uploader: MediaUploader, act: str, f: Path, *, tries: int = 5) -> str:
    for attempt in range(1, tries + 1):
        try:
            return await uploader.upload_video(act, f)
        except Exception as exc:  # noqa: BLE001
            if attempt < tries and any(m in str(exc) for m in NAV_MARKERS):
                print(f"      nav-сбой {f.name} ({attempt}/{tries}), пауза+повтор…")
                await asyncio.sleep(8)
                continue
            raise


async def wait_video_ready(stub, video_id: str, *, timeout_s: int = 240) -> str:
    import time

    start, last = time.monotonic(), "?"
    while time.monotonic() - start < timeout_s:
        try:
            r = await graph_call(stub, "GET", f"/{video_id}", {"fields": "status"})
            st = (r.get("status") or {}).get("video_status") or r.get("status") or "?"
            last = st
            if st in ("ready", "error"):
                return st
        except Exception as exc:  # noqa: BLE001
            last = f"poll_err:{exc}"
        await asyncio.sleep(8)
    return f"timeout(last={last})"


# Тела объектов (campaign_body / adset_body / image_creative_body / video_creative_body /
# ad_body / url_tags_of) импортируются из core.campaign_builder — без форка.
# CLI заливает PAUSED, поэтому _CLI_STATUS зафиксирован.
_CLI_STATUS = "PAUSED"


# ====================== сборка одной кампании ======================


async def build_campaign(
    cfg: LaunchConfig, client, stub, camp: CampaignCfg, resolver: CodeResolver, go: bool
) -> dict:
    act = cfg.account.act
    cc = cfg.as_campaign_config()  # адаптер для общих body-builder'ов campaign_builder
    creo_root = Path(os.path.expanduser(cfg.creo_root))  # noqa: ASYNC240
    thumbs = creo_root / ".thumbs"
    name = cfg.render(camp.name)
    print(f"\n{'=' * 64}\nКАМПАНИЯ [{camp.key}]: {name}")
    res = {"key": camp.key, "name": name, "campaign_id": None, "adsets": []}

    # файлы
    adset_files: list[list[Path]] = []
    for a in camp.adsets:
        files = sorted((creo_root / a.dir).glob(a.glob), key=resolver.code_of)
        print(
            f"  {cfg.render(a.name)}: {len(files)} файлов"
            + ("".join(f"\n      - {f.name} [{resolver.code_of(f)}]" for f in files))
        )
        if not files:
            res["error"] = f"no_files:{a.dir}/{a.glob}"
            return res
        adset_files.append(files)

    if not go:
        print("  [spec-print] создание пропущено (нет --go)")
        return res

    uploader = MediaUploader(client, chunk_size=3 * 1024 * 1024)

    # 1) кампания
    print("\n  1) Кампания…")
    cid = ids_from(
        await run_batch(
            stub,
            [
                make_batch_entry(
                    method="POST",
                    relative_url=f"{act}/campaigns",
                    body_params=campaign_body(cc, name),
                )
            ],
            "campaign",
        )
    )[0]
    if not cid:
        res["error"] = "campaign_failed"
        return res
    res["campaign_id"] = cid
    print(f"     campaign_id={cid}")

    # 2) адсеты
    print("\n  2) Адсеты…")
    entries = [
        make_batch_entry(
            method="POST",
            relative_url=f"{act}/adsets",
            # shared adset_body не несёт campaign_id (его подставляет исполнитель) —
            # CLI создаёт кампанию первой и инжектит реальный cid.
            body_params={**adset_body(cc, cfg.render(a.name), _CLI_STATUS), "campaign_id": cid},
        )
        for a in camp.adsets
    ]
    adset_ids = ids_from(await run_batch(stub, entries, "adsets"))
    if not all(adset_ids):
        res["error"] = f"adsets_incomplete:{adset_ids}"
        return res
    print(f"     adset_ids={adset_ids}")

    # 3) per-адсет: upload → creatives → ads
    for idx, a in enumerate(camp.adsets):
        adset_id = adset_ids[idx]
        files = adset_files[idx]
        rec = {
            "name": cfg.render(a.name),
            "adset_id": adset_id,
            "ads_ok": 0,
            "ads_total": len(files),
        }
        print(f"\n  --- адсет {rec['name']} (id={adset_id}) ---")

        refs: list[dict] = []
        if camp.kind == "image":
            for f in files:
                h = await uploader.upload_image(
                    act, f.read_bytes(), filename=f.name, content_type="image/jpeg"
                )
                refs.append({"code": resolver.code_of(f), "image_hash": h})
                print(f"      {f.name} [{resolver.code_of(f)}] -> {h[:18]}…")
        else:
            for f in files:
                thumb = extract_thumbnail(f, thumbs)
                thash = await uploader.upload_image(
                    act, thumb.read_bytes(), filename=thumb.name, content_type="image/jpeg"
                )
                vid = await upload_video_retry(uploader, act, f)
                refs.append({"code": resolver.code_of(f), "video_id": vid, "thumb_hash": thash})
                print(
                    f"      {f.name} [{resolver.code_of(f)}] -> video_id={vid} thumb={thash[:14]}…"
                )
            print("    жду готовности видео…")
            for r in refs:
                st = await wait_video_ready(stub, r["video_id"])
                print(f"      video_id={r['video_id']} status={st}")

        # creatives
        if camp.kind == "image":
            entries_cr = [
                make_batch_entry(
                    method="POST",
                    relative_url=f"{act}/adcreatives",
                    body_params=image_creative_body(
                        cc, f"{r['code']}", r["image_hash"], url_tags_of(cc, r["code"])
                    ),
                )
                for r in refs
            ]
        else:
            entries_cr = [
                make_batch_entry(
                    method="POST",
                    relative_url=f"{act}/adcreatives",
                    body_params=video_creative_body(
                        cc,
                        f"{r['code']}",
                        r["video_id"],
                        r["thumb_hash"],
                        url_tags_of(cc, r["code"]),
                    ),
                )
                for r in refs
            ]
        creative_ids = ids_from(await run_batch(stub, entries_cr, f"creatives[{rec['name']}]"))
        if not all(creative_ids):
            rec["error"] = f"creatives_incomplete:{creative_ids}"
            res["adsets"].append(rec)
            continue

        # ads
        entries_ad = [
            make_batch_entry(
                method="POST",
                relative_url=f"{act}/ads",
                body_params=ad_body(refs[i]["code"], adset_id, creative_ids[i], _CLI_STATUS),
            )
            for i in range(len(refs))
        ]
        ad_ids = ids_from(await run_batch(stub, entries_ad, f"ads[{rec['name']}]"))
        rec["ads_ok"] = sum(1 for x in ad_ids if x)
        rec["ad_ids"] = ad_ids
        res["adsets"].append(rec)

    return res


# ====================== cleanup ======================


async def cleanup(stub, ids: list[str]) -> None:
    for oid in ids:
        try:
            r = await graph_call(stub, "DELETE", f"/{oid}", {})
            print(f"  {oid}: OK {r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {oid}: {exc}")


# ====================== main ======================


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--only", default=None)
    ap.add_argument("--cleanup", nargs="*", default=None)
    args = ap.parse_args()

    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))  # noqa: ASYNC240
    cfg = LaunchConfig(**raw)
    resolver = CodeResolver(cfg.creative_codes, cfg.offer_code)

    client = MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST", "localhost"), port=50051)
    await client.start()
    ch = grpc.aio.insecure_channel(
        "localhost:50051", options=[("grpc.max_receive_message_length", 50 * 1024 * 1024)]
    )
    stub = meta_api_pb2_grpc.MetaApiServiceStub(ch)
    try:
        if args.cleanup:
            print(f"CLEANUP {args.cleanup}")
            await cleanup(stub, args.cleanup)
            return 0

        try:
            h = await asyncio.wait_for(client.check_health(), timeout=25)
            print(
                f"HEALTH healthy={h['healthy']} token={h['token_present']} url={h['current_url'][:70]}"
            )
            if args.go and not h["healthy"]:
                print("Vision не готов — останов (для --go нужна живая сессия).")
                return 3
        except Exception as e:  # noqa: BLE001
            print(
                f"health: {type(e).__name__} (graph-вызовы идут по существующей сессии, продолжаю)"
            )

        keys = [args.only] if args.only else [c.key for c in cfg.campaigns]
        results = []
        for camp in cfg.campaigns:
            if camp.key in keys:
                results.append(await build_campaign(cfg, client, stub, camp, resolver, args.go))

        print("\n" + "#" * 64 + "\nИТОГ:")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        # подсказка по cleanup при partial-fail
        for r in results:
            if r.get("error") or any(
                s.get("error") or s.get("ads_ok", 0) < s.get("ads_total", 0)
                for s in r.get("adsets", [])
            ):
                if r.get("campaign_id"):
                    print(
                        f"\n⚠️ [{r['key']}] частичный/ошибка — снести: python scripts/fb_launch.py --config {args.config} --cleanup {r['campaign_id']}"
                    )
        return 0
    finally:
        await ch.close()
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
