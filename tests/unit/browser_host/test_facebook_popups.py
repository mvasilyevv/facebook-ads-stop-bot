from __future__ import annotations

import pytest

from apps.browser_host.facebook_popups import dismiss_known_ads_manager_popups


class _PopupButton:
    """Заглушка кнопки внутри dialog."""

    def __init__(self, page: "_PopupPage", name: str, *, visible: bool) -> None:
        self._page = page
        self._name = name
        self._visible = visible

    async def click(self) -> None:
        if not self._visible:
            raise AssertionError("Скрытую кнопку нельзя кликать")
        self._page.clicked.append(self._name)
        self._page.modal_visible = False
        self._page.body_text = ""

    async def is_visible(self) -> bool:
        return self._visible and self._page.modal_visible

    async def count(self) -> int:
        return 1

    def nth(self, index: int) -> "_PopupButton":
        return self


class _PopupButtonCollection:
    """Заглушка коллекции кнопок внутри dialog."""

    def __init__(self, buttons: list[_PopupButton]) -> None:
        self._buttons = buttons

    async def count(self) -> int:
        return len(self._buttons)

    def nth(self, index: int) -> _PopupButton:
        return self._buttons[index]


class _PopupDialog:
    """Заглушка dialog с текстом и кнопками."""

    def __init__(self, page: "_PopupPage", *, text: str, button_name: str, visible: bool) -> None:
        self._page = page
        self._text = text
        self._button_name = button_name
        self._visible = visible

    async def inner_text(self) -> str:
        return self._text

    async def text_content(self) -> str:
        return self._text

    async def is_visible(self) -> bool:
        return self._visible and self._page.modal_visible

    def get_by_role(self, role: str, name: str):
        if role != "button":
            raise AssertionError(f"Неожиданная роль внутри dialog: {role}")
        if name not in {"ОК", "Ок", "OK", "Ok", "Закрыть", "Close"}:
            raise AssertionError(f"Неожиданное имя кнопки dialog: {name}")
        return _PopupButtonCollection(
            [
                _PopupButton(self._page, "global-hidden-OK", visible=False),
                _PopupButton(self._page, self._button_name, visible=self._visible),
            ]
        )

    def locator(self, selector: str):
        if selector.startswith("button:has-text(") or selector.startswith(
            "[role='button']:has-text("
        ):
            return self.get_by_role("button", self._button_name)
        if selector.startswith("text="):
            return self.get_by_role("button", self._button_name)
        raise AssertionError(f"Неожиданный селектор dialog: {selector}")


class _PopupDialogCollection:
    """Заглушка коллекции dialog-ов на странице."""

    def __init__(self, dialogs: list[_PopupDialog]) -> None:
        self._dialogs = dialogs

    async def count(self) -> int:
        return len(self._dialogs)

    def nth(self, index: int) -> _PopupDialog:
        return self._dialogs[index]


class _PopupPage:
    """Заглушка страницы для проверки закрытия popup."""

    def __init__(self) -> None:
        self.body_text = (
            "Общая страница без нужной модалки\n"
            "Выключите блокирование рекламы\n"
            "Рекламные инструменты Meta могут работать не так, как ожидается."
        )
        self.modal_visible = True
        self.clicked: list[str] = []
        self.global_clicked: list[str] = []
        self.visible_dialog = _PopupDialog(
            self,
            text=(
                "Выключите блокирование рекламы\n"
                "Рекламные инструменты Meta могут работать не так, как ожидается."
            ),
            button_name="Ok",
            visible=True,
        )
        self.hidden_dialog = _PopupDialog(
            self,
            text="Нейтральный диалог без нужного текста",
            button_name="ОК",
            visible=False,
        )

    def get_by_role(self, role: str, name: str | None = None):
        if role == "dialog":
            return _PopupDialogCollection([self.hidden_dialog, self.visible_dialog])
        if role == "button" and name in {"ОК", "Ок", "OK", "Ok"}:
            raise AssertionError("Нежелательный поиск кнопки вне dialog")
        raise AssertionError(f"Неожиданная роль: {role} {name}")

    def locator(self, selector: str):
        if selector in ("[role='dialog']", "[aria-modal='true']"):
            return _PopupDialogCollection([self.hidden_dialog, self.visible_dialog])
        if selector.startswith("button:has-text(") or selector.startswith(
            "[role='button']:has-text("
        ):
            raise AssertionError("Нежелательный поиск кнопки вне dialog")
        if selector.startswith("text="):
            raise AssertionError("Нежелательный поиск кнопки вне dialog")
        raise AssertionError(f"Неожиданный селектор страницы: {selector}")

    async def wait_for_timeout(self, delay_ms: int) -> None:
        return None

    async def wait_for_load_state(self, state: str) -> None:
        return None


# Проверяет, что popup-хелпер ищет кнопку только внутри реального dialog и закрывает модалку целиком.
@pytest.mark.asyncio
async def test_dismiss_known_ads_manager_popups_uses_visible_dialog_and_waits_for_close() -> None:
    page = _PopupPage()

    result = await dismiss_known_ads_manager_popups(page)

    assert result is True
    assert page.clicked == ["Ok"]
    assert page.global_clicked == []
    assert page.modal_visible is False
