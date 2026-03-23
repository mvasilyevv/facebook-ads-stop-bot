import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DecisionJournal } from "../src/components/DecisionJournal";
import type { DecisionItem } from "../src/types";

function buildDecision(overrides: Partial<DecisionItem>): DecisionItem {
  return {
    id: "decision-1",
    scan_run_id: "scan-run-1",
    fb_ad_id: "120241420000000001",
    rule_id: null,
    decision: "NO_ACTION",
    reason: "Изменения не требуются",
    action_executed: false,
    action_status: null,
    resolved_cpa_usd: "5.00",
    created_at: "2026-03-22T12:00:00Z",
    ...overrides,
  };
}

describe("DecisionJournal", () => {
  // Проверяет, что журнал показывает пустое состояние, если решений для отображения ещё нет.
  it("показывает empty state без решений", () => {
    render(
      <DecisionJournal
        decisions={[]}
        emptyTitle="Решений пока нет"
        emptyDescription="Журнал появится после первого скана"
      />,
    );

    expect(screen.getByText("Решений пока нет")).toBeInTheDocument();
    expect(screen.getByText("Журнал появится после первого скана")).toBeInTheDocument();
  });

  // Проверяет, что журнал корректно нормализует execution state даже при частично заполненном ответе API.
  it("нормализует execution state из решения и action status", () => {
    render(
      <DecisionJournal
        decisions={[
          buildDecision({
            id: "decision-no-action",
            fb_ad_id: "ad-no-action",
            decision: "NO_ACTION",
            reason: "Ничего не меняем",
            action_status: null,
            execution_state: undefined,
          }),
          buildDecision({
            id: "decision-pending",
            fb_ad_id: "ad-pending",
            decision: "WOULD_PAUSE",
            reason: "Ожидает нажатия",
            action_status: "PENDING",
            execution_state: undefined,
          }),
          buildDecision({
            id: "decision-failed",
            fb_ad_id: "ad-failed",
            decision: "WOULD_RESUME",
            reason: "Кнопка не найдена",
            action_status: "FAILED",
            execution_state: undefined,
          }),
        ]}
        emptyTitle="Решений пока нет"
        emptyDescription="Журнал появится после первого скана"
      />,
    );

    expect(screen.getByText("не требовалось")).toBeInTheDocument();
    expect(screen.getByText("выполняется")).toBeInTheDocument();
    expect(screen.getByText("ошибка")).toBeInTheDocument();
    expect(screen.getByText("без изменений: Ничего не меняем")).toBeInTheDocument();
    expect(screen.getByText("было бы выключено: Ожидает нажатия")).toBeInTheDocument();
    expect(screen.getByText("было бы включено: Кнопка не найдена")).toBeInTheDocument();
  });
});
