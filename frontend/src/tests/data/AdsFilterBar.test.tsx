/**
 * Тесты AdsFilterBar — search, state-pills, selects, chips, density.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import {
  AdsFilterBar,
  type AdsFilterState,
} from "@/components/domain/ads/AdsFilterBar";
import type { AlertState } from "@fb/shared";

// ─── Обёртка с управляемым состоянием ─────────────────────────────────────

function FilterBarWrapper({
  onSearchChange = vi.fn(),
  onStateToggle = vi.fn(),
  onOfferChange = vi.fn(),
  onCountryChange = vi.fn(),
  onClearAll = vi.fn(),
  initial,
}: {
  onSearchChange?: (v: string) => void;
  onStateToggle?: (state: AlertState) => void;
  onOfferChange?: (v: string) => void;
  onCountryChange?: (v: string) => void;
  onClearAll?: () => void;
  initial?: Partial<AdsFilterState>;
}) {
  const [filterState, setFilterState] = useState<AdsFilterState>({
    search: initial?.search ?? "",
    selectedStates: initial?.selectedStates ?? new Set(),
    selectedOffer: initial?.selectedOffer ?? "",
    selectedCountry: initial?.selectedCountry ?? "",
  });

  return (
    <AdsFilterBar
      filterState={filterState}
      offerOptions={[
        { value: "CR2", label: "CR2" },
        { value: "DRC", label: "DRC" },
      ]}
      countryOptions={[
        { value: "GH", label: "GH" },
        { value: "NG", label: "NG" },
      ]}
      onSearchChange={(v) => {
        setFilterState((s) => ({ ...s, search: v }));
        onSearchChange(v);
      }}
      onStateToggle={(state) => {
        setFilterState((s) => {
          const next = new Set(s.selectedStates);
          if (next.has(state)) next.delete(state);
          else next.add(state);
          return { ...s, selectedStates: next };
        });
        onStateToggle(state);
      }}
      onOfferChange={(v) => {
        setFilterState((s) => ({ ...s, selectedOffer: v }));
        onOfferChange(v);
      }}
      onCountryChange={(v) => {
        setFilterState((s) => ({ ...s, selectedCountry: v }));
        onCountryChange(v);
      }}
      onClearAll={() => {
        setFilterState({
          search: "",
          selectedStates: new Set(),
          selectedOffer: "",
          selectedCountry: "",
        });
        onClearAll();
      }}
    />
  );
}

// ─── Тесты ────────────────────────────────────────────────────────────────

describe("AdsFilterBar", () => {
  // Рендерится без ошибок
  it("рендерится без ошибок", () => {
    render(<FilterBarWrapper />);
    expect(screen.getByLabelText("Поиск объявлений")).toBeInTheDocument();
  });

  // SearchInput: ввод текста эмитит onSearchChange
  it("ввод в поиск вызывает onSearchChange", async () => {
    const user = userEvent.setup();
    const onSearchChange = vi.fn();
    render(<FilterBarWrapper onSearchChange={onSearchChange} />);

    const input = screen.getByLabelText("Поиск объявлений");
    await user.type(input, "tyver");

    // Последнее значение = введённая строка
    expect(onSearchChange).toHaveBeenLastCalledWith("tyver");
  });

  // Кнопка state-pill вызывает onStateToggle с правильным state
  it("клик по state-pill вызывает onStateToggle(state)", async () => {
    const user = userEvent.setup();
    const onStateToggle = vi.fn();
    render(<FilterBarWrapper onStateToggle={onStateToggle} />);

    const warningPill = screen.getByText("Предупреждение");
    await user.click(warningPill);
    expect(onStateToggle).toHaveBeenCalledWith("warning_sent");
  });

  // Активный state-pill имеет aria-pressed=true
  it("активный state-pill имеет aria-pressed=true", () => {
    render(
      <FilterBarWrapper
        initial={{ selectedStates: new Set<AlertState>(["stop_sent"]) }}
      />,
    );
    // Находим кнопку "Стоп" (label для stop_sent)
    const stopPill = screen.getByText("Стоп").closest("button");
    expect(stopPill).toHaveAttribute("aria-pressed", "true");
  });

  // Select по офферу эмитит onOfferChange
  it("выбор оффера в select вызывает onOfferChange", async () => {
    const user = userEvent.setup();
    const onOfferChange = vi.fn();
    render(<FilterBarWrapper onOfferChange={onOfferChange} />);

    const select = screen.getByLabelText("Фильтр по офферу");
    await user.selectOptions(select, "CR2");
    expect(onOfferChange).toHaveBeenCalledWith("CR2");
  });

  // Select по стране эмитит onCountryChange
  it("выбор страны в select вызывает onCountryChange", async () => {
    const user = userEvent.setup();
    const onCountryChange = vi.fn();
    render(<FilterBarWrapper onCountryChange={onCountryChange} />);

    const select = screen.getByLabelText("Фильтр по стране");
    await user.selectOptions(select, "GH");
    expect(onCountryChange).toHaveBeenCalledWith("GH");
  });

  // Active chips показываются при активных фильтрах
  it("chips показываются при активных фильтрах", () => {
    render(
      <FilterBarWrapper
        initial={{
          selectedStates: new Set<AlertState>(["warning_sent"]),
          selectedOffer: "DRC",
        }}
      />,
    );
    // Должен быть chip со статусом
    expect(screen.getByText(/статус: Предупреждение/)).toBeInTheDocument();
    // И chip с оффером
    expect(screen.getByText(/оффер: DRC/)).toBeInTheDocument();
  });

  // Chip × удаляет фильтр
  it("клик на × в chip вызывает сброс конкретного фильтра", async () => {
    const user = userEvent.setup();
    const onOfferChange = vi.fn();
    render(
      <FilterBarWrapper
        initial={{ selectedOffer: "CR2" }}
        onOfferChange={onOfferChange}
      />,
    );

    // Находим кнопку × рядом с chip оффера
    const chipText = screen.getByText(/оффер: CR2/);
    const chip = chipText.closest("span");
    const removeBtn = chip?.querySelector("button[aria-label='Удалить']");
    expect(removeBtn).toBeTruthy();
    await user.click(removeBtn!);

    expect(onOfferChange).toHaveBeenCalledWith("");
  });

  // "Сбросить всё" вызывает onClearAll
  it("кнопка «Сбросить всё» вызывает onClearAll", async () => {
    const user = userEvent.setup();
    const onClearAll = vi.fn();
    render(
      <FilterBarWrapper
        initial={{ selectedOffer: "CR2", search: "tyver" }}
        onClearAll={onClearAll}
      />,
    );

    const clearBtn = screen.getByLabelText("Сбросить все фильтры");
    await user.click(clearBtn);
    expect(onClearAll).toHaveBeenCalledTimes(1);
  });

  // Chips не показываются без активных фильтров
  it("chips не показываются без активных фильтров", () => {
    render(<FilterBarWrapper />);
    expect(screen.queryByText(/статус:/)).not.toBeInTheDocument();
    expect(screen.queryByText("Активно")).not.toBeInTheDocument();
  });

  // Density toggle эмитит через useUiStore — просто проверяем что кнопка есть
  it("density toggle button присутствует", () => {
    render(<FilterBarWrapper />);
    const densityBtn = screen.getByLabelText(/Плотность/);
    expect(densityBtn).toBeInTheDocument();
  });
});
