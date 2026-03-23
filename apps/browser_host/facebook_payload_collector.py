from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

from core.domain import (
    DeliveryStatus,
    ScopePresence,
    TrackingMode,
    extract_offer_code_from_ad_name,
)
from core.scanner import (
    ScannedAdRow,
    build_adset_scope_key,
    build_campaign_scope_key,
    normalize_delivery_status,
    parse_scanner_decimal,
)

_FB_AD_ID_PATTERN = re.compile(r"\b\d{8,20}\b")
_NON_ALNUM_PATTERN = re.compile(r"[^a-zа-я0-9]+", re.IGNORECASE)
_PLACEHOLDER_CAMPAIGN_NAME_PATTERN = re.compile(
    r"^(?:кампания|campaign)\s+\d{8,20}$", re.IGNORECASE
)
_PLACEHOLDER_CAMPAIGN_BY_AD_PATTERN = re.compile(
    r"^(?:кампания объявления|campaign ad)\s+\d{8,20}$",
    re.IGNORECASE,
)
_PLACEHOLDER_ADSET_NAME_PATTERN = re.compile(r"^(?:адсет|adset)\s+\d{8,20}$", re.IGNORECASE)
_PLACEHOLDER_AD_NAME_PATTERN = re.compile(r"^(?:объявление|ad)\s+\d{8,20}$", re.IGNORECASE)
_GRAPHQL_NODE_TYPES = {"Adgroup", "AdCampaign", "AdCampaignGroup"}


@dataclass(slots=True)
class _CapturedResponseRow:
    fb_ad_id: str
    adset_id: str | None = None
    campaign_id: str | None = None
    campaign_name: str | None = None
    adset_name: str | None = None
    ad_name: str | None = None
    delivery_status: str | None = None
    spend: str | None = None
    clicks: str | None = None
    cpc: str | None = None
    leads: str | None = None
    cost_per_lead: str | None = None
    registrations: str | None = None
    cost_per_registration: str | None = None
    deposits: str | None = None

    def merge(self, mapped_values: dict[str, str]) -> None:
        self.campaign_name = self.campaign_name or _extract_mapped_value(
            mapped_values,
            "campaign_name",
        )
        self.adset_name = self.adset_name or _extract_mapped_value(mapped_values, "adset_name")
        self.ad_name = self.ad_name or _extract_mapped_value(mapped_values, "ad_name")
        self.delivery_status = self.delivery_status or _extract_mapped_value(
            mapped_values,
            "delivery_status",
        )
        self.spend = self.spend or _extract_mapped_value(mapped_values, "spend")
        self.clicks = self.clicks or _extract_mapped_value(mapped_values, "clicks")
        self.cpc = self.cpc or _extract_mapped_value(mapped_values, "cpc")
        self.leads = self.leads or _extract_mapped_value(mapped_values, "leads")
        self.cost_per_lead = self.cost_per_lead or _extract_mapped_value(
            mapped_values,
            "cost_per_lead",
        )
        self.registrations = self.registrations or _extract_mapped_value(
            mapped_values,
            "registrations",
        )
        self.cost_per_registration = self.cost_per_registration or _extract_mapped_value(
            mapped_values,
            "cost_per_registration",
        )
        self.deposits = self.deposits or _extract_mapped_value(mapped_values, "deposits")


def parse_ads_manager_payloads(
    *,
    relevant_payloads: list[Any],
    all_payloads: list[Any],
    page_url: str,
) -> list[ScannedAdRow]:
    return merge_scanned_rows(
        parse_response_rows(relevant_payloads, page_url),
        parse_graphql_rows(all_payloads, page_url),
    )


