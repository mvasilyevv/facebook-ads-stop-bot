/**
 * Тесты DensityToggle — переключатель плотности строк (оживляет useUiStore.density).
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach } from "vitest";
import { DensityToggle } from "@/components/ui/DensityToggle";
import { useUiStore } from "@/stores/ui";

describe("DensityToggle", () => {
  beforeEach(() => {
    // Сбрасываем store к дефолту между тестами (persist в localStorage).
    useUiStore.getState().setDensity("comfortable");
  });

  // Рендер: radiogroup с тремя сегментами, выбран текущий из store.
  it("рендерит три сегмента, активен текущий density", () => {
    render(<DensityToggle />);
    const group = screen.getByRole("radiogroup", { name: "Плотность строк" });
    expect(group).toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    expect(screen.getByRole("radio", { name: "Просторно" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  // Клик по сегменту меняет density в store и aria-checked.
  it("клик по «Плотно» переключает store на dense", async () => {
    const user = userEvent.setup();
    render(<DensityToggle />);

    await user.click(screen.getByRole("radio", { name: "Плотно" }));

    expect(useUiStore.getState().density).toBe("dense");
    expect(screen.getByRole("radio", { name: "Плотно" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    // applyDensity выставил атрибут на <html> — tokens.css подхватит --row-h.
    expect(document.documentElement.getAttribute("data-density")).toBe("dense");
  });
});
