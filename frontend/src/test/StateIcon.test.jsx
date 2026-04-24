import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StateIcon } from "../components/StateIcon.jsx";

describe("StateIcon", () => {
  it("отображает метку для статуса NORMAL", () => {
    render(<StateIcon state="NORMAL" />);
    expect(screen.getByText("Норма")).toBeInTheDocument();
  });

  it("отображает метку для статуса STOP_SENT", () => {
    render(<StateIcon state="STOP_SENT" />);
    expect(screen.getByText("Требует отключения")).toBeInTheDocument();
  });

  it("отображает метку для статуса WARNING_SENT", () => {
    render(<StateIcon state="WARNING_SENT" />);
    expect(screen.getByText("Предупреждение")).toBeInTheDocument();
  });

  it("отображает '?' для неизвестного статуса", () => {
    render(<StateIcon state="UNKNOWN_STATE" />);
    expect(screen.getByText("?")).toBeInTheDocument();
  });

  it("добавляет правильный CSS-класс для размера lg", () => {
    const { container } = render(<StateIcon state="NORMAL" size="lg" />);
    expect(container.firstChild).toHaveClass("state-icon--lg");
  });

  it("добавляет css-класс для состояния строчными буквами", () => {
    const { container } = render(<StateIcon state="STOP_SENT" />);
    expect(container.firstChild).toHaveClass("state-icon--stop_sent");
  });

  it("задаёт tooltip из ALERT_STATE_TOOLTIPS", () => {
    render(<StateIcon state="DISABLED" />);
    const el = screen.getByTitle(/Отключено/);
    expect(el).toBeInTheDocument();
  });
});