def parse_response_rows(payloads: list[Any], page_url: str) -> list[ScannedAdRow]:
    scope_context = _extract_scope_context(page_url)
    rows_by_id: dict[str, _CapturedResponseRow] = {}
    adset_names_by_id, campaign_names_by_id, campaign_id_by_adset_id = (
        _collect_graphql_scope_references(payloads)
    )

    for payload in payloads:
        for candidate in _iter_response_row_candidates(payload):
            candidate_typename = _coerce_response_text(candidate.get("__typename"))
            if candidate_typename in {"AdCampaign", "AdCampaignGroup"}:
                continue
            mapped_values = _normalize_response_row(candidate)
            fb_ad_id = _extract_response_fb_ad_id(mapped_values)
            if fb_ad_id is None:
                continue

            row = rows_by_id.setdefault(fb_ad_id, _CapturedResponseRow(fb_ad_id=fb_ad_id))
            row.merge(mapped_values)
            row.adset_id = row.adset_id or _extract_mapped_identifier(
                mapped_values,
                "adset_id",
                "ad set id",
                "ad_campaign_id",
                "ad campaign id",
            )
            row.campaign_id = row.campaign_id or _extract_mapped_identifier(
                mapped_values,
                "campaign_id",
                "campaign group id",
                "campaign_group_id",
                "ad_campaign_group_id",
                "ad campaign group id",
            )
            _apply_scope_references(
                row,
                adset_names_by_id=adset_names_by_id,
                campaign_names_by_id=campaign_names_by_id,
                campaign_id_by_adset_id=campaign_id_by_adset_id,
            )

    for row in rows_by_id.values():
        _apply_scope_references(
            row,
            adset_names_by_id=adset_names_by_id,
            campaign_names_by_id=campaign_names_by_id,
            campaign_id_by_adset_id=campaign_id_by_adset_id,
        )

    return [_build_row_from_response_data(row, scope_context) for row in rows_by_id.values()]


def parse_graphql_rows(payloads: list[Any], page_url: str) -> list[ScannedAdRow]:
    scope_context = _extract_scope_context(page_url)
    rows_by_id: dict[str, _CapturedResponseRow] = {}
    adset_names_by_id, campaign_names_by_id, campaign_id_by_adset_id = (
        _collect_graphql_scope_references(payloads)
    )

    for payload in payloads:
        for node in _iter_graphql_nodes(payload):
            typename = _coerce_response_text(node.get("__typename"))
            node_id = _extract_graphql_identifier(node.get("id") or node.get("node_id"))

            if typename != "Adgroup" or node_id is None:
                continue

            row = rows_by_id.setdefault(node_id, _CapturedResponseRow(fb_ad_id=node_id))
            row.merge(_normalize_response_row(node))
            row.adset_id = row.adset_id or _extract_graphql_identifier(node.get("ad_campaign_id"))
            row.campaign_id = row.campaign_id or _extract_graphql_identifier(
                node.get("ad_campaign_group_id")
            )
            direct_campaign_name = _coerce_response_text(node.get("ad_campaign_group_name"))
            if _is_scope_name_usable("campaign_name", direct_campaign_name):
                row.campaign_name = row.campaign_name or direct_campaign_name

    for row in rows_by_id.values():
        _apply_scope_references(
            row,
            adset_names_by_id=adset_names_by_id,
            campaign_names_by_id=campaign_names_by_id,
            campaign_id_by_adset_id=campaign_id_by_adset_id,
        )

    return [_build_row_from_response_data(row, scope_context) for row in rows_by_id.values()]


def merge_scanned_rows(*row_groups: list[ScannedAdRow]) -> list[ScannedAdRow]:
    rows_by_id: dict[str, ScannedAdRow] = {}
    for row_group in row_groups:
        for row in row_group:
            current_row = rows_by_id.get(row.fb_ad_id)
            if current_row is None:
                rows_by_id[row.fb_ad_id] = row
                continue
            rows_by_id[row.fb_ad_id] = _merge_scanned_row_pair(current_row, row)
    return list(rows_by_id.values())


def has_unresolved_scope_rows(rows: list[ScannedAdRow]) -> bool:
    for row in rows:
        if _is_placeholder_scope_name("campaign_name", row.campaign_name):
            return True
        if _is_placeholder_scope_name("adset_name", row.adset_name):
            return True
        if _is_placeholder_ad_name(row.ad_name):
            return True
    return False


def _normalize_header_text(value: str) -> str:
    normalized = value.casefold().strip()
    normalized = _NON_ALNUM_PATTERN.sub(" ", normalized)
    return " ".join(part for part in normalized.split() if part)


def _normalize_status_text(value: str) -> str:
    return _normalize_header_text(value)


def _coerce_delivery_status(raw_value: str) -> DeliveryStatus:
    return normalize_delivery_status(_normalize_status_text(raw_value))


def _parse_decimal_value(raw_value: str) -> Decimal | None:
    return parse_scanner_decimal(raw_value)


def _parse_int_value(raw_value: str) -> int:
    value = _parse_decimal_value(raw_value)
    if value is None:
        return 0
    return int(value)


