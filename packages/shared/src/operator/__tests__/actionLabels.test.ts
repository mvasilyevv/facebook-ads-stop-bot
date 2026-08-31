import { describe, expect, it } from "vitest";

import {
  OPERATOR_COMMAND_QUEUED_NOTICE,
  OPERATOR_UNKNOWN_RESULT_LIST_NOTICE,
  OPERATOR_UNKNOWN_RESULT_NOTICE,
  operatorActionKindLabel,
  operatorActionReason,
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

  it("shows the recorded reason instead of a constant per state", () => {
    const disabledAccount = operatorActionReason({
      state: "failed",
      reason:
        "Шаг: создание объектов кампании. Ответ Meta: Отключенные аккаунты не могут создавать рекламу.",
    });
    const deadline = operatorActionReason({
      state: "failed",
      reason: "Шаг: загрузка креативов. Истёк отведённый заливу срок.",
    });

    expect(disabledAccount).not.toBe(deadline);
    expect(disabledAccount).toContain("Отключенные аккаунты");
    expect(deadline).toContain("Истёк отведённый заливу срок");
  });

  it("says the reason is unrecorded instead of a cheerful constant", () => {
    expect(operatorActionReason({ state: "failed", reason: null })).toBe(
      "Причина отказа не записана. Проверьте состояние перед повтором.",
    );
    expect(operatorActionReason({ state: "unknown", reason: "   " })).toBe(
      "Причина не записана. Результат требует сверки — не повторяйте действие вслепую.",
    );
  });

  it("keeps the reconcile warning on an ambiguous outcome that names its reason", () => {
    expect(
      operatorActionReason({
        state: "unknown",
        reason: "Ответ Meta потерян после отправки кампании.",
      }),
    ).toBe(
      "Ответ Meta потерян после отправки кампании. Результат требует сверки — не повторяйте действие вслепую.",
    );
  });

  it("refuses a reason that carries internals instead of showing it trimmed", () => {
    expect(
      operatorActionReason({
        state: "failed",
        reason: "Traceback: secret-host token=unsafe",
      }),
    ).toBe("Причина отказа не записана. Проверьте состояние перед повтором.");
    expect(
      operatorActionReason({
        state: "failed",
        reason: "Ответ Meta: сбой на https://graph.facebook.example/act_1/ads",
      }),
    ).toBe("Причина отказа не записана. Проверьте состояние перед повтором.");
    expect(
      operatorActionReason({
        state: "failed",
        reason: "Отказ по объекту 00000000-0000-4000-8000-000000000099",
      }),
    ).toBe("Причина отказа не записана. Проверьте состояние перед повтором.");
  });

  it("keeps state copy for commands that have not failed", () => {
    expect(operatorActionReason({ state: "queued", reason: null })).toBe(
      "Команда принята и ожидает выполнения.",
    );
    expect(operatorActionReason({ state: "confirmed", reason: null })).toBe(
      "Результат команды подтверждён.",
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
    ["cancelled", "warning"],
  ] as const)("tones %s as %s", (state, tone) => {
    expect(operatorCommandTone(state)).toBe(tone);
  });

  it("keeps the error tone for a real failure only", () => {
    // Систему отменяет собственная защита: выключенное сканирование, пустой
    // набор кабинетов, незаданный owner scope. Красный на таком исходе
    // отправил бы оператора чинить то, что работает как задумано.
    expect(operatorCommandTone("cancelled")).not.toBe("error");
    expect(operatorCommandTone("failed")).toBe("error");
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

  // Единый источник копии про «unknown ≠ success» (issue-аудит: копипаста
  // операторских формулировок на /actions, деталь действия и в OperatorAds.tsx
  // обоих фронтов) — см. также operatorCopyGuard.test.ts.
  it("keeps one canonical unknown-result banner", () => {
    expect(OPERATOR_UNKNOWN_RESULT_NOTICE).toBe(
      "Внешний результат неоднозначен. Система сверяет фактический статус; успех не подтверждён.",
    );
  });

  it("keeps one canonical unknown-result list caption", () => {
    expect(OPERATOR_UNKNOWN_RESULT_LIST_NOTICE).toBe(
      "Неизвестный результат означает проверку фактического результата, а не успешное завершение.",
    );
  });

  it("keeps one canonical queued-command notice", () => {
    expect(OPERATOR_COMMAND_QUEUED_NOTICE).toBe(
      "Результат будет подтверждён отдельной задачей.",
    );
  });
});
