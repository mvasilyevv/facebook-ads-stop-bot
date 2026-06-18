/**
 * FilterBar — строка фильтров Ads под канон design_handoff/ads-web.jsx.
 *
 * Слева → вправо:
 *   1. Search (max 360px) — leading search-icon + trailing «/» kbd-hint.
 *   2. State-pills (Норма/Предупреждение/Стоп/В работе/Отключено) —
 *      full-radius + state-dot, selected = accent border/bg/text.
 *   3. Offer-dropdown — checkbox-list (множественный выбор).
 *   4. Справа — «N объявлений».
 *   5. Ниже (если есть активные) — removable filter-chips.
 *
 * Controlled: всё состояние живёт снаружи (на странице). Компонент только
 * эмитит изменения через колбэки. ref на input пробрасывается для хоткея «/».
 */

import { useEffect, useRef, useState, type RefObject } from "react";
import { Search, Filter, ChevronDown, Check, X } from "lucide-react";
import { ALERT_STATE_LABELS, alertStateCssVar, type AlertState } from "@fb/shared";
import { Kbd } from "@/components/ui/Kbd";
import { cn } from "@/lib/utils/cn";

// ─── Публичный API ───────────────────────────────────────────────────────────

/** Состояние фильтров Ads (offer/кабинет — множественный выбор). */
export interface AdsFilterState {
  /** Строка поиска (имя / ad_id / offer). */
  search: string;
  /** Множество выбранных alert_state. Пустое = все. */
  selectedStates: Set<AlertState>;
  /** Множество выбранных offer-кодов. Пустое = все. */
  selectedOffers: Set<string>;
  /** Мульти-кабинет: множество выбранных ID кабинетов. Пустое = все. */
  selectedAccounts: Set<string>;
  /** Множество выбранных кампаний (campaign_name). Пустое = все. */
  selectedCampaigns: Set<string>;
  /** Множество выбранных адсетов («отец», adset_name). Пустое = все. */
  selectedAdsets: Set<string>;
}

/** Порядок и лейблы state-pills (канон). */
const STATE_PILLS: Array<{ value: AlertState; label: string }> = [
  { value: "normal", label: ALERT_STATE_LABELS.normal },
  { value: "warning_sent", label: ALERT_STATE_LABELS.warning_sent },
  { value: "stop_sent", label: ALERT_STATE_LABELS.stop_sent },
  { value: "claimed", label: ALERT_STATE_LABELS.claimed },
  { value: "disabled", label: ALERT_STATE_LABELS.disabled },
];

// Цвет FSM-точки — канонический маппинг state→токен из @fb/shared
// (локальная таблица удалена при дедупе: см. alertStateCssVar).

export interface FilterBarProps {
  filterState: AdsFilterState;
  /** Доступные offer-коды для dropdown. */
  offerOptions: string[];
  /** Мульти-кабинет: доступные ID кабинетов для dropdown (из загруженных строк). */
  accountOptions: string[];
  /** Доступные кампании (campaign_name) для dropdown. */
  campaignOptions: string[];
  /** Доступные адсеты (adset_name, «отец») для dropdown. */
  adsetOptions: string[];
  /** Кол-во строк после фильтрации (для «N объявлений»). */
  count: number;
  /** ref на search-input (для хоткея «/»). */
  searchRef?: RefObject<HTMLInputElement | null>;

  onSearchChange: (v: string) => void;
  onStateToggle: (state: AlertState) => void;
  onOfferToggle: (offer: string) => void;
  onAccountToggle: (accountId: string) => void;
  onCampaignToggle: (campaign: string) => void;
  onAdsetToggle: (adset: string) => void;
  onClearAll: () => void;

  className?: string;
}

// ─── Компонент ───────────────────────────────────────────────────────────────

