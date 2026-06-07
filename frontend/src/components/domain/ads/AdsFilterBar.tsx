/**
 * AdsFilterBar — полная строка фильтров для Ads-страницы.
 *
 * Блоки (слева → вправо):
 *   1. SearchInput — поиск по имени/id
 *   2. Pill-кнопки state-фильтров (FSM-состояния)
 *   3. Select: Offer
 *   4. Select: Country
 *   5. Разделитель
 *   6. Density toggle (compact/comfortable)
 *   7. Active-filter chips под строкой (если есть активные)
 *
 * Controlled: всё состояние фильтров живёт снаружи (в странице).
 * Компонент только эмитит изменения через коллбэки.
 */

import { type ReactNode } from "react";
import { X } from "lucide-react";
import { SearchInput } from "@/components/ui/Input";
import { FilterPill, Chip } from "@/components/ui/Pill";
import { Select, type SelectOption } from "@/components/ui/Select";
import { cn } from "@/lib/utils/cn";
import { useUiStore, type Density } from "@/stores/ui";
import {
  ALERT_STATE_LABELS,
  type AlertState,
} from "@fb/shared";

// ─── Публичный API ───────────────────────────────────────────────────────────

export interface AdsFilterState {
  /** Строка поиска (по имени/id). */
  search: string;
  /** Множество выбранных alert_state фильтров. Пустое = все. */
  selectedStates: Set<AlertState>;
  /** Код выбранного оффера. Пустая строка = все. */
  selectedOffer: string;
  /** Выбранный код страны. Пустая строка = все. */
  selectedCountry: string;
}

export interface AdsFilterBarProps {
  /** Текущее состояние фильтров. */
  filterState: AdsFilterState;

  /** Список доступных offer-кодов для select. */
  offerOptions: SelectOption[];
  /** Список доступных country-кодов для select. */
  countryOptions: SelectOption[];

  /** Коллбэки изменения */
  onSearchChange: (v: string) => void;
  onStateToggle: (state: AlertState) => void;
  onOfferChange: (v: string) => void;
  onCountryChange: (v: string) => void;
  /** Сбросить все фильтры сразу. */
  onClearAll: () => void;

  className?: string;
}

// FSM-состояния в том порядке, что в макете
const STATE_OPTIONS: Array<{ value: AlertState; label: string }> = [
  { value: "normal", label: ALERT_STATE_LABELS.normal },
  { value: "warning_sent", label: ALERT_STATE_LABELS.warning_sent },
  { value: "stop_sent", label: ALERT_STATE_LABELS.stop_sent },
  { value: "claimed", label: ALERT_STATE_LABELS.claimed },
  { value: "disabled", label: ALERT_STATE_LABELS.disabled },
];

// ─── Компонент ───────────────────────────────────────────────────────────────

export function AdsFilterBar({
  filterState,
  offerOptions,
  countryOptions,
  onSearchChange,
  onStateToggle,
  onOfferChange,
  onCountryChange,
  onClearAll,
  className,
}: AdsFilterBarProps) {
  const { search, selectedStates, selectedOffer, selectedCountry } = filterState;
  const { density, toggleDensity } = useUiStore();

  // Активных фильтров-чипов нет — не рендерим строку chips
  const hasActiveFilters =
    selectedStates.size > 0 ||
    selectedOffer !== "" ||
    selectedCountry !== "" ||
    search !== "";

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {/* ── Строка фильтров ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 flex-wrap p-3 bg-bg-1 border border-bg-5">
        {/* Поиск */}
        <div className="flex-1 min-w-[160px]">
          <SearchInput
            placeholder="Поиск по имени или id…"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            aria-label="Поиск объявлений"
            size="md"
          />
        </div>

        {/* State pills */}
        <div className="flex gap-1 flex-wrap" role="group" aria-label="Фильтр по состоянию">
          {STATE_OPTIONS.map((opt) => (
            <FilterPill
              key={opt.value}
              active={selectedStates.has(opt.value)}
              onClick={() => onStateToggle(opt.value)}
              aria-pressed={selectedStates.has(opt.value)}
            >
              {opt.label}
            </FilterPill>
          ))}
        </div>

        {/* Offer select */}
        <Select
          options={[{ value: "", label: "OFFER: все" }, ...offerOptions]}
          value={selectedOffer}
          onChange={(e) => onOfferChange(e.target.value)}
          aria-label="Фильтр по офферу"
          size="md"
          className="min-w-[130px]"
        />

        {/* Country select */}
        <Select
          options={[{ value: "", label: "СТРАНА: все" }, ...countryOptions]}
          value={selectedCountry}
          onChange={(e) => onCountryChange(e.target.value)}
          aria-label="Фильтр по стране"
          size="md"
          className="min-w-[130px]"
        />

        {/* Разделитель */}
        <span aria-hidden="true" className="w-px h-6 bg-bg-6 self-center" />

        {/* Density toggle */}
        <DensityToggle density={density} onToggle={toggleDensity} />
      </div>

      {/* ── Active-filter chips ─────────────────────────────────────────── */}
      {hasActiveFilters && (
        <ActiveFilterChips
          filterState={filterState}
          onStateToggle={onStateToggle}
          onOfferChange={onOfferChange}
          onCountryChange={onCountryChange}
          onSearchClear={() => onSearchChange("")}
          onClearAll={onClearAll}
        />
      )}
    </div>
  );
}

