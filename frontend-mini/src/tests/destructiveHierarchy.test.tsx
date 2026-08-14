/**
 * Иерархия деструктивных действий в mini-оболочке.
 *
 *  - disabled — плоская заливка, а не opacity-40: приглушённый красный давал
 *    контраст 1.67:1 и делал заблокированную кнопку нечитаемой;
 *  - «Включить» возобновляет реальный спенд и не должен выглядеть как
 *    нейтральная secondary-утилита;
 *  - critical и warning различаются формой иконки, а не только цветом.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Button } from "@/components/ui/Button";

vi.mock("@/lib/tg", () => ({
  getInitData: () => "signed_init_data",
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgAlert: vi.fn(),
  tgConfirm: vi.fn(),
}));

import { MiniSeverityBadge } from "@/features/operator/OperatorAds";

describe("mini disabled contrast", () => {
  it("uses a flat fill instead of a dimmed danger colour", () => {
    render(
      <Button variant="danger" disabled>
        Отключить
      </Button>,
    );

    const button = screen.getByRole("button", { name: "Отключить" });
    expect(button).toBeDisabled();
    expect(button.className).not.toContain("opacity-40");
    expect(button.className).toContain("disabled:opacity-100");
    expect(button.className).toContain("disabled:bg-bg-2");
    expect(button.className).toContain("disabled:text-bg-8");
  });
});

describe("mini resume affordance", () => {
  it("keeps resume-spend visually apart from neutral utilities", () => {
    const { container: warning } = render(<Button variant="warning">Включить</Button>);
    const { container: secondary } = render(<Button variant="secondary">Обновить</Button>);

    const warningClass = warning.querySelector("button")!.className;
    const secondaryClass = secondary.querySelector("button")!.className;

    expect(warningClass).toContain("--color-warning");
    expect(warningClass).not.toBe(secondaryClass);
    expect(warningClass).not.toContain("--color-danger");
  });
});

describe("mini severity iconography", () => {
  it("gives critical its own shape, not just another colour", () => {
    const { container: warning } = render(<MiniSeverityBadge severity="warning" />);
    const { container: critical } = render(<MiniSeverityBadge severity="critical" />);

    const warningIcon = warning.querySelector("svg")?.getAttribute("class");
    const criticalIcon = critical.querySelector("svg")?.getAttribute("class");

    expect(warningIcon).toBeTruthy();
    expect(criticalIcon).toBeTruthy();
    expect(criticalIcon).not.toBe(warningIcon);
    expect(criticalIcon).toContain("octagon-alert");
  });
});
