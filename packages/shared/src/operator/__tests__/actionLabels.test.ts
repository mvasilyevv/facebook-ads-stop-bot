import { describe, expect, it } from "vitest";

import {
  operatorActionKindLabel,
  operatorActionStateReason,
} from "../actionLabels";

describe("operator action labels", () => {
  it.each([
    ["pause", "Отключение объявления"],
    ["activate", "Включение объявления"],
    ["scan", "Сканирование кабинетов"],
    ["create", "Создание кампании"],
    ["duplicate", "Дублирование кампании"],
    ["other", "Системное действие"],
  ] as const)("localizes %s", (kind, label) => {
    expect(operatorActionKindLabel(kind)).toBe(label);
  });

  it("does not expose an unknown backend value", () => {
    expect(operatorActionKindLabel("internal_future_kind")).toBe(
      "Операторское действие",
    );
  });

  it.each([
    ["queued", "Команда принята и ожидает выполнения."],
    ["running", "Команда выполняется; итог ещё не подтверждён."],
    ["confirmed", "Результат команды подтверждён."],
    [
      "failed",
      "Команда завершилась ошибкой. Проверьте состояние перед повтором.",
    ],
    ["cancelled", "Команда отменена."],
    [
      "unknown",
      "Результат команды требует сверки. Не повторяйте действие вслепую.",
    ],
  ] as const)("describes %s without backend reason text", (state, reason) => {
    expect(operatorActionStateReason(state)).toBe(reason);
  });

  it("does not expose an unknown backend state", () => {
    expect(operatorActionStateReason("internal_retry_exhausted")).toBe(
      "Состояние команды требует сверки. Не повторяйте действие вслепую.",
    );
  });
});
