from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

from apps.browser_host.facebook_service_page import (
    ACTIONS_SERVICE_PAGE,
    build_service_page_url,
    ensure_ads_manager_service_page,
)


@dataclass(slots=True)
class _FakePage:
    """Заглушка страницы служебного контекста Ads Manager."""

    url: str
    goto_urls: list[str] = field(default_factory=list)
    context: Any | None = None

    async def goto(self, url: str, wait_until: str | None = None) -> None:
        self.url = url
        self.goto_urls.append(url)

    async def wait_for_load_state(self, state: str) -> None:
        return None


@dataclass(slots=True)
class _FakeContext:
    """Заглушка browser context для проверки изоляции служебной страницы."""

    pages: list[_FakePage]
    page_factory: Callable[[], _FakePage]
    created_pages: list[_FakePage] = field(default_factory=list)

    async def new_page(self) -> _FakePage:
        page = self.page_factory()
        page.context = self
        self.pages.append(page)
        self.created_pages.append(page)
        return page


@dataclass(slots=True)
class _FakeBrowser:
    """Заглушка browser с несколькими context."""

    contexts: list[_FakeContext]


# Проверяет, что service page не переиспользуется из чужого browser context.
@pytest.mark.asyncio
async def test_ensure_ads_manager_service_page_ignores_other_context_pages() -> None:
    target_context = _FakeContext(pages=[], page_factory=lambda: _FakePage(url=""))
    foreign_page = _FakePage(
        url=build_service_page_url(
            "https://adsmanager.facebook.com/adsmanager/manage/ads",
            service_role=ACTIONS_SERVICE_PAGE,
        )
    )
    foreign_context = _FakeContext(pages=[foreign_page], page_factory=lambda: _FakePage(url=""))
    browser = _FakeBrowser(contexts=[foreign_context, target_context])

    page = await ensure_ads_manager_service_page(
        browser=browser,
        context=target_context,
        service_role=ACTIONS_SERVICE_PAGE,
        seed_url="https://adsmanager.facebook.com/adsmanager/manage/ads",
    )

    assert page is not foreign_page
    assert target_context.created_pages == [page]
    assert foreign_context.created_pages == []


# Проверяет, что service page в целевом context переиспользуется без создания новой вкладки.
@pytest.mark.asyncio
async def test_ensure_ads_manager_service_page_reuses_page_from_target_context() -> None:
    target_url = build_service_page_url(
        "https://adsmanager.facebook.com/adsmanager/manage/ads",
        service_role=ACTIONS_SERVICE_PAGE,
    )
    existing_page = _FakePage(url=target_url)
    target_context = _FakeContext(pages=[existing_page], page_factory=lambda: _FakePage(url=""))
    browser = _FakeBrowser(contexts=[target_context])

    page = await ensure_ads_manager_service_page(
        browser=browser,
        context=target_context,
        service_role=ACTIONS_SERVICE_PAGE,
        seed_url="https://adsmanager.facebook.com/adsmanager/manage/ads",
    )

    assert page is existing_page
    assert target_context.created_pages == []
    assert existing_page.goto_urls == []


# Проверяет, что service page собирает batch-контекст через список selected_ad_ids.
def test_build_service_page_url_preserves_batch_selected_ad_ids() -> None:
    url = build_service_page_url(
        "https://adsmanager.facebook.com/adsmanager/manage/ads?selected_campaign_ids=1",
        service_role=ACTIONS_SERVICE_PAGE,
        selected_ad_ids=["111", "222"],
    )

    assert "selected_campaign_ids" not in url
    assert "selected_ad_ids=111" in url
    assert "selected_ad_ids=222" in url
