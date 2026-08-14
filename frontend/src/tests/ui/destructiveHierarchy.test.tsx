/**
 * Иерархия деструктивных действий в web-оболочке.
 *
 * Три инварианта, каждый из которых раньше нарушался:
 *  - у danger плотная рамка цвета статуса, а не полупрозрачная (была почти
 *    невидима на тёмном фоне и не отличала кнопку от нейтральной);
 *  - disabled — плоская заливка, а не opacity-40 (контраст 1.67:1 у danger
 *    делал заблокированный confirm в ConfirmDialog нечитаемым);
 *  - critical и warning различаются формой иконки, а не только цветом.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button, buttonStyles } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { OperatorSeverityBadge } from "@/features/operator/OperatorAds";

describe("danger affordance", () => {
  it("draws a dense status-coloured border", () => {
    const danger = buttonStyles({ variant: "danger" });
    expect(danger).toContain("border-danger");
    expect(danger).not.toContain("rgba(199,98,92,0.3)");
  });

  it("keeps resume-spend visually apart from neutral utilities", () => {
    const warning = buttonStyles({ variant: "warning" });
    const secondary = buttonStyles({ variant: "secondary" });
    expect(warning).toContain("border-warning");
    expect(warning).toContain("text-warning");
    expect(warning).not.toBe(secondary);
    // Возобновление спенда не должно выглядеть как деструктив.
    expect(warning).not.toContain("border-danger");
  });
});

describe("disabled contrast", () => {
  it("uses a flat fill instead of a dimmed danger colour", () => {
    const base = buttonStyles({ variant: "danger" });
    expect(base).not.toContain("disabled:opacity-40");
    expect(base).toContain("disabled:opacity-100");
    expect(base).toContain("disabled:bg-bg-2");
    expect(base).toContain("disabled:text-bg-8");
  });

  it("keeps the blocked ConfirmDialog button readable", () => {
    render(
      <ConfirmDialog
        open
        onOpenChange={() => {}}
        title="Отключить объявление?"
        description="Команда будет поставлена в очередь."
        confirmWord="ОТКЛЮЧИТЬ"
        confirmLabel="Отключить"
        onConfirm={() => {}}
      />,
    );

    const confirm = screen.getByRole("button", { name: "Отключить" });
    expect(confirm).toBeDisabled();
    expect(confirm.className).toContain("disabled:opacity-100");
    expect(confirm.className).not.toContain("disabled:opacity-40");
  });
});

describe("severity iconography", () => {
  it("gives critical its own shape, not just another colour", () => {
    const { container: warning } = render(<OperatorSeverityBadge severity="warning" />);
    const { container: critical } = render(<OperatorSeverityBadge severity="critical" />);

    const warningIcon = warning.querySelector("svg")?.getAttribute("class");
    const criticalIcon = critical.querySelector("svg")?.getAttribute("class");

    expect(warningIcon).toBeTruthy();
    expect(criticalIcon).toBeTruthy();
    expect(criticalIcon).not.toBe(warningIcon);
    expect(criticalIcon).toContain("octagon-alert");
  });
});

describe("button variant smoke", () => {
  it("renders the warning variant without dropping the label", () => {
    render(<Button variant="warning">Включить</Button>);
    expect(screen.getByRole("button", { name: "Включить" })).toBeInTheDocument();
  });
});