export function FilterBar({
  filterState,
  offerOptions,
  accountOptions,
  campaignOptions,
  adsetOptions,
  count,
  searchRef,
  onSearchChange,
  onStateToggle,
  onOfferToggle,
  onAccountToggle,
  onCampaignToggle,
  onAdsetToggle,
  onClearAll,
  className,
}: FilterBarProps) {
  const {
    search,
    selectedStates,
    selectedOffers,
    selectedAccounts,
    selectedCampaigns,
    selectedAdsets,
  } = filterState;
  const hasChips =
    selectedStates.size > 0 ||
    selectedOffers.size > 0 ||
    selectedAccounts.size > 0 ||
    selectedCampaigns.size > 0 ||
    selectedAdsets.size > 0;

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {/* ── Строка фильтров ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Search */}
        <div className="relative flex-1 min-w-[200px] max-w-[360px]">
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-bg-9"
          >
            <Search size={15} />
          </span>
          <input
            ref={searchRef}
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Поиск по названию / ad_id / offer"
            aria-label="Поиск объявлений"
            className={cn(
              "w-full h-8 bg-bg-2 border border-bg-6 rounded-[var(--r-1)]",
              "text-bg-11 text-[13px] pl-8 pr-8",
              "placeholder:text-bg-9 outline-none",
              "focus-visible:border-bg-7",
            )}
          />
          <Kbd className="absolute right-2 top-1/2 -translate-y-1/2 h-[18px] min-w-[18px] px-1 text-bg-8">
            /
          </Kbd>
        </div>

        {/* State pills */}
        <div className="flex gap-1.5" role="group" aria-label="Фильтр по состоянию">
          {STATE_PILLS.map((s) => {
            const on = selectedStates.has(s.value);
            return (
              <button
                key={s.value}
                type="button"
                onClick={() => onStateToggle(s.value)}
                aria-pressed={on}
                className={cn(
                  "inline-flex items-center gap-1.5 h-[30px] px-3",
                  "rounded-full border text-[12px] font-medium",
                  "transition-colors duration-[120ms] cursor-pointer",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                  on
                    ? "border-accent bg-accent-bg text-accent"
                    : "border-bg-6 bg-transparent text-bg-10 hover:border-bg-7 hover:text-bg-11",
                )}
              >
                <span
                  aria-hidden="true"
                  className="size-[7px] rounded-full"
                  style={{ background: alertStateCssVar(s.value) }}
                />
                {s.label}
              </button>
            );
          })}
        </div>

        {/* Offer dropdown */}
        <CheckDropdown
          label="offer"
          ariaLabel="Фильтр по офферу"
          emptyText="Нет офферов"
          options={offerOptions}
          selected={selectedOffers}
          onToggle={onOfferToggle}
        />

        {/* Campaign dropdown */}
        <CheckDropdown
          label="кампания"
          ariaLabel="Фильтр по кампании"
          emptyText="Нет кампаний"
          options={campaignOptions}
          selected={selectedCampaigns}
          onToggle={onCampaignToggle}
        />

        {/* Adset dropdown («отец» — прямой родитель объявления) */}
        <CheckDropdown
          label="адсет"
          ariaLabel="Фильтр по адсету"
          emptyText="Нет адсетов"
          options={adsetOptions}
          selected={selectedAdsets}
          onToggle={onAdsetToggle}
        />

        {/* Cabinet dropdown (мульти-кабинет) — показываем только когда кабинетов >1 */}
        {accountOptions.length > 1 && (
          <CheckDropdown
            label="кабинет"
            ariaLabel="Фильтр по кабинету"
            emptyText="Нет кабинетов"
            options={accountOptions}
            selected={selectedAccounts}
            onToggle={onAccountToggle}
          />
        )}

        <div className="flex-1" />

        {/* Count */}
        <span className="font-display text-[12px] text-bg-9 tabular-nums shrink-0">
          {count.toLocaleString("en-US")} объявлений
        </span>
      </div>

      {/* ── Active filter chips ─────────────────────────────────────────── */}
      {hasChips && (
        <div className="flex items-center gap-1.5 flex-wrap" role="group" aria-label="Активные фильтры">
          {[...selectedStates].map((s) => (
            <FilterChip key={`st-${s}`} onRemove={() => onStateToggle(s)}>
              state = {ALERT_STATE_LABELS[s]}
            </FilterChip>
          ))}
          {[...selectedOffers].map((o) => (
            <FilterChip key={`of-${o}`} onRemove={() => onOfferToggle(o)}>
              offer = {o}
            </FilterChip>
          ))}
          {[...selectedAccounts].map((a) => (
            <FilterChip key={`ac-${a}`} onRemove={() => onAccountToggle(a)}>
              кабинет = {a}
            </FilterChip>
          ))}
          {[...selectedCampaigns].map((c) => (
            <FilterChip key={`cm-${c}`} onRemove={() => onCampaignToggle(c)}>
              кампания = {c}
            </FilterChip>
          ))}
          {[...selectedAdsets].map((a) => (
            <FilterChip key={`as-${a}`} onRemove={() => onAdsetToggle(a)}>
              адсет = {a}
            </FilterChip>
          ))}
          <button
            type="button"
            onClick={onClearAll}
            aria-label="Сбросить все фильтры"
            className={cn(
              "inline-flex items-center gap-1 font-display text-[11px] text-bg-9",
              "hover:text-bg-11 underline decoration-bg-7 underline-offset-2 transition-colors",
            )}
          >
            <X size={11} aria-hidden="true" />
            Сбросить всё
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Checkbox-dropdown (offer / кабинет — общий компонент) ──────────────────

function CheckDropdown({
  label,
  ariaLabel,
  emptyText,
  options,
  selected,
  onToggle,
}: {
  /** Текст кнопки-триггера (нижний регистр, как в каноне: «offer», «кабинет»). */
  label: string;
  ariaLabel: string;
  emptyText: string;
  options: string[];
  selected: Set<string>;
  onToggle: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Закрытие по клику вне.
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onEsc(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        className={cn(
          "inline-flex items-center gap-1.5 h-8 px-3",
          "bg-bg-2 border border-bg-6 text-bg-10",
          "font-display text-[12px] tracking-wide",
          "hover:border-bg-7 hover:text-bg-11 transition-colors duration-[120ms]",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
          selected.size > 0 && "text-accent border-accent/40",
        )}
      >
        <Filter size={13} aria-hidden="true" />
        {label}
        {selected.size ? ` · ${selected.size}` : ""}
        <ChevronDown
          size={12}
          aria-hidden="true"
          className={cn("transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label={ariaLabel}
          className={cn(
            "absolute top-[calc(100%+6px)] left-0 z-30 min-w-[160px] max-h-[280px] overflow-y-auto",
            "bg-bg-3 border border-bg-6 p-1.5 flex flex-col gap-0.5",
          )}
        >
          {options.length === 0 ? (
            <span className="px-2 py-1.5 font-display text-[12px] text-bg-8">{emptyText}</span>
          ) : (
            options.map((o) => {
              const on = selected.has(o);
              return (
                <button
                  key={o}
                  type="button"
                  role="option"
                  aria-selected={on}
                  onClick={() => onToggle(o)}
                  className={cn(
                    "flex items-center gap-2 px-2 py-1.5 text-left",
                    "text-bg-11 text-[13px] transition-colors",
                    on ? "bg-bg-4" : "hover:bg-bg-4/60",
                  )}
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      "size-[14px] inline-flex items-center justify-center border",
                      on ? "border-accent bg-accent" : "border-bg-7",
                    )}
                  >
                    {on ? <Check size={11} strokeWidth={3} className="text-bg-0" /> : null}
                  </span>
                  <span className="font-display">{o}</span>
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

// ─── Filter chip (removable) ───────────────────────────────────────────────

function FilterChip({
  children,
  onRemove,
}: {
  children: React.ReactNode;
  onRemove: () => void;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 h-[22px] pl-2.5 pr-1",
        "bg-bg-2 border border-bg-6 text-bg-10",
        "font-display text-[11px] tracking-[0.02em]",
      )}
    >
      {children}
      <button
        type="button"
        aria-label="Удалить фильтр"
        onClick={onRemove}
        className="inline-flex items-center justify-center size-[16px] text-bg-9 hover:text-bg-11 transition-colors"
      >
        <X size={11} aria-hidden="true" />
      </button>
    </span>
  );
}
