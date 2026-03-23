from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pytest

from apps.browser_host.facebook_actions import FacebookAdsActionExecutor
from apps.browser_host.playwright_attach import AttachedBrowserSession

_ADS_ROW_SELECTOR = "div[role='presentation']._1gd4"
_MORE_BUTTON_NAMES = ("Больше", "Ещё", "Еще", "More")
_ACTION_SERVICE_ROLE = "actions"


@dataclass(slots=True)
class _FakeRow:
    """Заглушка строки таблицы Ads Manager."""

    text: str
    surfaces: tuple[str, ...] = ()
    clicked: bool = False
    switch: "_FakeSwitch | None" = None
    page: "_FakePage | None" = None

    async def click(self) -> None:
        self.clicked = True
        if self.page is not None and self.page.buttons_after_row_selection:
            self.page.button_names.update(self.page.buttons_after_row_selection)

    async def inner_text(self) -> str:
        return self.text

    async def evaluate(self, expression: str, fb_ad_id: str) -> bool:
        return any(f"table_row:{fb_ad_id}" in surface for surface in self.surfaces)

    def locator(self, selector: str) -> object:
        if selector in {"input[role='switch']", "[role='switch']"}:
            if self.switch is not None:
                return self.switch
            return _FakeMissingLocator()
        raise AssertionError(f"Неожиданный селектор строки: {selector}")


class _FakeMissingLocator:
    """Заглушка пустого locator без совпадений."""

    async def count(self) -> int:
        return 0

    def nth(self, index: int) -> "_FakeMissingLocator":
        return self


@dataclass(slots=True)
class _FakeSwitch:
    """Заглушка switch-переключателя объявления."""

    name: str
    aria_checked: str
    clicked: list[str]

    async def click(self) -> None:
        self.clicked.append(self.name)
        self.aria_checked = "false" if self.aria_checked == "true" else "true"

    async def count(self) -> int:
        return 1

    def nth(self, index: int) -> "_FakeSwitch":
        return self

    async def get_attribute(self, name: str) -> str | None:
        if name == "aria-checked":
            return self.aria_checked
        return None


class _FakeRowCollection:
    """Заглушка коллекции строк таблицы Ads Manager."""

    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    async def count(self) -> int:
        return len(self._rows)

    def nth(self, index: int) -> _FakeRow:
        return self._rows[index]


class _FakeActionTarget:
    """Заглушка кликабельного элемента Ads Manager."""

    def __init__(self, name: str, clicked: list[str], page: "_FakePage") -> None:
        self._name = name
        self._clicked = clicked
        self._page = page

    async def click(self) -> None:
        self._clicked.append(self._name)
        if self._name in self._page.modal_button_names:
            self._page.modal_visible = False
            self._page.body_text = ""
        if self._name in _MORE_BUTTON_NAMES:
            self._page.menu_opened = True


