/**
 * Тесты Tabs — keyboard navigation + варианты underline/segmented.
 * Radix Tabs обрабатывает Arrow Left/Right встроенно.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { Tabs, TabsList, TabsContent, type TabItem } from "@/components/ui/Tabs";

const ITEMS: TabItem[] = [
  { value: "a", label: "Alpha" },
  { value: "b", label: "Beta" },
  { value: "c", label: "Gamma", disabled: true },
];

// Минимальный wrapper с управляемым состоянием
function ControlledTabs({ variant = "underline" as "underline" | "segmented" }) {
  const [tab, setTab] = __useState("a");
  return (
    <Tabs value={tab} onValueChange={setTab} variant={variant}>
      <TabsList items={ITEMS} variant={variant} />
      <TabsContent value="a">Content A</TabsContent>
      <TabsContent value="b">Content B</TabsContent>
    </Tabs>
  );
}

// Импорт useState для использования внутри теста
import { useState as __useState } from "react";

describe("Tabs", () => {
  // Рендерит список вкладок
  it("рендерит все вкладки", () => {
    render(<ControlledTabs />);
    expect(screen.getByRole("tab", { name: "Alpha" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Beta" })).toBeInTheDocument();
  });

  // Активная вкладка имеет aria-selected
  it("первая вкладка активна по умолчанию", () => {
    render(<ControlledTabs />);
    expect(screen.getByRole("tab", { name: "Alpha" })).toHaveAttribute("aria-selected", "true");
  });

  // Disabled вкладка
  it("disabled вкладка имеет aria-disabled", () => {
    render(<ControlledTabs />);
    expect(screen.getByRole("tab", { name: "Gamma" })).toBeDisabled();
  });

  // Клик переключает вкладку
  it("клик по Beta → content B", async () => {
    const user = userEvent.setup();
    render(<ControlledTabs />);
    await user.click(screen.getByRole("tab", { name: "Beta" }));
    expect(screen.getByText("Content B")).toBeVisible();
  });

  // Keyboard navigation: ArrowRight переходит на следующую вкладку
  it("ArrowRight переключает на следующую вкладку", async () => {
    const user = userEvent.setup();
    render(<ControlledTabs />);
    // Фокус на первой вкладке
    screen.getByRole("tab", { name: "Alpha" }).focus();
    await user.keyboard("{ArrowRight}");
    // Beta должна стать активной (Radix фокусирует её)
    expect(screen.getByRole("tab", { name: "Beta" })).toHaveFocus();
  });

  // onValueChange вызывается при клике
  it("onValueChange вызывается с новым значением", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <Tabs value="a" onValueChange={onChange}>
        <TabsList
          items={[
            { value: "a", label: "A" },
            { value: "b", label: "B" },
          ]}
        />
      </Tabs>,
    );
    await user.click(screen.getByRole("tab", { name: "B" }));
    expect(onChange).toHaveBeenCalledWith("b");
  });

  // Segmented вариант рендерится
  it("segmented вариант рендерится без ошибок", () => {
    render(<ControlledTabs variant="segmented" />);
    expect(screen.getByRole("tablist")).toBeInTheDocument();
  });
});
