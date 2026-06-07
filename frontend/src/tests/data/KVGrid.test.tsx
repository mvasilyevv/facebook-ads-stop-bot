/**
 * Тесты KVGrid — value-states классы, sub-line, рендер items.
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { KVGrid } from "@/components/data/timeline/KVGrid";
import type { KVItem } from "@/components/data/timeline/KVGrid";

const BASE_ITEMS: KVItem[] = [
  { label: "Spend",     value: "$891.23", state: "bad",     sub: "+ $42 last hour" },
  { label: "CPL",       value: "$42.10",  state: "bad",     sub: "threshold $20 — over" },
  { label: "Leads",     value: "21",      state: "default" },
  { label: "Frequency", value: "4.8",     state: "warn",    sub: "over threshold 4.0" },
];

describe("KVGrid", () => {
  // Рендерит все лейблы
  it("рендерит все лейблы", () => {
    render(<KVGrid items={BASE_ITEMS} />);
    expect(screen.getByText("Spend")).toBeInTheDocument();
    expect(screen.getByText("CPL")).toBeInTheDocument();
    expect(screen.getByText("Leads")).toBeInTheDocument();
    expect(screen.getByText("Frequency")).toBeInTheDocument();
  });

  // Рендерит все значения
  it("рендерит значения", () => {
    render(<KVGrid items={BASE_ITEMS} />);
    expect(screen.getByText("$891.23")).toBeInTheDocument();
    expect(screen.getByText("21")).toBeInTheDocument();
  });

  // state=bad — применяет text-danger
  it("state=bad применяет text-danger класс", () => {
    render(<KVGrid items={[{ label: "CPL", value: "$42.10", state: "bad" }]} />);
    const val = screen.getByText("$42.10");
    expect(val.className).toContain("text-danger");
  });

  // state=warn — применяет text-warning
  it("state=warn применяет text-warning класс", () => {
    render(<KVGrid items={[{ label: "Freq", value: "4.8", state: "warn" }]} />);
    const val = screen.getByText("4.8");
    expect(val.className).toContain("text-warning");
  });

  // state=default — применяет text-bg-11
  it("state=default применяет text-bg-11 класс", () => {
    render(<KVGrid items={[{ label: "Leads", value: "21", state: "default" }]} />);
    const val = screen.getByText("21");
    expect(val.className).toContain("text-bg-11");
  });

  // Sub-строка рендерится
  it("рендерит sub-строку", () => {
    render(<KVGrid items={BASE_ITEMS} />);
    expect(screen.getByText("+ $42 last hour")).toBeInTheDocument();
    expect(screen.getByText("over threshold 4.0")).toBeInTheDocument();
  });

  // Без sub — нет лишних элементов
  it("item без sub не рендерит sub-строку", () => {
    render(<KVGrid items={[{ label: "Leads", value: "21" }]} />);
    // Только лейбл и значение — нет третьего потомка
    const container = screen.getByText("Leads").closest("div");
    expect(container?.querySelectorAll("span")).toHaveLength(2);
  });

  // Пустой список — рендерится пустой grid
  it("пустой items — не падает", () => {
    expect(() => {
      render(<KVGrid items={[]} />);
    }).not.toThrow();
  });
});