@dataclass(slots=True)
class _FakePage:
    """Заглушка страницы Playwright для тестирования executor."""

    url: str
    body_text: str = ""
    rows: list[_FakeRow] = field(default_factory=list)
    button_names: set[str] = field(default_factory=set)
    menuitem_names: set[str] = field(default_factory=set)
    clicked: list[str] = field(default_factory=list)
    goto_urls: list[str] = field(default_factory=list)
    closed: bool = False
    menu_opened: bool = False
    context: "_FakeContext | None" = None
    surface_switches: dict[str, _FakeSwitch] = field(default_factory=dict)
    generic_switch: _FakeSwitch | None = None
    scroll_views: list[list[_FakeRow]] = field(default_factory=list)
    scroll_position: int = 0
    scroll_calls: list[str] = field(default_factory=list)
    modal_visible: bool = False
    modal_button_names: set[str] = field(default_factory=set)
    buttons_after_row_selection: set[str] = field(default_factory=set)

    async def goto(self, url: str, wait_until: str | None = None) -> None:
        self.url = url
        self.goto_urls.append(url)

    async def wait_for_timeout(self, timeout_ms: int) -> None:
        return None

    async def wait_for_load_state(self, state: str) -> None:
        return None

    async def bring_to_front(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed

    def locator(self, selector: str) -> object:
        if selector == _ADS_ROW_SELECTOR:
            if self.scroll_views:
                return _FakeRowCollection(self.scroll_views[self.scroll_position])
            return _FakeRowCollection(self.rows)
        if selector in {"input[role='switch']", "[role='switch']"}:
            if self.modal_visible:
                return _FakeMissingLocator()
            if self.generic_switch is not None:
                return self.generic_switch
            return _FakeMissingLocator()
        if "table_row:" in selector and "role='switch'" in selector:
            if self.modal_visible:
                return _FakeMissingLocator()
            ad_id = selector.split("table_row:", 1)[1].split("unit", 1)[0]
            switch = self.surface_switches.get(ad_id)
            if switch is not None:
                return switch
            return _FakeMissingLocator()
        if selector.startswith("button:has-text("):
            name = selector.split("button:has-text('", 1)[1].rsplit("')", 1)[0]
            return self._resolve_clickable(name, source="button")
        if selector.startswith("[role='menuitem']:has-text("):
            name = selector.split("[role='menuitem']:has-text('", 1)[1].rsplit("')", 1)[0]
            return self._resolve_clickable(name, source="menuitem")
        if selector.startswith("text="):
            name = selector.split("text=", 1)[1]
            return self._resolve_clickable(name, source="text")
        raise AssertionError(f"Неожиданный селектор: {selector}")

    def get_by_role(self, role: str, name: str) -> _FakeActionTarget:
        if role == "button":
            return self._resolve_clickable(name, source="button")
        if role == "menuitem":
            return self._resolve_clickable(name, source="menuitem")
        if role == "tab":
            return self._resolve_clickable(name, source="tab")
        raise AssertionError(f"Неожиданная роль: {role}")

    def _resolve_clickable(self, name: str, *, source: str) -> _FakeActionTarget:
        if self.modal_visible:
            if name in self.modal_button_names:
                return _FakeActionTarget(name, self.clicked, self)
            raise AssertionError(f"Модальное окно блокирует элемент: {name}")
        if name in self.button_names:
            return _FakeActionTarget(name, self.clicked, self)
        if self.menu_opened and name in self.menuitem_names:
            return _FakeActionTarget(name, self.clicked, self)
        if source == "tab" and name == "Объявления 1 выбрано":
            return _FakeActionTarget(name, self.clicked, self)
        if source == "text" and self.menu_opened and name in self.menuitem_names:
            return _FakeActionTarget(name, self.clicked, self)
        if source == "text" and name in self.button_names:
            return _FakeActionTarget(name, self.clicked, self)
        raise AssertionError(f"Неожиданный кликабельный элемент: {name}")

    async def evaluate(self, expression: str, payload) -> None:
        if isinstance(payload, list) and len(payload) == 2:
            direction = payload[0]
            self.scroll_calls.append(str(direction))
            if not self.scroll_views:
                return None
            if direction == "down" and self.scroll_position < len(self.scroll_views) - 1:
                self.scroll_position += 1
            if direction == "up" and self.scroll_position > 0:
                self.scroll_position -= 1
            return None

        edge = str(payload)
        self.scroll_calls.append(f"edge:{edge}")
        if not self.scroll_views:
            return None
        if edge == "top":
            self.scroll_position = 0
        else:
            self.scroll_position = len(self.scroll_views) - 1
        return None


@dataclass(slots=True)
class _FakeContext:
    """Заглушка browser context."""

    pages: list[_FakePage]
    page_factory: Callable[[], _FakePage]
    created_pages: list[_FakePage] = field(default_factory=list)

    async def new_page(self) -> _FakePage:
        page = self.page_factory()
        page.context = self
        _bind_page_rows(page)
        self.pages.append(page)
        self.created_pages.append(page)
        return page


class _FakeBrowser:
    """Заглушка browser с одним открытым context."""

    def __init__(self, context: _FakeContext) -> None:
        self.contexts = [context]


class _FakeSessionManager:
    """Заглушка manager для проверки executor без Playwright runtime."""

    def __init__(self, session: AttachedBrowserSession) -> None:
        self._session = session
        self.released = False

    async def ensure_session(self, profile_id: str) -> AttachedBrowserSession:
        return self._session

    async def release_session(self, session: AttachedBrowserSession) -> None:
        self.released = True


def _build_executor(
    session: AttachedBrowserSession,
) -> tuple[FacebookAdsActionExecutor, _FakeSessionManager]:
    """Собирает executor с фейковым manager для локального теста."""

    manager = _FakeSessionManager(session)
    executor = FacebookAdsActionExecutor(session_manager=manager)
    return executor, manager


def _build_session(
    base_page: _FakePage,
    temp_page_factory: Callable[[], _FakePage] | None = None,
    service_role: str | None = _ACTION_SERVICE_ROLE,
) -> tuple[AttachedBrowserSession, _FakeContext]:
    """Собирает attached session с контролируемым browser context."""

    if service_role is not None:
        base_page.url = _append_service_role(base_page.url, service_role)
    context = _FakeContext(
        [base_page],
        page_factory=temp_page_factory or (lambda: _FakePage(url="about:blank")),
    )
    base_page.context = context
    _bind_page_rows(base_page)
    browser = _FakeBrowser(context)
    attached_session = AttachedBrowserSession(
        profile_id="profile-1",
        cdp_url="http://127.0.0.1:54000",
        webdriver_url=None,
        is_attached=True,
        browser=browser,
        context=context,
    )
    return attached_session, context


def _append_service_role(url: str, service_role: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["fb_agent_service"] = [service_role]
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query, doseq=True),
            parsed.fragment,
        )
    )


