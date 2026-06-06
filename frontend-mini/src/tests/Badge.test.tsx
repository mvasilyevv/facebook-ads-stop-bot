/**
 * Тест FSM-маппинга Badge компонента.
 * Проверяет, что alertStateToBadgeVariant корректно маппит все alert_state.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AlertStateBadge, TaskStatusBadge } from "@/components/ui/Badge";

describe("AlertStateBadge", () => {
  // normal → текст "Норма"
  it("отображает 'Норма' для normal", () => {
    render(<AlertStateBadge state="normal" />);
    expect(screen.getByText("Норма")).toBeInTheDocument();
  });

  // stop_sent → текст "Стоп"
  it("отображает 'Стоп' для stop_sent", () => {
    render(<AlertStateBadge state="stop_sent" />);
    expect(screen.getByText("Стоп")).toBeInTheDocument();
  });

  // warning_sent → текст "Предупреждение"
  it("отображает 'Предупреждение' для warning_sent", () => {
    render(<AlertStateBadge state="warning_sent" />);
    expect(screen.getByText("Предупреждение")).toBeInTheDocument();
  });

  // claimed → "В работе"
  it("отображает 'В работе' для claimed", () => {
    render(<AlertStateBadge state="claimed" />);
    expect(screen.getByText("В работе")).toBeInTheDocument();
  });

  // disabled → "Отключено"
  it("отображает 'Отключено' для disabled", () => {
    render(<AlertStateBadge state="disabled" />);
    expect(screen.getByText("Отключено")).toBeInTheDocument();
  });

  // UPPERCASE из TMA-API нормализуется
  it("нормализует UPPERCASE из TMA-API ('STOP_SENT' → 'Стоп')", () => {
    render(<AlertStateBadge state="STOP_SENT" />);
    expect(screen.getByText("Стоп")).toBeInTheDocument();
  });

  // Неизвестный state → fallback normal
  it("fallback к 'Норма' для неизвестного состояния", () => {
    render(<AlertStateBadge state="ARCHIVED_OLD" />);
    expect(screen.getByText("Норма")).toBeInTheDocument();
  });
});

describe("TaskStatusBadge", () => {
  // PENDING → "В очереди"
  it("отображает 'В очереди' для PENDING", () => {
    render(<TaskStatusBadge status="PENDING" />);
    expect(screen.getByText("В очереди")).toBeInTheDocument();
  });

  // FAILED → "Ошибка"
  it("отображает 'Ошибка' для FAILED", () => {
    render(<TaskStatusBadge status="FAILED" />);
    expect(screen.getByText("Ошибка")).toBeInTheDocument();
  });

  // SUCCEEDED → "Выполнено"
  it("отображает 'Выполнено' для SUCCEEDED", () => {
    render(<TaskStatusBadge status="SUCCEEDED" />);
    expect(screen.getByText("Выполнено")).toBeInTheDocument();
  });
});
