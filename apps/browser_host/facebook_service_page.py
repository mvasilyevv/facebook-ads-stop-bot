from __future__ import annotations

import contextlib
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

_ADS_MANAGER_DEFAULT_URL = "https://adsmanager.facebook.com/adsmanager/manage/ads"
_SERVICE_QUERY_KEY = "fb_agent_service"
SCANNER_SERVICE_PAGE = "scanner"
ACTIONS_SERVICE_PAGE = "actions"
_AD_SPECIFIC_QUERY_KEYS = (
    "selected_ad_ids",
    "ad_id",
    "asset_id",
    "id",
)
_ACTION_SCOPE_RESET_KEYS = (
    "selected_campaign_ids",
    "selected_adset_ids",
)


def is_ads_manager_service_url(url: str) -> bool:
    return extract_service_page_role(url) is not None


def extract_service_page_role(url: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(_SERVICE_QUERY_KEY, [])
    for value in values:
        normalized = str(value).strip().casefold()
        if normalized:
            return normalized
    return None


def build_service_page_url(
    seed_url: str | None,
    *,
    service_role: str,
    selected_ad_id: str | None = None,
) -> str:
    parsed = urlparse(seed_url or _ADS_MANAGER_DEFAULT_URL)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "adsmanager.facebook.com"
    path = parsed.path or "/adsmanager/manage/ads"
    if "adsmanager" not in path.casefold():
        path = "/adsmanager/manage/ads"

    query = parse_qs(parsed.query, keep_blank_values=True)
    query.pop(_SERVICE_QUERY_KEY, None)
    for key in _AD_SPECIFIC_QUERY_KEYS:
        query.pop(key, None)
    if selected_ad_id is not None:
        for key in _ACTION_SCOPE_RESET_KEYS:
            query.pop(key, None)
        query["selected_ad_ids"] = [selected_ad_id]
    query[_SERVICE_QUERY_KEY] = [service_role]
    return urlunparse(
        (
            scheme,
            netloc,
            path,
            parsed.params,
            urlencode(query, doseq=True),
            "",
        )
    )


async def ensure_ads_manager_service_page(
    *,
    browser: Any | None,
    context: Any | None,
    service_role: str,
    seed_url: str | None,
    selected_ad_id: str | None = None,
) -> Any:
    target_url = build_service_page_url(
        seed_url,
        service_role=service_role,
        selected_ad_id=selected_ad_id,
    )
    target_context = _resolve_context(browser=browser, context=context)
    if target_context is None:
        raise RuntimeError("Не удалось получить browser context для служебной страницы Ads Manager")

    existing_page = await _find_service_page(
        context=target_context,
        service_role=service_role,
    )
    if existing_page is not None:
        await _goto_if_needed(existing_page, target_url)
        await _wait_for_dom_ready(existing_page)
        return existing_page

    new_page_factory = getattr(target_context, "new_page", None)
    if not callable(new_page_factory):
        raise RuntimeError(
            "Browser context не умеет создавать новую служебную страницу Ads Manager"
        )

    page = await new_page_factory()
    await _goto_if_needed(page, target_url, force=True)
    await _wait_for_dom_ready(page)
    return page


async def _find_service_page(
    *,
    context: Any | None,
    service_role: str,
) -> Any | None:
    for page in _get_pages_from_context(context):
        page_url = getattr(page, "url", "") or ""
        if extract_service_page_role(page_url) == service_role:
            return page
    return None


def _get_pages_from_context(context: Any) -> list[Any]:
    pages = getattr(context, "pages", None)
    if pages is None:
        return []
    if callable(pages):
        with contextlib.suppress(Exception):
            return list(pages())
        return []
    return list(pages)


def _resolve_context(*, browser: Any | None, context: Any | None) -> Any | None:
    if context is not None:
        return context
    if browser is None:
        return None
    contexts = list(getattr(browser, "contexts", []) or [])
    return contexts[0] if contexts else None


async def _goto_if_needed(page: Any, target_url: str, *, force: bool = False) -> None:
    current_url = getattr(page, "url", "") or ""
    if not force and _normalize_url(current_url) == _normalize_url(target_url):
        return

    goto = getattr(page, "goto", None)
    if not callable(goto):
        return
    await goto(target_url, wait_until="domcontentloaded")


async def _wait_for_dom_ready(page: Any) -> None:
    wait_for_load_state = getattr(page, "wait_for_load_state", None)
    if not callable(wait_for_load_state):
        return
    with contextlib.suppress(Exception):
        await wait_for_load_state("domcontentloaded")


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    normalized_query = urlencode(parse_qs(parsed.query, keep_blank_values=True), doseq=True)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            normalized_query,
            "",
        )
    )