def _bind_page_rows(page: _FakePage) -> None:
    for row in page.rows:
        row.page = page
    for row_group in page.scroll_views:
        for row in row_group:
            row.page = page


# Проверяет, что executor находит объявление и ставит его на паузу в уже открытой выбранной вкладке.
@pytest.mark.asyncio
async def test_pause_ad_success_in_current_selected_page() -> None:
    base_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1&selected_ad_ids=1234567890123",
        button_names={*_MORE_BUTTON_NAMES, "ОК"},
        menuitem_names={"Выключить"},
    )

    attached_session, context = _build_session(base_page)
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert result.fb_ad_id == "1234567890123"
    assert "переведено на паузу" in result.message
    assert "Больше" in base_page.clicked
    assert "Выключить" in base_page.clicked
    assert "ОК" in base_page.clicked
    assert manager.released is True
    assert context.created_pages == []


# Проверяет, что executor создает отдельную служебную страницу и не трогает рабочую вкладку пользователя.
@pytest.mark.asyncio
async def test_pause_ad_uses_dedicated_service_page_instead_of_user_tab() -> None:
    selected_clicked: list[str] = []
    user_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1&selected_campaign_ids=777",
    )
    service_page = _FakePage(
        url="about:blank",
        generic_switch=_FakeSwitch(
            name="service-switch",
            aria_checked="true",
            clicked=selected_clicked,
        ),
    )

    attached_session, context = _build_session(
        user_page,
        temp_page_factory=lambda: service_page,
        service_role=None,
    )
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert user_page.clicked == []
    assert context.created_pages == [service_page]
    assert service_page.goto_urls
    assert "fb_agent_service=actions" in service_page.goto_urls[0]
    assert "selected_ad_ids=1234567890123" in service_page.goto_urls[0]
    assert selected_clicked == ["service-switch"]
    assert manager.released is True


# Проверяет, что executor умеет ставить объявление на паузу через switch в текущей выбранной вкладке.
@pytest.mark.asyncio
async def test_pause_ad_uses_switch_in_current_selected_page() -> None:
    selected_clicked: list[str] = []
    base_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1&selected_ad_ids=1234567890123",
        generic_switch=_FakeSwitch(
            name="selected-switch",
            aria_checked="true",
            clicked=selected_clicked,
        ),
    )

    attached_session, context = _build_session(base_page)
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert selected_clicked == ["selected-switch"]
    assert base_page.generic_switch is not None
    assert base_page.generic_switch.aria_checked == "false"
    assert manager.released is True
    assert context.created_pages == []