// ─── Density toggle ──────────────────────────────────────────────────────────

function DensityToggle({
  density,
  onToggle,
}: {
  density: Density;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={cn(
        "h-8 px-3 font-display text-[10.5px] tracking-wider uppercase border transition-colors",
        "flex items-center gap-1.5",
        density === "compact"
          ? "bg-accent-bg border-accent/30 text-accent"
          : "bg-bg-2 border-bg-6 text-bg-9 hover:border-bg-7 hover:text-bg-11",
      )}
      aria-label={`Плотность: ${density === "compact" ? "компактная" : "комфортная"}`}
      aria-pressed={density === "compact"}
      title="Переключить плотность строк таблицы"
    >
      {/* Иконка-шпаргалка: compact = 3 тонкие линии, comfortable = 2 широкие */}
      <span aria-hidden="true" className="flex flex-col gap-[2px]">
        {density === "compact" ? (
          <>
            <span className="block w-4 h-[1.5px] bg-current" />
            <span className="block w-4 h-[1.5px] bg-current" />
            <span className="block w-4 h-[1.5px] bg-current" />
          </>
        ) : (
          <>
            <span className="block w-4 h-[2.5px] bg-current" />
            <span className="block w-4 h-[2.5px] bg-current" />
          </>
        )}
      </span>
      <span>{density === "compact" ? "Компакт" : "Комфорт"}</span>
    </button>
  );
}

// ─── Active filter chips ─────────────────────────────────────────────────────

interface ActiveFilterChipsProps {
  filterState: AdsFilterState;
  onStateToggle: (state: AlertState) => void;
  onOfferChange: (v: string) => void;
  onCountryChange: (v: string) => void;
  onSearchClear: () => void;
  onClearAll: () => void;
}

function ActiveFilterChips({
  filterState,
  onStateToggle,
  onOfferChange,
  onCountryChange,
  onSearchClear,
  onClearAll,
}: ActiveFilterChipsProps) {
  const { search, selectedStates, selectedOffer, selectedCountry } = filterState;

  const chips: ReactNode[] = [];

  // Chip для каждого выбранного state
  for (const state of selectedStates) {
    chips.push(
      <Chip key={`state-${state}`} onRemove={() => onStateToggle(state)}>
        статус: {ALERT_STATE_LABELS[state]}
      </Chip>,
    );
  }

  // Chip для оффера
  if (selectedOffer) {
    chips.push(
      <Chip key="offer" onRemove={() => onOfferChange("")}>
        оффер: {selectedOffer}
      </Chip>,
    );
  }

  // Chip для страны
  if (selectedCountry) {
    chips.push(
      <Chip key="country" onRemove={() => onCountryChange("")}>
        страна: {selectedCountry}
      </Chip>,
    );
  }

  // Chip для поиска
  if (search) {
    chips.push(
      <Chip key="search" onRemove={onSearchClear}>
        поиск: {search}
      </Chip>,
    );
  }

  if (chips.length === 0) return null;

  return (
    <div className="flex items-center gap-2 flex-wrap" role="group" aria-label="Активные фильтры">
      <span className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8 shrink-0">
        Активно
      </span>
      {chips}
      <button
        type="button"
        onClick={onClearAll}
        className="flex items-center gap-1 font-display text-[11px] text-bg-9 hover:text-bg-11 underline decoration-bg-7 underline-offset-3 transition-colors"
        aria-label="Сбросить все фильтры"
      >
        <X size={11} aria-hidden="true" />
        Сбросить всё
      </button>
    </div>
  );
}
