/**
 * Регресс на #337: баннер ошибки не печатает внутренности запроса.
 *
 * Раньше `details` заполнялся напрямую из `error.message` (например,
 * queryKey/JSON-дамп react-query или сырой текст исключения) и попадал в
 * разметку как есть. Теперь `details` — только заранее написанный
 * операторский текст (см. safeApiProblemMessage); всё похожее на сырой дамп
 * (скобки, JSON, stack trace) компонент игнорирует и показывает обобщённый
 * текст вместо него.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OperatorUnavailableState } from "@/components/layout/OperatorPageBoundary";

describe("OperatorUnavailableState", () => {
  it("не рендерит сырой message с JSON/скобками", () => {
    const rawMessage =
      'Query data cannot be undefined for the queryKey \'["offers","list",true]\'';

    render(
      <OperatorUnavailableState
        title="Офферы недоступны"
        resource="каталог офферов"
        details={rawMessage}
      />,
    );

    expect(screen.queryByText(rawMessage, { exact: false })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("[");
    expect(document.body.textContent).not.toContain("queryKey");
    expect(
      screen.getByText("Не удалось загрузить каталог офферов. Повторите запрос."),
    ).toBeInTheDocument();
  });

  it("не рендерит текст, похожий на stack trace", () => {
    const rawMessage = "TypeError: Failed to fetch\n    at fetchOffers (client.ts:42:11)";

    render(
      <OperatorUnavailableState
        title="Офферы недоступны"
        resource="каталог офферов"
        details={rawMessage}
      />,
    );

    expect(screen.queryByText(/client\.ts/)).not.toBeInTheDocument();
    expect(
      screen.getByText("Не удалось загрузить каталог офферов. Повторите запрос."),
    ).toBeInTheDocument();
  });

  it("показывает заранее написанный операторский текст как есть", () => {
    render(
      <OperatorUnavailableState
        title="Офферы недоступны"
        resource="каталог офферов"
        details="Сервер сейчас перегружен, данные не обновляются."
      />,
    );

    expect(
      screen.getByText("Сервер сейчас перегружен, данные не обновляются. Повторите запрос."),
    ).toBeInTheDocument();
  });
});
