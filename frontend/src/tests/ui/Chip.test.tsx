/**
 * Chip — инлайновый тэг внутри поля ввода (TagListInput, строка фильтров).
 * Размер держим по контенту: чип и его × не растягиваются до 44px-таргета,
 * иначе одно значение занимает пол-строки формы.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Chip } from "@/components/ui/Pill";

describe("Chip", () => {
  it("кнопка × компактна, а не 44px-таргет", () => {
    render(<Chip onRemove={() => undefined}>1234567890123456</Chip>);

    const remove = screen.getByRole("button", { name: "Удалить" });
    expect(remove).toHaveClass("size-[18px]");
    expect(remove).not.toHaveClass("size-11");
  });

  it("высота чипа идёт по контенту", () => {
    render(<Chip onRemove={() => undefined}>GH_AVI</Chip>);

    expect(screen.getByText("GH_AVI")).not.toHaveClass("min-h-11");
  });
});
