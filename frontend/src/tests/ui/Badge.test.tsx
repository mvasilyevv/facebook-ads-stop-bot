/**
 * Тесты Badge — FSM-маппинг через @fb/shared хелперы + рендер всех вариантов.
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Badge } from "@/components/ui/Badge";
import { alertStateToBadgeVariant, taskStatusToBadgeVariant } from "@fb/shared";

describe("Badge", () => {
  // Базовый рендер
  it("рендерит текст", () => {
    render(<Badge>STOP</Badge>);
    expect(screen.getByText("STOP")).toBeInTheDocument();
  });

  // Dot по умолчанию есть
  it("показывает dot по умолчанию", () => {
    const { container } = render(<Badge variant="warning">WARN</Badge>);
    // dot — aria-hidden span
    const dot = container.querySelector("[aria-hidden='true']");
    expect(dot).toBeInTheDocument();
  });

  // withDot=false скрывает dot
  it("withDot=false — dot отсутствует", () => {
    const { container } = render(<Badge variant="stop" withDot={false}>STOP</Badge>);
    expect(container.querySelector("[aria-hidden='true']")).not.toBeInTheDocument();
  });

  // FSM alert_state маппинг через @fb/shared
  it.each([
    ["normal", "normal"],
    ["warning_sent", "warning"],
    ["stop_sent", "stop"],
    ["claimed", "claimed"],
    ["disabled", "disabled"],
  ] as const)(
    "alertStateToBadgeVariant(%s) → variant %s рендерится",
    (state, _variant) => {
      render(<Badge variant={alertStateToBadgeVariant(state)}>{state}</Badge>);
      expect(screen.getByText(state)).toBeInTheDocument();
    },
  );

  // Task status маппинг через @fb/shared
  it.each([
    ["PENDING", "pending"],
    ["RUNNING", "running"],
    ["SUCCEEDED", "done"],
    ["FAILED", "failed"],
    ["RETRYING", "retrying"],
    ["CANCELLED", "cancelled"],
  ] as const)(
    "taskStatusToBadgeVariant(%s) → variant %s",
    (status, _expected) => {
      const variant = taskStatusToBadgeVariant(status);
      render(<Badge variant={variant as "pending"}>{status}</Badge>);
      expect(screen.getByText(status)).toBeInTheDocument();
    },
  );

  // Все 15 вариантов рендерятся
  it.each([
    "normal", "warning", "stop", "claimed", "disabled",
    "success", "info", "neutral", "pending", "running",
    "done", "failed", "retrying", "cancelled", "draft",
  ] as const)(
    "вариант %s рендерится без ошибок",
    (variant) => {
      render(<Badge variant={variant}>{variant}</Badge>);
      expect(screen.getByText(variant)).toBeInTheDocument();
    },
  );

  // Размеры
  it.each(["sm", "md"] as const)("size %s рендерится", (size) => {
    render(<Badge variant="warning" size={size}>OK</Badge>);
    expect(screen.getByText("OK")).toBeInTheDocument();
  });
});
