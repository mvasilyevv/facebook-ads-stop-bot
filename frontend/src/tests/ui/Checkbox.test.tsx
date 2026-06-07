/**
 * Тесты Checkbox — tri-state (unchecked/checked/indeterminate) + a11y.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { Checkbox, type CheckboxState } from "@/components/ui/Checkbox";

// Обёртка с состоянием для интерактивных тестов
function Controlled({ initial = false as CheckboxState }) {
  const [checked, setChecked] = useState<CheckboxState>(initial);
  return (
    <Checkbox
      checked={checked}
      onChange={(v) => setChecked(v)}
      label="Тест"
    />
  );
}

describe("Checkbox", () => {
  // Unchecked — aria-checked="false"
  it("unchecked — aria-checked false", () => {
    render(<Checkbox checked={false} label="Test" />);
    expect(screen.getByRole("checkbox")).toHaveAttribute("aria-checked", "false");
  });

  // Checked — aria-checked="true"
  it("checked — aria-checked true", () => {
    render(<Checkbox checked={true} label="Test" />);
    expect(screen.getByRole("checkbox")).toHaveAttribute("aria-checked", "true");
  });

  // Indeterminate — aria-checked="mixed"
  it("indeterminate — aria-checked mixed", () => {
    render(<Checkbox checked="indeterminate" label="Test" />);
    expect(screen.getByRole("checkbox")).toHaveAttribute("aria-checked", "mixed");
  });

  // Клик на unchecked → onChange(true)
  it("unchecked + клик → onChange вызывается с true", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Checkbox checked={false} onChange={onChange} label="Test" />);
    await user.click(screen.getByRole("checkbox"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  // Клик на checked → onChange(false)
  it("checked + клик → onChange вызывается с false", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Checkbox checked={true} onChange={onChange} label="Test" />);
    await user.click(screen.getByRole("checkbox"));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  // Indeterminate + клик → onChange(true) (indeterminate → checked)
  it("indeterminate + клик → onChange(true)", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Checkbox checked="indeterminate" onChange={onChange} label="Test" />);
    await user.click(screen.getByRole("checkbox"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  // Disabled — клик не вызывает onChange
  it("disabled — onChange не вызывается", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Checkbox checked={false} onChange={onChange} disabled label="Test" />);
    await user.click(screen.getByRole("checkbox"));
    expect(onChange).not.toHaveBeenCalled();
  });

  // Контролируемый переход состояний
  it("переключение unchecked → checked → unchecked", async () => {
    const user = userEvent.setup();
    render(<Controlled initial={false} />);
    const cb = screen.getByRole("checkbox");
    // → checked
    await user.click(cb);
    expect(cb).toHaveAttribute("aria-checked", "true");
    // → unchecked
    await user.click(cb);
    expect(cb).toHaveAttribute("aria-checked", "false");
  });

  // Отображается label
  it("label виден", () => {
    render(<Checkbox checked={false} label="Мой лейбл" />);
    expect(screen.getByText("Мой лейбл")).toBeInTheDocument();
  });
});
