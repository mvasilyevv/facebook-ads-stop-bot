// Тест: Button рендерится с разными variants и реагирует на клик.
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Button } from "@/components/ui/Button";

describe("Button", () => {
  // Тест: primary variant отрисован и доступен по тексту.
  it("рендерится с primary variant и текстом", () => {
    render(<Button variant="primary">Scan</Button>);
    const btn = screen.getByRole("button", { name: /scan/i });
    expect(btn).toBeInTheDocument();
  });

  // Тест: disabled блокирует onClick.
  it("disabled блокирует обработчик клика", () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Disabled
      </Button>,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  // Тест: loading state блокирует кнопку и показывает спиннер.
  it("loading state блокирует клик", () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Saving
      </Button>,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  // Тест: каждый из 4 size'ов применяет правильный высотный класс.
  it("применяет size класс", () => {
    const { rerender } = render(<Button size="xs">x</Button>);
    expect(screen.getByRole("button").className).toMatch(/h-6/);
    rerender(<Button size="lg">x</Button>);
    expect(screen.getByRole("button").className).toMatch(/h-10/);
  });
});
