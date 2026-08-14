import { describe, expect, it } from "vitest";

import {
  operatorActionKindLabel,
  operatorActionRecovery,
  operatorActionStateReason,
  operatorCommandTone,
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

  it("offers an exact target check for failed or ambiguous ad commands", () => {
    expect(operatorActionRecovery("failed", "ad-42")).toEqual({
      label: "Проверить объявление",
      destination: "target",
    });
    expect(operatorActionRecovery("unknown", "ad-42")).toEqual({
      label: "Проверить объявление",
      destination: "target",
    });
  });

  it.each([
    ["confirmed", "success"],
    ["queued", "info"],
    ["running", "info"],
    ["unknown", "warning"],
    ["failed", "error"],
    ["cancelled", "error"],
  ] as const)("tones %s as %s", (state, tone) => {
    expect(operatorCommandTone(state)).toBe(tone);
  });

  it("reserves the success tone for a confirmed result only", () => {
    // HTTP 202 = queued. Зелёный тон на принятой, но не выполненной команде
    // означал бы для оператора завершённое money-действие.
    expect(operatorCommandTone("queued")).not.toBe("success");
    expect(operatorCommandTone("running")).not.toBe("success");
    expect(operatorCommandTone("internal_future_state")).toBe("warning");
    expect(operatorCommandTone(undefined)).toBe("warning");
  });

  it("falls back to source diagnostics without inventing a target", () => {
    expect(operatorActionRecovery("failed", null)).toEqual({
      label: "Проверить источники",
      destination: "sources",
    });
    expect(operatorActionRecovery("confirmed", "ad-42")).toBeNull();
  });
});
