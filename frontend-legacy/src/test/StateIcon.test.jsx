import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StateIcon } from "../components/StateIcon.jsx";

describe("StateIcon", () => {
  // Сценарий: статус NORMAL показывает ожидаемую русскую метку.
  it("отображает метку для статуса NORMAL", () => {
    render(<StateIcon state="NORMAL" />);
    expect(screen.getByText("Норма")).toBeInTheDocument();
  });

  // Сценарий: статус STOP_SENT показывает метку требуемого действия.
  it("отображает метку для статуса STOP_SENT", () => {
    render(<StateIcon state="STOP_SENT" />);
    expect(screen.getByText("Требует отключения")).toBeInTheDocument();
  });

  // Сценарий: статус WARNING_SENT показывает предупреждающую метку.
  it("отображает метку для статуса WARNING_SENT", () => {
    render(<StateIcon state="WARNING_SENT" />);
    expect(screen.getByText("Предупреждение")).toBeInTheDocument();
  });

  // Сценарий: неизвестный статус не ломает компонент и показывает запасное значение.
  it("отображает '?' для неизвестного статуса", () => {
    render(<StateIcon state="UNKNOWN_STATE" />);
    expect(screen.getByText("?")).toBeInTheDocument();
  });

  // Сценарий: размер lg применяет актуальные Tailwind-классы размера.
  it("добавляет классы размера lg", () => {
    const { container } = render(<StateIcon state="NORMAL" size="lg" />);
    expect(container.firstChild).toHaveClass("px-2.5", "py-1", "text-2xs");
  });

  // Сценарий: состояние STOP_SENT применяет актуальные классы опасного статуса.
  it("добавляет классы состояния STOP_SENT", () => {
    const { container } = render(<StateIcon state="STOP_SENT" />);
    expect(container.firstChild).toHaveClass("bg-danger-muted", "text-danger", "border-danger/40");
  });

  // Сценарий: всплывающая подсказка берётся из справочника подсказок статусов.
  it("задаёт tooltip из ALERT_STATE_TOOLTIPS", () => {
    render(<StateIcon state="DISABLED" />);
    const el = screen.getByTitle(/Отключено/);
    expect(el).toBeInTheDocument();
  });
});