# Проверяет, что executor сначала закрывает блокирующее окно Meta и только потом жмет switch объявления.
@pytest.mark.asyncio
async def test_pause_ad_dismisses_blocking_popup_before_switch_click() -> None:
    selected_clicked: list[str] = []
    base_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1&selected_ad_ids=1234567890123",
        body_text=(
            "Выключите блокирование рекламы\n"
            "Рекламные инструменты Meta могут работать не так, как ожидается."
        ),
        modal_visible=True,
        modal_button_names={"ОК"},
        generic_switch=_FakeSwitch(
            name="selected-switch",
            aria_checked="true",
            clicked=selected_clicked,
        ),
    )

    attached_session, context = _build_session(base_page)
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert base_page.clicked[0] == "ОК"
    assert selected_clicked == ["selected-switch"]
    assert base_page.modal_visible is False
    assert manager.released is True
    assert context.created_pages == []


# Проверяет, что popup закрывается внутри dialog и не кликает скрытый глобальный ОК на странице.
@pytest.mark.asyncio
async def test_pause_ad_closes_dialog_scoped_popup_and_skips_hidden_global_button() -> None:
    class _PopupButton:
        def __init__(
            self,
            page: "_PopupPage",
            *,
            name: str,
            visible: bool,
            close_after_clicks: int = 1,
            click_log: list[str],
        ) -> None:
            self._page = page
            self._name = name
            self._visible = visible
            self._close_after_clicks = close_after_clicks
            self._click_log = click_log

        async def click(self) -> None:
            if not self._visible:
                raise AssertionError("Скрытую кнопку нельзя кликать")
            self._click_log.append(self._name)
            self._page.click_counts[self._name] = self._page.click_counts.get(self._name, 0) + 1
            if self._page.click_counts[self._name] >= self._close_after_clicks:
                self._page.modal_visible = False
                self._page.body_text = ""

        async def is_visible(self) -> bool:
            return self._visible and self._page.modal_visible

        async def count(self) -> int:
            return 1

        def nth(self, index: int) -> "_PopupButton":
            return self

    class _PopupButtonCollection:
        def __init__(self, buttons: list[_PopupButton]) -> None:
            self._buttons = buttons

        async def count(self) -> int:
            return len(self._buttons)

        def nth(self, index: int) -> _PopupButton:
            return self._buttons[index]

    class _PopupDialog:
        def __init__(self, page: "_PopupPage") -> None:
            self._page = page

        async def inner_text(self) -> str:
            return self._page.body_text

        async def text_content(self) -> str:
            return self._page.body_text

        def get_by_role(self, role: str, name: str):
            if role != "button":
                raise AssertionError(f"Неожиданная роль внутри dialog: {role}")
            if name not in {"ОК", "Ок", "OK", "Ok"}:
                raise AssertionError(f"Неожиданное имя кнопки dialog: {name}")
            return _PopupButtonCollection(
                [
                    _PopupButton(
                        self._page,
                        name="global-hidden-OK",
                        visible=False,
                        click_log=self._page.global_clicked,
                    ),
                    _PopupButton(
                        self._page,
                        name="dialog-visible-OK",
                        visible=True,
                        close_after_clicks=2,
                        click_log=self._page.popup_clicked,
                    ),
                ]
            )

        def locator(self, selector: str):
            if selector.startswith("button:has-text(") or selector.startswith(
                "[role='button']:has-text("
            ):
                return self.get_by_role("button", "ОК")
            if selector.startswith("text="):
                return self.get_by_role("button", "ОК")
            raise AssertionError(f"Неожиданный селектор dialog: {selector}")

        async def is_visible(self) -> bool:
            return self._page.modal_visible

    class _PopupPage(_FakePage):
        def __init__(self) -> None:
            super().__init__(
                url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1&selected_ad_ids=1234567890123",
                body_text=(
                    "Выключите блокирование рекламы\n"
                    "Рекламные инструменты Meta могут работать не так, как ожидается."
                ),
                button_names={*_MORE_BUTTON_NAMES, "ОК"},
                menuitem_names={"Выключить"},
                modal_visible=True,
                modal_button_names=set(),
            )
            self.popup_clicked: list[str] = []
            self.global_clicked: list[str] = []
            self.click_counts: dict[str, int] = {}

        def get_by_role(self, role: str, name: str | None = None):
            if role == "dialog":
                return _PopupDialog(self)
            if role == "button" and name in {"ОК", "Ок", "OK", "Ok"}:
                return _PopupButton(
                    self,
                    name="global-hidden-OK",
                    visible=False,
                    click_log=self.global_clicked,
                )
            return super().get_by_role(role, name)

        def locator(self, selector: str):
            if selector in ("[role='dialog']", "[aria-modal='true']"):
                return _PopupDialog(self)
            if selector.startswith("button:has-text(") or selector.startswith(
                "[role='button']:has-text("
            ):
                return _PopupButton(
                    self,
                    name="global-hidden-OK",
                    visible=False,
                    click_log=self.global_clicked,
                )
            if selector.startswith("text="):
                return _PopupButton(
                    self,
                    name="global-hidden-OK",
                    visible=False,
                    click_log=self.global_clicked,
                )
            return super().locator(selector)

        async def wait_for_timeout(self, timeout_ms: int) -> None:
            return None

    base_page = _PopupPage()
    base_page.url = _append_service_role(base_page.url, _ACTION_SERVICE_ROLE)
    context = _FakeContext([base_page], page_factory=lambda: _PopupPage())
    base_page.context = context
    browser = _FakeBrowser(context)
    attached_session = AttachedBrowserSession(
        profile_id="profile-1",
        cdp_url="http://127.0.0.1:54000",
        webdriver_url=None,
        is_attached=True,
        browser=browser,
        context=context,
    )
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert base_page.popup_clicked == ["dialog-visible-OK", "dialog-visible-OK"]
    assert base_page.global_clicked == []
    assert base_page.modal_visible is False
    assert manager.released is True
    assert context.created_pages == []


