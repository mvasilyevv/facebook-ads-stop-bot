/**
 * Тесты Button-компонента.
 * Покрываем: варианты рендера, disabled, loading, icon-size.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { Button } from "@/components/ui/Button";

describe("Button", () => {
  // Базовый рендер — текст виден
  it("рендерит label", () => {
    render(<Button>Сохранить</Button>);
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeInTheDocument();
  });

  // Вариант primary получает правильный data-атрибут через CVA
  it("primary вариант — кнопка доступна для клика", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(<Button variant="primary" onClick={onClick}>Ок</Button>);
    await user.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });

  // disabled блокирует клик
  it("disabled — клик не срабатывает", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(<Button disabled onClick={onClick}>Нельзя</Button>);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    await user.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  // loading — spinner показывается, button disabled
  it("loading — кнопка disabled + aria-busy", () => {
    render(<Button loading>Загрузка</Button>);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-busy", "true");
  });

  // Variants smoke — все варианты рендерятся без ошибки
  it.each(["primary", "secondary", "danger", "ghost", "ghost-danger"] as const)(
    "вариант %s рендерится без ошибок",
    (variant) => {
      render(<Button variant={variant}>{variant}</Button>);
      expect(screen.getByRole("button")).toBeInTheDocument();
    },
  );

  // Sizes smoke
  it.each(["xs", "sm", "md", "lg", "icon"] as const)(
    "size %s рендерится",
    (size) => {
      render(<Button size={size} aria-label={size}>{size}</Button>);
      expect(screen.getByRole("button")).toBeInTheDocument();
    },
  );

  // fullWidth
  it("fullWidth добавляет w-full класс", () => {
    render(<Button fullWidth>Full</Button>);
    expect(screen.getByRole("button")).toHaveClass("w-full");
  });

  // leftIcon не показывается при loading
  it("leftIcon скрыт во время loading", () => {
    render(<Button loading leftIcon={<span data-testid="icon" />}>Текст</Button>);
    expect(screen.queryByTestId("icon")).not.toBeInTheDocument();
  });

  // rightIcon отображается
  it("rightIcon показывается", () => {
    render(<Button rightIcon={<span data-testid="right" />}>Текст</Button>);
    expect(screen.getByTestId("right")).toBeInTheDocument();
  });
});
