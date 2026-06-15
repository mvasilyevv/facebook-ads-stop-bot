/**
 * Тесты FilterBar (канон ads-web.jsx) — search, state-pills, offer-dropdown, chips.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { useRef, useState } from "react";
import { FilterBar, type AdsFilterState } from "@/components/domain/ads/FilterBar";
import type { AlertState } from "@fb/shared";

// ─── Управляемая обёртка ──────────────────────────────────────────────────────

function Wrapper({
  onSearchChange = vi.fn(),
  onStateToggle = vi.fn(),
  onOfferToggle = vi.fn(),
  onAccountToggle = vi.fn(),
  onClearAll = vi.fn(),
  initial,
  accountOptions = [],
}: {
  onSearchChange?: (v: string) => void;
  onStateToggle?: (s: AlertState) => void;
  onOfferToggle?: (o: string) => void;
  onAccountToggle?: (a: string) => void;
  onClearAll?: () => void;
  initial?: Partial<AdsFilterState>;
  /** Мульти-кабинет: ≤1 — dropdown кабинета скрыт. */
  accountOptions?: string[];
}) {
  const searchRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<AdsFilterState>({
    search: initial?.search ?? "",
    selectedStates: initial?.selectedStates ?? new Set(),
    selectedOffers: initial?.selectedOffers ?? new Set(),
    selectedAccounts: initial?.selectedAccounts ?? new Set(),
  });

  return (
    <FilterBar
      filterState={state}
      offerOptions={["CR2", "DRC"]}
      accountOptions={accountOptions}
      count={42}
      searchRef={searchRef}
      onSearchChange={(v) => {
        setState((s) => ({ ...s, search: v }));
        onSearchChange(v);
      }}
      onStateToggle={(st) => {
        setState((s) => {
          const next = new Set(s.selectedStates);
          if (next.has(st)) next.delete(st);
          else next.add(st);
          return { ...s, selectedStates: next };
        });
        onStateToggle(st);
      }}
      onOfferToggle={(o) => {
        setState((s) => {
          const next = new Set(s.selectedOffers);
          if (next.has(o)) next.delete(o);
          else next.add(o);
          return { ...s, selectedOffers: next };
        });
        onOfferToggle(o);
      }}
      onAccountToggle={(a) => {
        setState((s) => {
          const next = new Set(s.selectedAccounts);
          if (next.has(a)) next.delete(a);
          else next.add(a);
          return { ...s, selectedAccounts: next };
        });
        onAccountToggle(a);
      }}
      onClearAll={() => {
        setState({
          search: "",
          selectedStates: new Set(),
          selectedOffers: new Set(),
          selectedAccounts: new Set(),
        });
        onClearAll();
      }}
    />
  );
}

// ─── Тесты ────────────────────────────────────────────────────────────────────

describe("FilterBar", () => {
  // Рендер + count.
  it("рендерит поиск и счётчик", () => {
    render(<Wrapper />);
    expect(screen.getByLabelText("Поиск объявлений")).toBeInTheDocument();
    expect(screen.getByText(/42 объявлений/)).toBeInTheDocument();
  });

  // Поиск эмитит onSearchChange.
  it("ввод в поиск вызывает onSearchChange", async () => {
    const user = userEvent.setup();
    const onSearchChange = vi.fn();
    render(<Wrapper onSearchChange={onSearchChange} />);
    await user.type(screen.getByLabelText("Поиск объявлений"), "tyver");
    expect(onSearchChange).toHaveBeenLastCalledWith("tyver");
  });

  // State-pill вызывает onStateToggle с правильным значением.
  it("клик по state-pill вызывает onStateToggle(state)", async () => {
    const user = userEvent.setup();
    const onStateToggle = vi.fn();
    render(<Wrapper onStateToggle={onStateToggle} />);
    await user.click(screen.getByText("Предупреждение"));
    expect(onStateToggle).toHaveBeenCalledWith("warning_sent");
  });

  // Активный pill: aria-pressed=true.
  it("активный state-pill имеет aria-pressed=true", () => {
    render(<Wrapper initial={{ selectedStates: new Set<AlertState>(["stop_sent"]) }} />);
    expect(screen.getByText("Стоп").closest("button")).toHaveAttribute("aria-pressed", "true");
  });

  // Offer-dropdown: открытие + выбор → onOfferToggle.
  it("offer-dropdown открывается и выбор вызывает onOfferToggle", async () => {
    const user = userEvent.setup();
    const onOfferToggle = vi.fn();
    render(<Wrapper onOfferToggle={onOfferToggle} />);

    await user.click(screen.getByLabelText("Фильтр по офферу"));
    // Опции появились.
    const option = screen.getByRole("option", { name: "CR2" });
    await user.click(option);
    expect(onOfferToggle).toHaveBeenCalledWith("CR2");
  });

  // Chips показываются при активных фильтрах.
  it("chips показываются при активных state/offer фильтрах", () => {
    render(
      <Wrapper
        initial={{
          selectedStates: new Set<AlertState>(["warning_sent"]),
          selectedOffers: new Set(["DRC"]),
        }}
      />,
    );
    expect(screen.getByText(/state = Предупреждение/)).toBeInTheDocument();
    expect(screen.getByText(/offer = DRC/)).toBeInTheDocument();
  });

  // «Сбросить всё» вызывает onClearAll.
  it("кнопка «Сбросить всё» вызывает onClearAll", async () => {
    const user = userEvent.setup();
    const onClearAll = vi.fn();
    render(<Wrapper initial={{ selectedOffers: new Set(["CR2"]) }} onClearAll={onClearAll} />);
    await user.click(screen.getByLabelText("Сбросить все фильтры"));
    expect(onClearAll).toHaveBeenCalledTimes(1);
  });

  // Без активных фильтров chips не рендерятся.
  it("chips не показываются без активных фильтров", () => {
    render(<Wrapper />);
    expect(screen.queryByText(/state =/)).not.toBeInTheDocument();
    expect(screen.queryByText(/offer =/)).not.toBeInTheDocument();
  });

  // Мульти-кабинет: dropdown скрыт при ≤1 кабинете (нечего фильтровать).
  it("dropdown кабинета скрыт при одном кабинете", () => {
    render(<Wrapper accountOptions={["111"]} />);
    expect(screen.queryByLabelText("Фильтр по кабинету")).not.toBeInTheDocument();
  });

  // Мульти-кабинет: при нескольких кабинетах — dropdown работает, выбор даёт chip.
  it("dropdown кабинета: выбор эмитит onAccountToggle и показывает chip", async () => {
    const user = userEvent.setup();
    const onAccountToggle = vi.fn();
    render(<Wrapper accountOptions={["111", "222"]} onAccountToggle={onAccountToggle} />);

    await user.click(screen.getByLabelText("Фильтр по кабинету"));
    await user.click(screen.getByText("222"));

    expect(onAccountToggle).toHaveBeenCalledWith("222");
    expect(screen.getByText(/кабинет = 222/)).toBeInTheDocument();
  });
});
