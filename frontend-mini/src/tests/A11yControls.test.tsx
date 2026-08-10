import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Sheet } from "@/components/ui/Sheet";
import { Button } from "@/components/ui/Button";
import { Slider } from "@/components/ui/Slider";

describe("TMA form error semantics", () => {
  it("associates input errors with the invalid control", () => {
    render(<Input label="Кабинеты" errorMessage="Укажите кабинет" />);

    const input = screen.getByLabelText("Кабинеты");
    const alert = screen.getByRole("alert");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", alert.id);
  });

  it("associates select errors with the invalid control", () => {
    render(
      <Select
        label="Оффер"
        options={[{ value: "", label: "Выберите оффер" }]}
        errorMessage="Оффер обязателен"
      />,
    );

    const select = screen.getByLabelText("Оффер");
    const alert = screen.getByRole("alert");
    expect(select).toHaveAttribute("aria-invalid", "true");
    expect(select).toHaveAttribute("aria-describedby", alert.id);
  });

  it("keeps portal sheets inside Telegram content safe areas", () => {
    render(
      <Sheet open onClose={() => {}} title="Порог">
        Настройки порога
      </Sheet>,
    );

    const sheet = screen.getByRole("dialog", { name: "Порог" });
    expect(sheet.className).toContain(
      "left-[max(var(--tg-content-safe-left,0px),env(safe-area-inset-left))]",
    );
    expect(sheet.className).toContain(
      "right-[max(var(--tg-content-safe-right,0px),env(safe-area-inset-right))]",
    );
    expect(sheet.className).toContain(
      "max-h-[calc(var(--tg-viewport-stable-height,100dvh)-max(var(--tg-content-safe-top,0px),env(safe-area-inset-top)))]",
    );
    expect(sheet.className).toContain(
      "pb-[max(16px,var(--tg-content-safe-bottom,0px),env(safe-area-inset-bottom))]",
    );
  });

  it("exposes loading state and disables duplicate button activation", () => {
    render(<Button loading>Сохранить</Button>);

    const button = screen.getByRole("button", { name: "Сохранить" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toHaveAttribute("data-loading", "true");
  });

  it("keeps the native range control at the 44px touch-target height", () => {
    render(<Slider label="Порог" value={50} onChange={() => {}} />);

    expect(screen.getByRole("slider", { name: "Порог" })).toHaveClass("h-11");
  });
});
