/**
 * Тесты Drawer — Esc закрывает, focus-trap, overlay.
 * Radix Dialog обеспечивает Esc и focus-trap автоматически.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { Drawer } from "@/components/ui/Drawer";

function OpenDrawer({ onOpenChange = vi.fn() }: { onOpenChange?: (v: boolean) => void }) {
  return (
    <Drawer open title="Тест Drawer" onOpenChange={onOpenChange}>
      <p>Содержимое</p>
      <button>Кнопка внутри</button>
    </Drawer>
  );
}

describe("Drawer", () => {
  // Drawer открыт — контент виден
  it("открытый drawer — title виден", () => {
    render(<OpenDrawer />);
    expect(screen.getByText("Тест Drawer")).toBeInTheDocument();
  });

  // Drawer закрыт — контент не рендерится (Radix Portal)
  it("closed drawer — контент не рендерится", () => {
    render(
      <Drawer open={false} title="Скрытый" onOpenChange={vi.fn()}>
        <p>Скрытое содержимое</p>
      </Drawer>,
    );
    expect(screen.queryByText("Скрытый")).not.toBeInTheDocument();
  });

  // Кнопка × вызывает onOpenChange(false)
  it("кнопка Закрыть вызывает onOpenChange(false)", async () => {
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(<OpenDrawer onOpenChange={onOpenChange} />);
    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  // Esc закрывает drawer
  it("Esc вызывает onOpenChange(false)", async () => {
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(<OpenDrawer onOpenChange={onOpenChange} />);
    await user.keyboard("{Escape}");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  // Eyebrow отображается
  it("eyebrow виден", () => {
    render(
      <Drawer open title="T" eyebrow="06 · DETAIL" onOpenChange={vi.fn()}>
        children
      </Drawer>,
    );
    expect(screen.getByText("06 · DETAIL")).toBeInTheDocument();
  });

  // Footer рендерится
  it("footer виден", () => {
    render(
      <Drawer open title="T" footer={<span>Footer content</span>} onOpenChange={vi.fn()}>
        body
      </Drawer>,
    );
    expect(screen.getByText("Footer content")).toBeInTheDocument();
  });

  it("не выходит за границы viewport шириной 360px", () => {
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 360,
    });

    try {
      render(
        <Drawer open title="Мобильная панель" width={640} onOpenChange={vi.fn()}>
          body
        </Drawer>,
      );

      expect(screen.getByRole("dialog", { name: "Мобильная панель" })).toHaveStyle({
        width: "100%",
        maxWidth: "640px",
      });
    } finally {
      Object.defineProperty(window, "innerWidth", {
        configurable: true,
        value: originalInnerWidth,
      });
    }
  });

  // Focus-trap: первый фокусируемый элемент получает фокус при открытии
  it("при открытии фокус перемещается в drawer", async () => {
    void userEvent.setup();
    const { unmount } = render(<OpenDrawer />);
    // Radix Dialog перемещает фокус на первый интерактивный элемент
    // проверяем что фокус НЕ остался снаружи
    expect(document.activeElement).not.toBe(document.body);
    unmount();
  });
});
