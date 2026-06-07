/**
 * Тесты ChartCard — обёртка chart-карточки с tabs и meta-footer.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ChartCard, RangeTabs } from "@/components/data/charts/ChartCard";

// ─── RangeTabs ────────────────────────────────────────────────────────────────

describe("RangeTabs", () => {
  const ITEMS = [
    { value: "1h", label: "1h" },
    { value: "24h", label: "24h" },
    { value: "7d", label: "7d" },
  ];

  // Рендерит все табы
  it("рендерит все диапазоны", () => {
    render(<RangeTabs items={ITEMS} value="24h" onChange={() => {}} />);
    expect(screen.getByRole("tab", { name: "1h" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "24h" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "7d" })).toBeInTheDocument();
  });

  // Активный таб имеет aria-selected=true
  it("активный таб имеет aria-selected=true", () => {
    render(<RangeTabs items={ITEMS} value="24h" onChange={() => {}} />);
    expect(screen.getByRole("tab", { name: "24h" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "1h" })).toHaveAttribute("aria-selected", "false");
  });

  // Клик вызывает onChange с правильным значением
  it("клик по 7d вызывает onChange('7d')", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<RangeTabs items={ITEMS} value="24h" onChange={onChange} />);
    await user.click(screen.getByRole("tab", { name: "7d" }));
    expect(onChange).toHaveBeenCalledWith("7d");
  });
});

// ─── ChartCard ────────────────────────────────────────────────────────────────

describe("ChartCard", () => {
  // Рендерит eyebrow и title
  it("рендерит eyebrow и title", () => {
    render(
      <ChartCard eyebrow="02 Spend" title="Spend rate · last 24h">
        <div>chart body</div>
      </ChartCard>,
    );
    expect(screen.getByText("02 Spend")).toBeInTheDocument();
    expect(screen.getByText("Spend rate · last 24h")).toBeInTheDocument();
  });

  // Рендерит rangeControl в header
  it("рендерит rangeControl слот", () => {
    render(
      <ChartCard
        title="Test"
        rangeControl={<button>RangeCtrl</button>}
      >
        <div>body</div>
      </ChartCard>,
    );
    expect(screen.getByText("RangeCtrl")).toBeInTheDocument();
  });

  // Meta-footer рендерится при metaItems
  it("рендерит meta-footer с metaItems", () => {
    render(
      <ChartCard
        title="Test"
        metaItems={[
          { label: "total", value: "$4,872" },
          { label: "peak", value: "$487" },
        ]}
      >
        <div>body</div>
      </ChartCard>,
    );
    expect(screen.getByText("total")).toBeInTheDocument();
    expect(screen.getByText("$4,872")).toBeInTheDocument();
    expect(screen.getByText("peak")).toBeInTheDocument();
  });

  // Footer скрыт при пустом metaItems
  it("footer скрыт при пустом metaItems", () => {
    render(
      <ChartCard title="Test" metaItems={[]}>
        <div>body</div>
      </ChartCard>,
    );
    // Нет лейблов footer
    expect(screen.queryByText("total")).not.toBeInTheDocument();
  });

  // Рендерит children
  it("рендерит слот children", () => {
    render(
      <ChartCard title="T">
        <div data-testid="chart-body">inner</div>
      </ChartCard>,
    );
    expect(screen.getByTestId("chart-body")).toBeInTheDocument();
  });
});
