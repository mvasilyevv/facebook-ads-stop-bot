/**
 * Тесты ReasonBlock — блок AI reasoning с цитатой.
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ReasonBlock } from "@/components/domain/drafts/ReasonBlock";

describe("ReasonBlock", () => {
  // Рендер базового текста
  it("показывает текст обоснования", () => {
    render(
      <ReasonBlock
        text="Spend $891.23 with CPL $42.10 — over the $20 threshold by 2.1×."
      />,
    );
    expect(
      screen.getByText(/Spend \$891\.23 with CPL/),
    ).toBeInTheDocument();
  });

  // Eyebrow "AI reasoning"
  it("показывает eyebrow 'AI reasoning'", () => {
    render(<ReasonBlock text="Test reason" />);
    expect(screen.getByText("AI reasoning")).toBeInTheDocument();
  });

  // Source модели отображается
  it("показывает source модели при передаче", () => {
    render(<ReasonBlock text="Test reason" source="claude-opus-4-7" />);
    expect(screen.getByText("claude-opus-4-7")).toBeInTheDocument();
  });

  // Без source — компонент не падает
  it("рендерит без source без ошибок", () => {
    expect(() => render(<ReasonBlock text="Test" source={null} />)).not.toThrow();
  });

  // Акцентный левый border — через className (не UI-утверждение, проверяем наличие элемента)
  it("присутствует div с border-l-2 (accent left border)", () => {
    const { container } = render(<ReasonBlock text="Test" />);
    const el = container.firstChild as HTMLElement;
    expect(el.className).toContain("border-l-2");
    expect(el.className).toContain("border-accent");
  });
});