# Проверяет, что executor возвращает понятное сообщение, если объявление не найдено в текущих вкладках Ads Manager.
@pytest.mark.asyncio
async def test_pause_ad_returns_not_found_message() -> None:
    base_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1",
    )

    attached_session, context = _build_session(base_page)
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is False
    assert result.message == "Не удалось найти объявление 1234567890123 для паузы"
    assert manager.released is True
    assert context.created_pages == []


# Проверяет, что executor умеет откатиться к строке таблицы, если действие нельзя выполнить напрямую на текущей странице.
@pytest.mark.asyncio
async def test_pause_ad_falls_back_to_row_match_when_toolbar_is_missing() -> None:
    base_row = _FakeRow(
        "Объявление без явного id",
        surfaces=("/am/table/table_row:1234567890123unit/table_cell:forObjectType(name,ADGROUP)",),
    )
    base_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1",
        rows=[base_row],
        button_names={"ОК"},
        buttons_after_row_selection={"Пауза"},
    )

    attached_session, context = _build_session(base_page)
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert base_row.clicked is True
    assert "Пауза" in base_page.clicked
    assert "ОК" in base_page.clicked
    assert manager.released is True
    assert context.created_pages == []


# Проверяет, что executor умеет находить строку по data-surface даже когда fb_ad_id не виден в тексте.
@pytest.mark.asyncio
async def test_pause_ad_finds_row_by_surface_id() -> None:
    rows = [
        _FakeRow(
            "Объявление без явного id",
            surfaces=(
                "/am/table/table_row:1234567890123unit/table_cell:forObjectType(name,ADGROUP)",
            ),
        )
    ]
    base_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1",
        rows=rows,
        button_names={"ОК"},
        buttons_after_row_selection={"Пауза"},
    )

    attached_session, context = _build_session(base_page)
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert rows[0].clicked is True
    assert "Пауза" in base_page.clicked
    assert "ОК" in base_page.clicked
    assert manager.released is True
    assert context.created_pages == []


# Проверяет, что executor умеет докрутить список вниз и найти объявление вне текущего окна.
@pytest.mark.asyncio
async def test_pause_ad_scrolls_down_to_find_row() -> None:
    target_row = _FakeRow(
        "Объявление 1234567890123",
        surfaces=("/am/table/table_row:1234567890123unit/table_cell:forObjectType(name,ADGROUP)",),
    )
    base_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1",
        button_names={"ОК"},
        buttons_after_row_selection={"Пауза"},
        scroll_views=[[], [target_row]],
    )

    attached_session, context = _build_session(base_page)
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert target_row.clicked is True
    assert "down" in base_page.scroll_calls
    assert manager.released is True
    assert context.created_pages == []


