#!/usr/bin/env python3
"""PoC: Marketing API через browser-agent gRPC.

Что проверяем:
1. browser-agent gRPC доступен (порт 50051)
2. Vision-сессия активна (или поднимаем сами через --bootstrap)
3. Страница Ads Manager загружена, EAA-токен извлекается из page source
4. GET /me — базовая авторизация работает
5. GET /me/adaccounts — список кабинетов
6. GET /act_X/insights — реальные метрики (если указан --ad-account)

PoC НЕ запускает observer и НЕ парсит таблицу — Telegram алертов о колонках не будет.

Usage:
    # Если Vision-сессия уже поднята (например observer работает):
    python scripts/meta_api_poc.py

    # Если сессии нет — PoC сам поднимет браузер и перейдёт на Ads Manager:
    python scripts/meta_api_poc.py --bootstrap

    # С конкретным кабинетом для теста /insights:
    python scripts/meta_api_poc.py --bootstrap --ad-account act_XXXXXXXXX

    # После теста по умолчанию сессия НЕ закрывается (чтобы можно было перезапускать
    # PoC быстро). Если хочешь закрыть:
    python scripts/meta_api_poc.py --bootstrap --close-on-exit
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from clients.python_grpc.meta_api_client import MetaApiClient, MetaApiError

logger = logging.getLogger("meta_api_poc")

ADS_MANAGER_URL = "https://adsmanager.facebook.com/adsmanager/manage/campaigns"


def _format_currency(value: object, currency: str = "USD") -> str:
    try:
        return f"{float(value):.2f} {currency}"
    except (TypeError, ValueError):
        return f"{value} {currency}"


async def _load_vision_config() -> BrowserAgentConfig:
    """Прочитать VisionSettings из БД, расшифровать токен, собрать конфиг."""
    from sqlalchemy import select

    from core.crypto import decrypt
    from core.db import get_session_factory
    from core.models import VisionSettings

    Session = get_session_factory()
    async with Session() as db:
        row = (await db.execute(select(VisionSettings).limit(1))).scalar_one_or_none()
        if not row:
            raise RuntimeError("В БД нет VisionSettings — нечего использовать для start_browser")
        if not row.x_token_encrypted:
            raise RuntimeError("VisionSettings.x_token_encrypted пустой")

        token = decrypt(row.x_token_encrypted)
        profile_id = getattr(row, "profile_id", "") or getattr(row, "vision_profile_id", "")
        api_url = getattr(row, "api_url", "") or "http://127.0.0.1:3030"
        folder_id = getattr(row, "folder_id", None)

        return BrowserAgentConfig(
            grpc_host="localhost",
            grpc_port=50051,
            vision_x_token=token,
            vision_api_url=api_url,
            vision_profile_id=profile_id,
            vision_folder_id=folder_id or None,
        )


async def bootstrap_session(target_url: str, grpc_host: str, grpc_port: int) -> str:
    """Запустить Vision-сессию и перейти на Ads Manager, если нужно.

    Логика:
    1. Сначала проверяем health существующей сессии.
    2. Если уже healthy на странице Ads Manager — ничего не делаем.
    3. Если current_url уже Ads Manager, но токен ещё не появился — ждём polling.
    4. Если сессии нет — start_browser, потом navigate.
    5. Если сессия есть, но URL не тот — только navigate.
    """
    meta_client = MetaApiClient(grpc_host=grpc_host, grpc_port=grpc_port)
    await meta_client.start()

    try:
        # ── Шаг 1: попытаться использовать существующую сессию ─────────
        print("\n[bootstrap] Проверяю существующую Vision-сессию...")
        initial_health = await meta_client.check_health()
        print(f"            detail: {initial_health.detail}")
        print(f"            url:    {initial_health.current_url or '(пусто)'}")

        if initial_health.healthy and initial_health.token_present:
            print(f"            [OK] Сессия уже готова! token_length={initial_health.token_length}")
            print("            Пропускаем start_browser + navigate.")
            return "(существующая сессия)"

        existing_url = initial_health.current_url or ""
        already_on_ads_manager = (
            "adsmanager.facebook.com" in existing_url
            or "facebook.com/adsmanager" in existing_url
            or "business.facebook.com" in existing_url
        )
        session_exists = "session" not in initial_health.detail.lower() or already_on_ads_manager

        # ── Шаг 2: при необходимости поднять Vision-сессию ─────────────
        if not session_exists:
            print("\n[bootstrap] Сессии нет, поднимаю через StartBrowser...")
            config = await _load_vision_config()
            config.grpc_host = grpc_host
            config.grpc_port = grpc_port
            print(f"            profile_id:  {config.vision_profile_id}")
            print(f"            token (JWT): ...{config.vision_x_token.split('.')[-1][-10:]}")

            browser_client = BrowserAgentClient(config)
            await browser_client.start()
            t0 = perf_counter()
            session_id = await browser_client.start_browser()
            elapsed = (perf_counter() - t0) * 1000
            print(f"            session_id: {session_id}  cdp_port: {browser_client._cdp_port}")
            print(f"            время:      {elapsed:.0f} мс")
            await browser_client.close()
            session_exists = True

        # ── Шаг 3: при необходимости перейти на Ads Manager ────────────
        if not already_on_ads_manager:
            print(f"\n[bootstrap] Перехожу на {target_url} (wait_until=commit)...")
            config = await _load_vision_config()
            config.grpc_host = grpc_host
            config.grpc_port = grpc_port
            browser_client = BrowserAgentClient(config)
            await browser_client.start()
            try:
                t0 = perf_counter()
                # short timeout — если navigate висит, выйдем по asyncio.wait_for
                await asyncio.wait_for(
                    browser_client.navigate(target_url, wait_until="commit"),
                    timeout=20.0,
                )
                elapsed = (perf_counter() - t0) * 1000
                print(f"            время navigate: {elapsed:.0f} мс")
            except asyncio.TimeoutError:
                print("            navigate не вернулся за 20с — продолжаем с polling")
            except Exception as exc:
                print(f"            navigate упал: {exc} — продолжаем с polling")
            finally:
                await browser_client.close()
        else:
            print("\n[bootstrap] Страница уже на Ads Manager, navigate не нужен.")

        # ── Шаг 4: polling токена ───────────────────────────────────────
        print("[bootstrap] Polling access_token (каждые 2с, максимум 60с)...")
        t_start = perf_counter()
        deadline = t_start + 60.0
        attempt = 0
        last_detail = ""
        while perf_counter() < deadline:
            attempt += 1
            health = await meta_client.check_health()
            if health.healthy and health.token_present:
                elapsed = perf_counter() - t_start
                print(
                    f"            [{attempt}] token найден ({health.token_length} chars), "
                    f"polling занял {elapsed:.1f}с"
                )
                print(f"            current_url: {health.current_url[:120]}")
                return "(сессия активна)"
            if health.detail != last_detail:
                print(
                    f"            [{attempt}] detail={health.detail}, url={health.current_url[:80]}"
                )
                last_detail = health.detail
            await asyncio.sleep(2)

        print(f"            [TIMEOUT] За 60с токен не появился. Последний detail: {last_detail}")
        return "(timeout)"
    finally:
        await meta_client.close()


async def run_poc_tests(
    grpc_host: str,
    grpc_port: int,
    ad_account_id: str | None,
) -> int:
    """Возвращает 0 при успехе, 1 при ошибке."""
    client = MetaApiClient(grpc_host=grpc_host, grpc_port=grpc_port)
    try:
        await client.start()
    except Exception as exc:
        print(f"\n[FAIL] Не удалось подключиться к browser-agent: {exc}")
        return 1

    try:
        # ── Шаг 1: Health-check ──────────────────────────────────────────
        print("\n[1/4] Проверка состояния Marketing API канала...")
        t0 = perf_counter()
        try:
            health = await client.check_health()
        except Exception as exc:
            print(f"      [FAIL] Health-check провалился: {exc}")
            return 1
        elapsed_ms = (perf_counter() - t0) * 1000

        print(f"      healthy:        {health.healthy}")
        print(f"      current_url:    {health.current_url or '(пусто)'}")
        print(f"      token_present:  {health.token_present}")
        print(
            f"      token_length:   {health.token_length if health.token_length else 'не найден'}"
        )
        print(f"      detail:         {health.detail}")
        print(f"      latency:        {elapsed_ms:.0f} мс")

        if not health.healthy:
            print(f"\n[FAIL] Marketing API недоступен: {health.detail}")
            print("       Используй --bootstrap чтобы PoC сам поднял сессию.")
            return 1

        # ── Шаг 2: GET /me ───────────────────────────────────────────────
        print("\n[2/4] GET /me — проверка авторизации...")
        t0 = perf_counter()
        try:
            me = await client.get_me()
        except MetaApiError as exc:
            print(f"      [FAIL] code={exc.code} subcode={exc.subcode} type={exc.type}")
            print(f"             message: {exc}")
            print(f"             fbtrace_id: {exc.fbtrace_id}")
            if exc.is_token_invalidated:
                print("       Токен инвалидирован — refresh страницы в Vision-сессии")
            return 1
        elapsed_ms = (perf_counter() - t0) * 1000

        print(f"      user_id:        {me.get('id', '?')}")
        print(f"      name:           {me.get('name', '?')}")
        print(f"      latency:        {elapsed_ms:.0f} мс")

        # ── Шаг 3: GET /me/adaccounts ────────────────────────────────────
        print("\n[3/4] GET /me/adaccounts — список рекламных кабинетов...")
        t0 = perf_counter()
        try:
            accounts = await client.list_ad_accounts()
        except MetaApiError as exc:
            print(f"      [FAIL] code={exc.code}: {exc}")
            return 1
        elapsed_ms = (perf_counter() - t0) * 1000

        print(f"      Найдено кабинетов: {len(accounts)}")
        print(f"      latency:        {elapsed_ms:.0f} мс")
        for idx, acc in enumerate(accounts[:5], start=1):
            acc_id = acc.get("id", "?")
            currency = acc.get("currency", "?")
            timezone = acc.get("timezone_name", "?")
            status = acc.get("account_status", "?")
            print(f"      [{idx}] {acc_id} ({currency}, {timezone}, status={status})")
        if len(accounts) > 5:
            print(f"      ...и ещё {len(accounts) - 5}")

        # ── Шаг 4: GET /act_X/insights ───────────────────────────────────
        if not ad_account_id:
            if accounts:
                ad_account_id = accounts[0].get("id", "")
                print(f"\n[4/4] --ad-account не указан, используем {ad_account_id}")
            else:
                print("\n[4/4] Пропускаем insights — нет доступных кабинетов")
                print("\n[OK] Базовая проверка пройдена. Marketing API канал работает!")
                return 0

        print(f"\n[4/4] GET /{ad_account_id}/insights за сегодня...")
        t0 = perf_counter()
        try:
            insights = await client.get_insights(
                ad_account_id,
                level="ad",
                date_preset="today",
                limit=20,
            )
        except MetaApiError as exc:
            print(f"      [FAIL] code={exc.code} subcode={exc.subcode}: {exc}")
            print(f"             fbtrace_id: {exc.fbtrace_id}")
            return 1
        elapsed_ms = (perf_counter() - t0) * 1000

        print(f"      Получено объявлений: {len(insights)}")
        print(f"      latency:        {elapsed_ms:.0f} мс")

        if not insights:
            print("      Объявлений в today нет (это нормально если кабинет пуст или все на pause)")
        else:
            print("      Топ-3 по spend:")
            sorted_ads = sorted(
                insights,
                key=lambda x: float(x.get("spend") or 0),
                reverse=True,
            )
            for idx, ad in enumerate(sorted_ads[:3], start=1):
                ad_id = ad.get("ad_id", "?")
                ad_name = ad.get("ad_name", "?")
                spend = _format_currency(ad.get("spend"), "USD")
                impressions = ad.get("impressions", "?")
                clicks = ad.get("clicks", "?")
                ctr = ad.get("ctr", "?")
                cpc = _format_currency(ad.get("cpc"), "USD")
                ad_name_short = ad_name[:50] + "..." if len(ad_name) > 50 else ad_name
                print(f"      [{idx}] {ad_id} | {ad_name_short}")
                print(
                    f"           spend={spend}  impressions={impressions}  "
                    f"clicks={clicks}  CTR={ctr}%  CPC={cpc}"
                )

        print("\n" + "=" * 70)
        print("  [OK] Все проверки пройдены. Marketing API канал работает!")
        print("=" * 70)
        return 0

    finally:
        await client.close()


async def run_poc(args: argparse.Namespace) -> int:
    print("=" * 70)
    print(f"  PoC Marketing API через browser-agent gRPC ({args.grpc_host}:{args.grpc_port})")
    print("=" * 70)

    if args.bootstrap:
        try:
            await bootstrap_session(args.target_url, args.grpc_host, args.grpc_port)
        except Exception as exc:
            print(f"\n[FAIL] Bootstrap провалился: {exc}")
            print("       Возможно Vision X-Token истёк (401), либо browser-agent не запущен.")
            return 1

    result = await run_poc_tests(args.grpc_host, args.grpc_port, args.ad_account)

    if args.close_on_exit and args.bootstrap:
        print("\n[cleanup] Закрываю Vision-сессию (--close-on-exit)...")
        try:
            config = await _load_vision_config()
            client = BrowserAgentClient(config)
            await client.start()
            # Подцепим сессию через preferred lookup (она у browser-agent in-memory)
            from clients.python_grpc.v1 import browser_session_pb2

            info = await client._browser_stub.GetSessionInfo(
                browser_session_pb2.GetSessionInfoRequest(session_id=""),
                timeout=5.0,
            )
            client._session_id = info.session_id
            await client.disconnect_browser()
            await client.close()
            print("           Готово.")
        except Exception as exc:
            print(f"           Не удалось закрыть: {exc}")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--grpc-host", default="localhost")
    parser.add_argument("--grpc-port", type=int, default=50051)
    parser.add_argument(
        "--ad-account",
        default=None,
        help="ID рекламного кабинета (act_XXX или просто число). По умолчанию — первый из /adaccounts",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Поднять Vision-сессию из PoC (start_browser + navigate). По умолчанию: использует существующую сессию.",
    )
    parser.add_argument(
        "--target-url",
        default=ADS_MANAGER_URL,
        help=f"URL для navigate. По умолчанию: {ADS_MANAGER_URL}",
    )
    parser.add_argument(
        "--close-on-exit",
        action="store_true",
        help="Закрыть Vision-сессию после тестов (по умолчанию НЕ закрывает, чтобы можно было перезапускать)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.ad_account and not args.ad_account.startswith("act_"):
        args.ad_account = f"act_{args.ad_account}"

    try:
        return asyncio.run(run_poc(args))
    except KeyboardInterrupt:
        print("\n[interrupted]")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