def _extract_numeric_identifier(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    match = _FB_AD_ID_PATTERN.search(raw_value)
    return match.group(0) if match is not None else None


def _extract_mapped_value(mapped_values: dict[str, str], field_name: str) -> str | None:
    return mapped_values.get(_normalize_header_text(field_name))


def _extract_mapped_identifier(mapped_values: dict[str, str], *field_names: str) -> str | None:
    for field_name in field_names:
        identifier = _extract_numeric_identifier(
            mapped_values.get(_normalize_header_text(field_name))
        )
        if identifier is not None:
            return identifier
    return None


def _is_scope_name_usable(field_name: str, value: str | None) -> bool:
    if value is None:
        return False
    normalized = _normalize_header_text(value)
    if not normalized or normalized == "unknown":
        return False
    if field_name == "adset_name" and (
        "активные объявления" in normalized or "active ads" in normalized
    ):
        return False
    return True


def _is_placeholder_scope_name(field_name: str, value: str | None) -> bool:
    if value is None:
        return False
    normalized_value = value.strip()
    if field_name == "campaign_name":
        return bool(
            _PLACEHOLDER_CAMPAIGN_NAME_PATTERN.fullmatch(normalized_value)
            or _PLACEHOLDER_CAMPAIGN_BY_AD_PATTERN.fullmatch(normalized_value)
        )
    if field_name == "adset_name":
        return bool(_PLACEHOLDER_ADSET_NAME_PATTERN.fullmatch(normalized_value))
    return False


def _is_placeholder_ad_name(value: str | None) -> bool:
    if value is None:
        return False
    return bool(_PLACEHOLDER_AD_NAME_PATTERN.fullmatch(value.strip()))


def _collect_graphql_scope_references(
    payloads: list[Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    adset_names_by_id: dict[str, str] = {}
    campaign_names_by_id: dict[str, str] = {}
    campaign_id_by_adset_id: dict[str, str] = {}

    for payload in payloads:
        for node in _iter_graphql_nodes(payload):
            typename = _coerce_response_text(node.get("__typename"))
            node_id = _extract_graphql_identifier(node.get("id") or node.get("node_id"))
            if typename == "AdCampaign":
                if node_id is None:
                    continue
                adset_name = _coerce_response_text(node.get("name"))
                if _is_scope_name_usable("adset_name", adset_name):
                    adset_names_by_id[node_id] = adset_name
                campaign_id = _extract_graphql_identifier(node.get("ad_campaign_group_id"))
                if campaign_id is not None:
                    campaign_id_by_adset_id[node_id] = campaign_id
                campaign_name = _coerce_response_text(node.get("ad_campaign_group_name"))
                if campaign_id is not None and _is_scope_name_usable(
                    "campaign_name",
                    campaign_name,
                ):
                    campaign_names_by_id[campaign_id] = campaign_name
                continue

            if typename != "AdCampaignGroup" or node_id is None:
                continue

            campaign_name = _coerce_response_text(node.get("name"))
            if _is_scope_name_usable("campaign_name", campaign_name):
                campaign_names_by_id[node_id] = campaign_name

    return adset_names_by_id, campaign_names_by_id, campaign_id_by_adset_id


def _apply_scope_references(
    row: _CapturedResponseRow,
    *,
    adset_names_by_id: dict[str, str],
    campaign_names_by_id: dict[str, str],
    campaign_id_by_adset_id: dict[str, str],
) -> None:
    if (
        not _is_scope_name_usable("adset_name", row.adset_name)
        and row.adset_id is not None
        and row.adset_id in adset_names_by_id
    ):
        row.adset_name = adset_names_by_id[row.adset_id]

    campaign_id = row.campaign_id or (
        campaign_id_by_adset_id.get(row.adset_id) if row.adset_id is not None else None
    )
    if campaign_id is not None:
        row.campaign_id = row.campaign_id or campaign_id

    if (
        not _is_scope_name_usable("campaign_name", row.campaign_name)
        and campaign_id is not None
        and campaign_id in campaign_names_by_id
    ):
        row.campaign_name = campaign_names_by_id[campaign_id]


def _iter_graphql_nodes(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        typename = payload.get("__typename")
        if isinstance(typename, str) and typename in _GRAPHQL_NODE_TYPES:
            yield payload
        for value in payload.values():
            yield from _iter_graphql_nodes(value)
        return

    if isinstance(payload, list):
        for item in payload:
            yield from _iter_graphql_nodes(item)


def _extract_graphql_identifier(value: Any) -> str | None:
    if isinstance(value, (str, int)):
        return _extract_numeric_identifier(str(value))
    return None


def _build_row_from_response_data(
    row: _CapturedResponseRow,
    scope_context: dict[str, str | None],
) -> ScannedAdRow:
    campaign_name = (
        (row.campaign_name if _is_scope_name_usable("campaign_name", row.campaign_name) else None)
        or (f"Кампания {row.campaign_id}" if row.campaign_id is not None else None)
        or (
            scope_context.get("campaign_name")
            if _is_scope_name_usable("campaign_name", scope_context.get("campaign_name"))
            else None
        )
        or f"Кампания объявления {row.fb_ad_id}"
    )
    adset_name = (
        (row.adset_name if _is_scope_name_usable("adset_name", row.adset_name) else None)
        or (f"Адсет {row.adset_id}" if row.adset_id is not None else None)
        or (
            scope_context.get("adset_name")
            if _is_scope_name_usable("adset_name", scope_context.get("adset_name"))
            else None
        )
        or ""
    )
    ad_name = row.ad_name or f"Объявление {row.fb_ad_id}"
    delivery_status = _coerce_delivery_status(row.delivery_status or "unknown")
    campaign_scope_key = build_campaign_scope_key(campaign_name)
    return ScannedAdRow(
        fb_ad_id=row.fb_ad_id,
        campaign_scope_key=campaign_scope_key,
        adset_scope_key=build_adset_scope_key(adset_name or row.fb_ad_id, campaign_scope_key),
        campaign_name=campaign_name,
        adset_name=adset_name,
        ad_name=ad_name,
        delivery_status=delivery_status,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=_parse_decimal_value(row.spend or "") or Decimal("0"),
        clicks=_parse_int_value(row.clicks or ""),
        cpc=_parse_decimal_value(row.cpc or ""),
        leads=_parse_int_value(row.leads or ""),
        cost_per_lead=_parse_decimal_value(row.cost_per_lead or ""),
        registrations=_parse_int_value(row.registrations or ""),
        cost_per_registration=_parse_decimal_value(row.cost_per_registration or ""),
        deposits=_parse_int_value(row.deposits or ""),
        last_seen_at=datetime.now(tz=UTC),
        resolved_offer_code=extract_offer_code_from_ad_name(ad_name),
    )


def _iter_response_row_candidates(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("rows"), list):
            for row in payload["rows"]:
                if isinstance(row, dict):
                    yield row
            return

        if isinstance(payload.get("nodes"), list):
            for item in payload["nodes"]:
                yield from _iter_response_row_candidates(item)
            return

        data = payload.get("data")
        if isinstance(data, dict):
            yield from _iter_response_row_candidates(data)
            return
        if isinstance(data, list):
            for item in data:
                yield from _iter_response_row_candidates(item)
            return

        if (
            any(
                key in payload
                for key in (
                    "dimension_values",
                    "atomic_values",
                    "ad_id",
                    "id",
                    "name",
                    "spend",
                    "clicks",
                    "cpc",
                )
            )
            or payload.get("__typename") in _GRAPHQL_NODE_TYPES
        ):
            yield payload
            return

    if isinstance(payload, list):
        for item in payload:
            yield from _iter_response_row_candidates(item)


def _normalize_response_row(row: dict[str, Any]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    typename = _coerce_response_text(row.get("__typename"))
    headers = row.get("headers")
    if isinstance(headers, list):
        headers = [_extract_response_header_name(item) for item in headers]
    else:
        headers = None

    direct_keys = {
        "ad_id",
        "fb_ad_id",
        "id",
        "campaign_name",
        "adset_name",
        "ad_name",
        "delivery_status",
        "delivery",
        "status",
        "spend",
        "clicks",
        "cpc",
        "leads",
        "cost_per_lead",
        "registrations",
        "cost_per_registration",
        "deposits",
    }

    for key, value in row.items():
        if key in {"headers", "rows"}:
            continue
        normalized_key = _normalize_header_text(str(key))
        if key in {"dimension_values", "atomic_values", "values"}:
            mapped.update(_flatten_response_container(value, headers=headers))
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            mapped[normalized_key] = _coerce_response_text(value)
            continue
        mapped.update(_flatten_response_container(value, headers=headers))

    for key, value in row.items():
        if key in direct_keys:
            normalized_key = _normalize_header_text(str(key))
            if isinstance(value, (str, int, float, bool)) or value is None:
                mapped[normalized_key] = _coerce_response_text(value)

    if typename == "Adgroup":
        ad_name = row.get("name")
        if isinstance(ad_name, (str, int, float, bool)) or ad_name is None:
            mapped[_normalize_header_text("ad name")] = _coerce_response_text(ad_name)
        adset_name = row.get("ad_campaign_name")
        if isinstance(adset_name, (str, int, float, bool)) or adset_name is None:
            mapped[_normalize_header_text("adset name")] = _coerce_response_text(adset_name)
        delivery_status = _extract_graphql_delivery_status_text(row.get("delivery_status"))
        if delivery_status:
            mapped[_normalize_header_text("delivery status")] = delivery_status

    if typename == "AdCampaign":
        adset_name = row.get("name")
        if isinstance(adset_name, (str, int, float, bool)) or adset_name is None:
            mapped[_normalize_header_text("adset name")] = _coerce_response_text(adset_name)

    if typename == "AdCampaignGroup":
        campaign_name = row.get("name")
        if isinstance(campaign_name, (str, int, float, bool)) or campaign_name is None:
            mapped[_normalize_header_text("campaign name")] = _coerce_response_text(campaign_name)

    return mapped


def _extract_response_header_name(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("name", "field", "column", "header", "metric", "dimension", "key"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return str(item or "")


def _extract_graphql_delivery_status_text(value: Any) -> str | None:
    if isinstance(value, dict):
        substatuses = value.get("substatuses")
        if isinstance(substatuses, list):
            for item in substatuses:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        status = value.get("status")
        if isinstance(status, str) and status.strip():
            return status.strip()
    return None


def _flatten_response_container(
    value: Any,
    *,
    headers: list[str] | None = None,
) -> dict[str, str]:
    flattened: dict[str, str] = {}

    if isinstance(value, dict):
        pair_key = _extract_pair_key(value)
        if (
            pair_key is not None
            and "value" in value
            and not isinstance(value["value"], (dict, list))
        ):
            flattened[_normalize_header_text(pair_key)] = _coerce_response_text(value["value"])
            return flattened

        for key, item in value.items():
            if isinstance(item, (str, int, float, bool)) or item is None:
                flattened[_normalize_header_text(str(key))] = _coerce_response_text(item)
            else:
                flattened.update(_flatten_response_container(item, headers=headers))
        return flattened

    if isinstance(value, list):
        if headers is not None and len(headers) == len(value):
            for header, item in zip(headers, value, strict=False):
                if isinstance(item, (str, int, float, bool)) or item is None:
                    flattened[_normalize_header_text(header)] = _coerce_response_text(item)
                else:
                    flattened.update(_flatten_response_container(item, headers=headers))
            return flattened

        for item in value:
            if isinstance(item, dict):
                flattened.update(_flatten_response_container(item, headers=headers))
            elif isinstance(item, (str, int, float, bool)) or item is None:
                flattened[str(len(flattened))] = _coerce_response_text(item)
        return flattened

    return flattened


def _extract_pair_key(value: dict[str, Any]) -> str | None:
    for key_name in ("name", "field", "column", "header", "metric", "dimension", "key"):
        key_value = value.get(key_name)
        if isinstance(key_value, str) and key_value.strip():
            return key_value
    return None


def _coerce_response_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _extract_response_fb_ad_id(mapped_values: dict[str, str]) -> str | None:
    candidates = (
        mapped_values.get(_normalize_header_text("fb_ad_id")),
        mapped_values.get(_normalize_header_text("ad_id")),
        mapped_values.get(_normalize_header_text("id")),
    )
    for candidate in candidates:
        direct_identifier = _extract_numeric_identifier(candidate)
        if direct_identifier is not None:
            return direct_identifier
    return None


def _extract_scope_context(page_url: str) -> dict[str, str | None]:
    campaign_id = _extract_query_identifier(page_url, "selected_campaign_ids")
    adset_id = _extract_query_identifier(page_url, "selected_adset_ids", "selected_ad_set_ids")
    return {
        "campaign_name": f"Кампания {campaign_id}" if campaign_id is not None else None,
        "adset_name": f"Адсет {adset_id}" if adset_id is not None else None,
    }


def _extract_query_identifier(page_url: str, *keys: str) -> str | None:
    query = parse_qs(urlparse(page_url).query)
    identifiers: list[str] = []
    for key in keys:
        values = query.get(key)
        if not values:
            continue
        for value in values:
            identifiers.extend(_FB_AD_ID_PATTERN.findall(value))
    unique_identifiers = tuple(dict.fromkeys(identifiers))
    if len(unique_identifiers) == 1:
        return unique_identifiers[0]
    return None


def _merge_scanned_row_pair(current_row: ScannedAdRow, candidate_row: ScannedAdRow) -> ScannedAdRow:
    campaign_name = _prefer_scope_name(
        "campaign_name",
        current_row.campaign_name,
        candidate_row.campaign_name,
    )
    adset_name = _prefer_scope_name(
        "adset_name",
        current_row.adset_name,
        candidate_row.adset_name,
    )
    ad_name = _prefer_ad_name(current_row.ad_name, candidate_row.ad_name)
    campaign_scope_key = build_campaign_scope_key(campaign_name)
    return ScannedAdRow(
        fb_ad_id=current_row.fb_ad_id,
        campaign_scope_key=campaign_scope_key,
        adset_scope_key=build_adset_scope_key(
            adset_name or current_row.fb_ad_id, campaign_scope_key
        ),
        campaign_name=campaign_name,
        adset_name=adset_name,
        ad_name=ad_name,
        delivery_status=_prefer_delivery_status(
            current_row.delivery_status,
            candidate_row.delivery_status,
        ),
        tracking_mode=candidate_row.tracking_mode,
        scope_presence=candidate_row.scope_presence,
        spend=_prefer_decimal_metric(current_row.spend, candidate_row.spend),
        clicks=_prefer_int_metric(current_row.clicks, candidate_row.clicks),
        cpc=_prefer_optional_decimal_metric(current_row.cpc, candidate_row.cpc),
        leads=_prefer_int_metric(current_row.leads, candidate_row.leads),
        cost_per_lead=_prefer_optional_decimal_metric(
            current_row.cost_per_lead,
            candidate_row.cost_per_lead,
        ),
        registrations=_prefer_int_metric(current_row.registrations, candidate_row.registrations),
        cost_per_registration=_prefer_optional_decimal_metric(
            current_row.cost_per_registration,
            candidate_row.cost_per_registration,
        ),
        deposits=_prefer_int_metric(current_row.deposits, candidate_row.deposits),
        last_seen_at=_prefer_last_seen_at(current_row.last_seen_at, candidate_row.last_seen_at),
        account_name=candidate_row.account_name or current_row.account_name,
        resolved_offer_id=candidate_row.resolved_offer_id or current_row.resolved_offer_id,
        resolved_offer_code=candidate_row.resolved_offer_code or current_row.resolved_offer_code,
    )


def _prefer_scope_name(field_name: str, current_value: str, candidate_value: str) -> str:
    if not _is_scope_name_usable(field_name, candidate_value):
        return current_value
    if not _is_scope_name_usable(field_name, current_value):
        return candidate_value
    if _is_placeholder_scope_name(field_name, candidate_value) and not _is_placeholder_scope_name(
        field_name,
        current_value,
    ):
        return current_value
    return candidate_value


def _prefer_ad_name(current_value: str, candidate_value: str) -> str:
    if not candidate_value.strip():
        return current_value
    if not current_value.strip():
        return candidate_value
    if _is_placeholder_ad_name(candidate_value) and not _is_placeholder_ad_name(current_value):
        return current_value
    return candidate_value


def _prefer_delivery_status(
    current_value: DeliveryStatus,
    candidate_value: DeliveryStatus,
) -> DeliveryStatus:
    if candidate_value == DeliveryStatus.UNKNOWN and current_value != DeliveryStatus.UNKNOWN:
        return current_value
    return candidate_value


def _prefer_decimal_metric(current_value: Decimal, candidate_value: Decimal) -> Decimal:
    if not candidate_value.is_zero() or current_value.is_zero():
        return candidate_value
    return current_value


def _prefer_optional_decimal_metric(
    current_value: Decimal | None,
    candidate_value: Decimal | None,
) -> Decimal | None:
    if candidate_value is None:
        return current_value
    if current_value is None or not candidate_value.is_zero() or current_value.is_zero():
        return candidate_value
    return current_value


def _prefer_int_metric(current_value: int, candidate_value: int) -> int:
    if candidate_value != 0 or current_value == 0:
        return candidate_value
    return current_value


def _prefer_last_seen_at(
    current_value: datetime | None,
    candidate_value: datetime | None,
) -> datetime | None:
    if current_value is None:
        return candidate_value
    if candidate_value is None:
        return current_value
    if candidate_value >= current_value:
        return candidate_value
    return current_value
