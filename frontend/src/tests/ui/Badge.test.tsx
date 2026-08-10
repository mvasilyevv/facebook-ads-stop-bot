/** Tests for the presentational Badge primitive. */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Badge } from "@/components/ui/Badge";

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
    const { container } = render(
      <Badge variant="stop" withDot={false}>
        STOP
      </Badge>,
    );
    expect(container.querySelector("[aria-hidden='true']")).not.toBeInTheDocument();
  });

  // Все 15 вариантов рендерятся
  it.each([
    "normal",
    "warning",
    "stop",
    "claimed",
    "disabled",
    "success",
    "info",
    "neutral",
    "pending",
    "running",
    "done",
    "failed",
    "retrying",
    "cancelled",
  ] as const)("вариант %s рендерится без ошибок", (variant) => {
    render(<Badge variant={variant}>{variant}</Badge>);
    expect(screen.getByText(variant)).toBeInTheDocument();
  });

  it.each(["disabled", "neutral", "cancelled"] as const)(
    "использует контрастный semantic dot для варианта %s",
    (variant) => {
      const { container } = render(<Badge variant={variant}>{variant}</Badge>);
      const dot = container.querySelector("[aria-hidden='true']");

      expect(dot).toHaveClass("bg-bg-8");
      expect(dot).not.toHaveClass("bg-bg-7");
    },
  );

  // Размеры
  it.each(["sm", "md"] as const)("size %s рендерится", (size) => {
    render(
      <Badge variant="warning" size={size}>
        OK
      </Badge>,
    );
    expect(screen.getByText("OK")).toBeInTheDocument();
  });
});
