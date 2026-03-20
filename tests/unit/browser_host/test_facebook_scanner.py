from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.browser_host.facebook_scanner import (
    FacebookAdsScannerProvider,
    _coerce_delivery_status,
    _parse_decimal_value,
    _parse_int_value,
)
from apps.browser_host.playwright_attach import AttachedBrowserSession
from core.config import Settings
from core.domain import DeliveryStatus


# Проверяет, что scanner корректно нормализует приоритетный статус отклоненного объявления.
def test_coerce_delivery_status_maps_not_delivering() -> None:
    status = _coerce_delivery_status("Не показывается")

    assert status == DeliveryStatus.NOT_DELIVERING


# Проверяет, что scanner разбирает денежные значения из разных форматов интерфейса Facebook.
def test_parse_decimal_value_supports_common_ui_formats() -> None:
    assert _parse_decimal_value("$1,234.56") == Decimal("1234.56")
    assert _parse_decimal_value("1 234,56 $") == Decimal("1234.56")
    assert _parse_decimal_value("—") is None


# Проверяет, что scanner извлекает целочисленные метрики даже если Facebook отдает разделители тысяч.
def test_parse_int_value_supports_group_separators() -> None:
    assert _parse_int_value("1 234") == 1234
    assert _parse_int_value("2,345") == 2345


# Проверяет, что scanner умеет вытащить fb_ad_id из ссылок, где id приходит в query string.
def test_extract_fb_ad_id_from_href_reads_query_parameters() -> None:
    fb_ad_id = FacebookAdsScannerProvider._extract_fb_ad_id_from_href(
        "https://www.facebook.com/adsmanager/manage/ads?selected_ad_ids=1234567890123"
    )

    assert fb_ad_id == "1234567890123"


# Проверяет, что scanner всегда освобождает временную browser session, даже если в браузере нет страницы Ads Manager.
@pytest.mark.asyncio
async def test_scanner_releases_session_when_ads_page_missing() -> None:
    attached_session = AttachedBrowserSession(
        profile_id="profile-1",
        cdp_url="http://127.0.0.1:54000",
        webdriver_url=None,
        is_attached=True,
        browser=SimpleNamespace(contexts=[]),
    )

    class _FakeSessionManager:
        def __init__(self) -> None:
            self.released_profiles: list[str] = []

        async def ensure_session(self, profile_id: str) -> AttachedBrowserSession:
            return attached_session

        async def release_session(self, session: AttachedBrowserSession) -> None:
            self.released_profiles.append(session.profile_id)

    fake_manager = _FakeSessionManager()
    provider = FacebookAdsScannerProvider(
        settings=Settings(),
        browser_session_manager=fake_manager,
    )

    with pytest.raises(RuntimeError, match="нет открытых страниц"):
        await provider.scan_rows(profile_id="profile-1", browser_host_name="host-1")

    assert fake_manager.released_profiles == ["profile-1"]