# Проверяет, что executor возвращается наверх и повторяет поиск, если объявление осталось выше текущего скролла.
@pytest.mark.asyncio
async def test_pause_ad_scrolls_back_to_top_and_finds_row() -> None:
    target_row = _FakeRow(
        "Объявление 1234567890123",
        surfaces=("/am/table/table_row:1234567890123unit/table_cell:forObjectType(name,ADGROUP)",),
    )
    base_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1",
        button_names={"ОК"},
        buttons_after_row_selection={"Пауза"},
        scroll_views=[[target_row], []],
        scroll_position=1,
    )

    attached_session, context = _build_session(base_page)
    executor, manager = _build_executor(attached_session)

    result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert target_row.clicked is True
    assert "edge:top" in base_page.scroll_calls
    assert manager.released is True
    assert context.created_pages == []


# Проверяет, что executor умеет возобновлять объявление через switch в строке таблицы, если toolbar недоступен.
@pytest.mark.asyncio
async def test_resume_ad_uses_row_switch_when_toolbar_is_missing() -> None:
    base_clicked: list[str] = []
    base_row = _FakeRow(
        "Объявление без явного id",
        surfaces=(
            "/am/table/table_row:1234567890123unit/table_cell:forObjectType(toggle,ADGROUP)",
        ),
        switch=_FakeSwitch(
            name="row-switch",
            aria_checked="false",
            clicked=base_clicked,
        ),
    )
    base_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1",
        rows=[base_row],
    )

    attached_session, context = _build_session(base_page)
    executor, manager = _build_executor(attached_session)

    result = await executor.resume_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert base_clicked == ["row-switch"]
    assert base_row.switch is not None
    assert base_row.switch.aria_checked == "true"
    assert base_row.clicked is False
    assert manager.released is True
    assert context.created_pages == []


# Проверяет, что executor возобновляет объявление в текущей выбранной вкладке без открытия новой.
@pytest.mark.asyncio
async def test_resume_ad_success_in_current_selected_page() -> None:
    base_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1&selected_ad_ids=1234567890123",
        button_names={*_MORE_BUTTON_NAMES, "ОК"},
        menuitem_names={"Включить"},
    )

    attached_session, context = _build_session(base_page)
    executor, manager = _build_executor(attached_session)

    result = await executor.resume_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1234567890123",
    )

    assert result.success is True
    assert result.fb_ad_id == "1234567890123"
    assert "возобновлено" in result.message
    assert "Больше" in base_page.clicked
    assert "Включить" in base_page.clicked
    assert "ОК" in base_page.clicked
    assert manager.released is True
    assert context.created_pages == []


# Проверяет, что executor не создаёт временные вкладки и меняет статус в текущей странице.
@pytest.mark.asyncio
async def test_pause_ad_does_not_create_temporary_tabs() -> None:
    first_switch_clicks: list[str] = []
    second_switch_clicks: list[str] = []
    base_page = _FakePage(
        url="https://adsmanager.facebook.com/adsmanager/manage/ads?act=1",
        surface_switches={
            "1111111111111": _FakeSwitch(
                name="switch-111",
                aria_checked="true",
                clicked=first_switch_clicks,
            ),
            "2222222222222": _FakeSwitch(
                name="switch-222",
                aria_checked="true",
                clicked=second_switch_clicks,
            ),
        },
    )

    attached_session, context = _build_session(base_page)
    executor, manager = _build_executor(attached_session)

    first_result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="1111111111111",
    )
    second_result = await executor.pause_ad(
        profile_id="profile-1",
        browser_host_name="browser-host-local",
        fb_ad_id="2222222222222",
    )

    assert first_result.success is True
    assert second_result.success is True
    assert first_switch_clicks == ["switch-111"]
    assert second_switch_clicks == ["switch-222"]
    assert len(context.created_pages) == 0
    assert manager.released is True
