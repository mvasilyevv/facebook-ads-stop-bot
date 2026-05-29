/**
 * Ads (`/ads`) — таблица объявлений с фильтрами, bulk-actions, drawer.
 *
 * Блоки (по docs/frontend_v2_mockups/ads.html):
 *   1. PageHeader — eyebrow 02, stats в subtitle, кнопки Export/Columns.
 *   2. FilterBar — поиск по имени, фильтр по alert_state (Pill-чипы),
 *      select по offer_code, include_inactive toggle.
 *   3. ActiveFilters — chip'ы активных фильтров с × + "clear all".
 *   4. Таблица — 11 колонок с checkbox, сортировкой, row-click → drawer.
 *   5. BulkActionBar — sticky bottom при выборе строк (disable + clear).
 *   6. Pagination — простой next/prev.
 *   7. Drawer — маршрутизирует к /ads/$fbAdId (открывается поверх).
 *
 * Источники данных:
 *   - useAds({ alert_state, include_inactive, limit, offset }) — список.
 *   - useCreateDisableTask() — mutation для bulk-disable.
 */

import { useState, useMemo, useCallback, type ReactNode } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Search, Download, SlidersHorizontal, X, XCircle, Layers } from "lucide-react";

import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Badge, alertStateToBadge } from "@/components/ui/Badge";
import { RuleBadge } from "@/components/domain/RuleBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";
import { useAds, useCreateDisableTask } from "@/lib/api/ads";
import { formatSpend, formatRelativeTime } from "@/lib/utils/format";
import { cn } from "@/lib/utils/cn";
import type { AdSnapshot } from "@/lib/types/api";

export const Route = createFileRoute("/ads/")({
  component: AdsPage,
});

/** Лейблы FSM-состояний для Pill-чипов фильтра. */
const STATE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "normal", label: "normal" },
  { value: "warning_sent", label: "warning" },
  { value: "stop_sent", label: "stop" },
  { value: "claimed", label: "claimed" },
  { value: "disabled", label: "disabled" },
];

const PAGE_SIZE = 50;

/** Короткая читаемая метка FSM-state для бейджа (mock-совместимость). */
function stateLabel(state: string): string {
  switch (state) {
    case "warning_sent":
      return "warn";
    case "stop_sent":
      return "stop";
    default:
      return state;
  }
}

function AdsPage() {
  const navigate = useNavigate();

  // ─── Состояние фильтров ──────────────────────────────────────────────────
  const [search, setSearch] = useState("");
  /** Множество выбранных alert_state-фильтров. Пустое = все. */
  const [selectedStates, setSelectedStates] = useState<Set<string>>(new Set());
  const [selectedOffer, setSelectedOffer] = useState<string>("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [offset, setOffset] = useState(0);

  // ─── Состояние выбора строк ──────────────────────────────────────────────
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [confirmBulkOpen, setConfirmBulkOpen] = useState(false);

  // ─── API-запрос ──────────────────────────────────────────────────────────
  /** alert_state для API: берём первый если только один, иначе undefined (API не поддерживает multi). */
  const apiAlertState = selectedStates.size === 1 ? [...selectedStates][0] : undefined;

  const adsQuery = useAds({
    alert_state: apiAlertState,
    include_inactive: includeInactive || undefined,
    limit: PAGE_SIZE,
    offset,
  });

  const disableMutation = useCreateDisableTask();

  // ─── Фильтрация на клиенте ───────────────────────────────────────────────
  const filteredAds = useMemo(() => {
    const raw = adsQuery.data ?? [];
    return raw.filter((ad) => {
      // Фильтр по множеству состояний (когда выбрано несколько — client-side)
      if (selectedStates.size > 1 && !selectedStates.has(ad.alert_state)) return false;
      // Фильтр по offer_code
      if (selectedOffer && ad.offer_code !== selectedOffer) return false;
      // Поиск по имени
      if (search) {
        const q = search.toLowerCase();
        if (
          !ad.ad_name.toLowerCase().includes(q) &&
          !ad.fb_ad_id.toLowerCase().includes(q)
        ) {
          return false;
        }
      }
      return true;
    });
  }, [adsQuery.data, selectedStates, selectedOffer, search]);

  // ─── Уникальные offer_codes для Select ──────────────────────────────────
  const offerCodes = useMemo(() => {
    const codes = new Set<string>();
    for (const ad of adsQuery.data ?? []) {
      if (ad.offer_code) codes.add(ad.offer_code);
    }
    return [...codes].sort();
  }, [adsQuery.data]);

  // ─── Aggregate stats для subtitle ────────────────────────────────────────
  const stats = useMemo(() => {
    const all = adsQuery.data ?? [];
    return {
      total: all.length,
      warning: all.filter((a) => a.alert_state === "warning_sent").length,
      stop: all.filter((a) => a.alert_state === "stop_sent").length,
      showing: filteredAds.length,
    };
  }, [adsQuery.data, filteredAds]);

  // ─── Bulk actions ─────────────────────────────────────────────────────────
  const toggleRow = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    if (selectedIds.size === filteredAds.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredAds.map((a) => a.fb_ad_id)));
    }
  }, [selectedIds.size, filteredAds]);

  const handleBulkDisable = useCallback(async () => {
    const ids = [...selectedIds];
    let success = 0;
    let fail = 0;
    for (const fb_ad_id of ids) {
      try {
        await disableMutation.mutateAsync(fb_ad_id);
        success++;
      } catch {
        fail++;
      }
    }
    setSelectedIds(new Set());
    if (fail === 0) {
      toast.success(
        `Disable запущен для ${success} объявлени${success === 1 ? "я" : "й"}`,
        "Задачи добавлены в очередь.",
      );
    } else {
      toast.warning(
        `Частичный успех: ${success} ок, ${fail} ошибок`,
        "Проверь очередь задач.",
      );
    }
  }, [selectedIds, disableMutation]);

  // ─── Фильтр-чипы ─────────────────────────────────────────────────────────
  const removeStateFilter = useCallback((state: string) => {
    setSelectedStates((prev) => {
      const next = new Set(prev);
      next.delete(state);
      return next;
    });
  }, []);

  const clearAllFilters = useCallback(() => {
    setSearch("");
    setSelectedStates(new Set());
    setSelectedOffer("");
    setIncludeInactive(false);
  }, []);

  const hasActiveFilters = selectedStates.size > 0 || selectedOffer !== "" || search !== "";

  // ─── Навигация к drawer ───────────────────────────────────────────────────
  const openDrawer = useCallback(
    (fbAdId: string) => {
      navigate({ to: "/ads/$fbAdId", params: { fbAdId } });
    },
    [navigate],
  );

  const indeterminate = selectedIds.size > 0 && selectedIds.size < filteredAds.length;
  const allSelected = filteredAds.length > 0 && selectedIds.size === filteredAds.length;

  return (
    <>
      {/* 1. PageHeader */}
      <PageHeader
        eyebrowNum="02"
        eyebrow="ОПЕРИРОВАТЬ · ПРОВЕРЯТЬ · ВМЕШИВАТЬСЯ"
        title="Объявления."
        displayNumber="02"
        subtitle={
          <>
            <span className="text-bg-11 font-medium">{stats.total}</span> активных
            <HeaderSep />
            <span className={cn("font-medium", stats.warning > 0 ? "text-warning" : "text-bg-11")}>
              {stats.warning}
            </span>{" "}
            предупреждений
            <HeaderSep />
            <span className={cn("font-medium", stats.stop > 0 ? "text-danger" : "text-bg-11")}>
              {stats.stop}
            </span>{" "}
            стоп
            <HeaderSep />
            <span>показано {stats.showing} из {stats.total}</span>
          </>
        }
        action={
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="md"
              leftIcon={<Download size={14} aria-hidden="true" />}
            >
              Экспорт
            </Button>
            <Button
              variant="secondary"
              size="md"
              leftIcon={<SlidersHorizontal size={14} aria-hidden="true" />}
            >
              Колонки
            </Button>
          </div>
        }
      />

      {/* 2. FilterBar */}
      <div className="flex items-center gap-2 p-3 bg-bg-1 border border-bg-5 mb-2">
        {/* Поиск */}
        <div className="flex-1 flex items-center gap-2 px-3 h-8 bg-bg-2 border border-bg-6 focus-within:border-accent focus-within:bg-bg-3 transition-colors">
          <Search size={14} className="text-bg-9 shrink-0" aria-hidden="true" />
          <input
            type="text"
            placeholder="Поиск по имени или id…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 bg-transparent border-0 outline-0 text-bg-11 text-[13px] font-body placeholder:text-bg-9"
            aria-label="Поиск объявления"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              className="text-bg-9 hover:text-bg-11 transition-colors"
              aria-label="Очистить поиск"
            >
              <X size={12} aria-hidden="true" />
            </button>
          )}
        </div>

        {/* State Pill-чипы */}
        <div className="flex gap-1">
          {STATE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => {
                setSelectedStates((prev) => {
                  const next = new Set(prev);
                  if (next.has(opt.value)) next.delete(opt.value);
                  else next.add(opt.value);
                  return next;
                });
              }}
              className={cn(
                "h-7 px-3 font-display text-[11px] tracking-wider uppercase border transition-colors",
                selectedStates.has(opt.value)
                  ? "bg-accent-bg border-accent/30 text-accent"
                  : "bg-bg-2 border-bg-6 text-bg-9 hover:border-bg-7 hover:text-bg-11",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Offer Select */}
        <div className="relative">
          <select
            value={selectedOffer}
            onChange={(e) => setSelectedOffer(e.target.value)}
            className={cn(
              "h-8 pl-3 pr-8 appearance-none",
              "bg-bg-2 border border-bg-6 text-[12px] font-display",
              "focus:border-accent focus:outline-none transition-colors",
              selectedOffer ? "text-bg-11" : "text-bg-9",
            )}
            aria-label="Фильтр по офферу"
          >
            <option value="">OFFER: any</option>
            {offerCodes.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
          <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-bg-9">
            ▾
          </span>
        </div>

        {/* Include inactive toggle */}
        <button
          type="button"
          onClick={() => setIncludeInactive((v) => !v)}
          className={cn(
            "h-8 px-3 font-display text-[11px] tracking-wider uppercase border transition-colors",
            includeInactive
              ? "bg-accent-bg border-accent/30 text-accent"
              : "bg-bg-2 border-bg-6 text-bg-9 hover:border-bg-7 hover:text-bg-11",
          )}
        >
          +неактивные
        </button>
      </div>

      {/* 3. Active filter chips */}
      {hasActiveFilters && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <span className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8 mr-1">
            Активно
          </span>
          {[...selectedStates].map((state) => (
            <FilterChip
              key={state}
              label={`state = ${state}`}
              onRemove={() => removeStateFilter(state)}
            />
          ))}
          {selectedOffer && (
            <FilterChip
              label={`offer = ${selectedOffer}`}
              onRemove={() => setSelectedOffer("")}
            />
          )}
          {search && (
            <FilterChip label={`search = ${search}`} onRemove={() => setSearch("")} />
          )}
          <button
            type="button"
            onClick={clearAllFilters}
            className="text-[11px] font-display text-bg-9 hover:text-bg-11 underline decoration-bg-7 underline-offset-3 transition-colors"
          >
            Сбросить всё
          </button>
        </div>
      )}

      {/* 4. Таблица */}
      <div className="bg-bg-1 border border-bg-5 overflow-hidden mb-4">
        {adsQuery.isError ? (
          <div className="p-6">
            <ErrorState
              error={adsQuery.error}
              onRetry={() => adsQuery.refetch()}
            />
          </div>
        ) : (
          <table className="w-full border-collapse" style={{ fontVariantNumeric: "tabular-nums" }}>
            <thead>
              <tr>
                {/* Checkbox-all */}
                <th className="w-9 px-3 py-3 bg-bg-1 border-b border-bg-5">
                  <CheckboxEl
                    checked={allSelected}
                    indeterminate={indeterminate}
                    onChange={toggleAll}
                    aria-label="Выбрать все"
                  />
                </th>
                <Th>Объявление</Th>
                <Th>Offer</Th>
                <Th>Статус</Th>
                <Th align="right">Spend</Th>
                <Th align="right">CPL</Th>
                <Th align="right">CTR</Th>
                <Th align="right">Частота</Th>
                <Th align="right">Лиды</Th>
                <Th align="right">Посл. скан</Th>
                <th className="bg-bg-1 border-b border-bg-5 w-10" />
              </tr>
            </thead>
            <tbody>
              {adsQuery.isLoading
                ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} />)
                : filteredAds.length === 0
                  ? null
                  : filteredAds.map((ad) => (
                      <AdRow
                        key={ad.fb_ad_id}
                        ad={ad}
                        selected={selectedIds.has(ad.fb_ad_id)}
                        onToggleSelect={() => toggleRow(ad.fb_ad_id)}
                        onOpen={() => openDrawer(ad.fb_ad_id)}
                      />
                    ))}
            </tbody>
          </table>
        )}

        {/* Empty state внутри таблицы */}
        {!adsQuery.isLoading && !adsQuery.isError && filteredAds.length === 0 && (
          <EmptyState
            icon={<Layers size={36} strokeWidth={1.25} aria-hidden="true" />}
            title="Объявления не найдены"
            description={
              hasActiveFilters
                ? "Попробуй изменить фильтры или сбросить их."
                : "Нет данных для отображения."
            }
            action={
              hasActiveFilters ? (
                <Button variant="secondary" size="sm" onClick={clearAllFilters}>
                  Сбросить фильтры
                </Button>
              ) : undefined
            }
          />
        )}
      </div>

      {/* Pagination */}
      {!adsQuery.isLoading && !adsQuery.isError && (adsQuery.data?.length ?? 0) >= PAGE_SIZE && (
        <div className="flex items-center justify-between font-display text-[11.5px] text-bg-9 tracking-wide pb-4">
          <span>
            Показано{" "}
            <span className="text-bg-11">{offset + 1}–{offset + filteredAds.length}</span>
          </span>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={offset === 0}
              onClick={() => {
                setOffset(Math.max(0, offset - PAGE_SIZE));
                setSelectedIds(new Set());
              }}
            >
              ← Назад
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={(adsQuery.data?.length ?? 0) < PAGE_SIZE}
              onClick={() => {
                setOffset(offset + PAGE_SIZE);
                setSelectedIds(new Set());
              }}
            >
              Вперёд →
            </Button>
          </div>
        </div>
      )}

      {/* 5. Bulk action bar */}
      {selectedIds.size > 0 && (
        <BulkBar
          count={selectedIds.size}
          onDisable={() => setConfirmBulkOpen(true)}
          onClear={() => setSelectedIds(new Set())}
        />
      )}

      {/* Confirm dialog для bulk-disable */}
      <ConfirmDialog
        open={confirmBulkOpen}
        onOpenChange={setConfirmBulkOpen}
        title={`Отключить ${selectedIds.size} объявлени${selectedIds.size === 1 ? "е" : "й"}?`}
        description="Задачи disable будут добавлены в очередь. Действие нельзя отменить автоматически."
        confirmLabel="Отключить"
        onConfirm={handleBulkDisable}
      />
    </>
  );
}

// ─── Subcomponents ───────────────────────────────────────────────────────────

/** Заголовок колонки таблицы. */
function Th({ children, align = "left" }: { children?: ReactNode; align?: "left" | "right" }) {
  return (
    <th
      className={cn(
        "bg-bg-1 border-b border-bg-5 py-3 px-3.5",
        "font-display text-[10px] tracking-[0.12em] uppercase text-bg-8 font-medium",
        "sticky top-0",
        align === "right" ? "text-right" : "text-left",
      )}
    >
      {children}
    </th>
  );
}

/** Строка-скелетон при загрузке. */
function SkeletonRow() {
  return (
    <tr className="border-b border-bg-3">
      {Array.from({ length: 11 }).map((_, i) => (
        <td key={i} className="px-3.5 py-3">
          <Skeleton height={14} width={i === 1 ? "80%" : i === 0 ? 16 : "60%"} />
        </td>
      ))}
    </tr>
  );
}

/** Кастомный checkbox-элемент (не native — для согласованного вида). */
function CheckboxEl({
  checked,
  indeterminate,
  onChange,
  "aria-label": ariaLabel,
}: {
  checked: boolean;
  indeterminate?: boolean;
  onChange: () => void;
  "aria-label"?: string;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={indeterminate ? "mixed" : checked}
      aria-label={ariaLabel}
      onClick={onChange}
      className={cn(
        "size-4 border inline-flex items-center justify-center transition-colors",
        checked || indeterminate
          ? "bg-accent border-accent text-bg-0"
          : "bg-bg-2 border-bg-7 hover:border-bg-9",
      )}
    >
      {indeterminate ? (
        <span aria-hidden="true" className="block w-2 h-px bg-bg-0" />
      ) : checked ? (
        <svg
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          width={11}
          height={11}
          aria-hidden="true"
        >
          <polyline points="2 6 5 9 10 3" />
        </svg>
      ) : null}
    </button>
  );
}

/** Строка таблицы для одного AdSnapshot. */
function AdRow({
  ad,
  selected,
  onToggleSelect,
  onOpen,
}: {
  ad: AdSnapshot;
  selected: boolean;
  onToggleSelect: () => void;
  onOpen: () => void;
}) {
  const isStop = ad.alert_state === "stop_sent";
  const isWarning = ad.alert_state === "warning_sent";
  const isDisabled = ad.alert_state === "disabled";

  const rowCls = cn(
    "border-b border-bg-3 transition-colors cursor-pointer",
    selected
      ? "bg-accent-bg [box-shadow:inset_2px_0_0_theme(colors.accent)]"
      : isStop
        ? "bg-[rgba(199,98,92,0.04)] hover:bg-[rgba(199,98,92,0.08)]"
        : isWarning
          ? "bg-[rgba(212,168,88,0.04)] hover:bg-bg-2"
          : "hover:bg-bg-2",
  );

  const metricCls = isDisabled ? "text-bg-9" : "text-bg-11";

  // CPL threshold не знаем здесь — просто показываем значение
  const spend = ad.metrics?.spend ?? null;
  const cpl = ad.metrics?.cost_per_lead ?? null;
  const ctr = ad.metrics?.ctr ?? null;
  const freq = ad.metrics?.frequency ?? null;
  const leads = ad.metrics?.leads ?? null;
  const lastSeen = ad.last_seen_at;

  const ruleCodes = [...(ad.stop_rule_codes ?? []), ...(ad.warning_rule_codes ?? [])].slice(0, 3);

  return (
    <tr className={rowCls}>
      {/* Checkbox */}
      <td className="w-9 px-3 py-3" onClick={(e) => e.stopPropagation()}>
        <CheckboxEl
          checked={selected}
          onChange={onToggleSelect}
          aria-label={`Выбрать ${ad.ad_name}`}
        />
      </td>

      {/* Ad cell */}
      <td className="py-3 px-3.5 max-w-[320px]" onClick={onOpen}>
        <div className="flex items-center gap-3">
          {/* thumbnail-placeholder */}
          <div
            aria-hidden="true"
            className="w-14 h-8 bg-bg-3 border border-bg-5 shrink-0 relative overflow-hidden"
          />
          <div className="min-w-0">
            <div className="font-display text-[13px] text-bg-11 truncate tracking-tight">
              {ad.ad_name}
            </div>
            <div className="font-display text-[10.5px] text-bg-9 tracking-wide mt-0.5">
              {ad.fb_ad_id}
              {ad.offer_code ? ` · ${ad.offer_code}` : ""}
            </div>
          </div>
        </div>
      </td>

      {/* Offer */}
      <td className="py-3 px-3.5" onClick={onOpen}>
        {ad.offer_code ? (
          <span className="inline-flex items-center px-2 py-0.5 bg-bg-3 border border-bg-6 text-bg-10 font-display text-[10.5px] tracking-[0.04em] uppercase">
            {ad.offer_code}
          </span>
        ) : (
          <span className="text-bg-8 font-display text-[11px]">—</span>
        )}
      </td>

      {/* State badge */}
      <td className="py-3 px-3.5" onClick={onOpen}>
        <Badge variant={alertStateToBadge(ad.alert_state)} size="md">
          {stateLabel(ad.alert_state)}
        </Badge>
      </td>

      {/* Spend */}
      <td
        className={cn("py-3 px-3.5 text-right font-display text-[13px] tabular-nums", metricCls)}
        onClick={onOpen}
      >
        {formatSpend(spend)}
      </td>

      {/* CPL */}
      <td
        className={cn("py-3 px-3.5 text-right font-display text-[13px] tabular-nums", metricCls)}
        onClick={onOpen}
      >
        {formatSpend(cpl)}
      </td>

      {/* CTR */}
      <td
        className={cn("py-3 px-3.5 text-right font-display text-[13px] tabular-nums", metricCls)}
        onClick={onOpen}
      >
        {ctr != null ? `${parseFloat(String(ctr)).toFixed(2)}%` : "—"}
      </td>

      {/* Frequency */}
      <td
        className={cn("py-3 px-3.5 text-right font-display text-[13px] tabular-nums", metricCls)}
        onClick={onOpen}
      >
        {freq != null ? parseFloat(String(freq)).toFixed(1) : "—"}
      </td>

      {/* Leads */}
      <td
        className={cn("py-3 px-3.5 text-right font-display text-[13px] tabular-nums", metricCls)}
        onClick={onOpen}
      >
        {leads ?? "—"}
      </td>

      {/* Last scan */}
      <td
        className="py-3 px-3.5 text-right font-display text-[11px] text-bg-9 tracking-wide tabular-nums"
        onClick={onOpen}
      >
        {formatRelativeTime(lastSeen)}
      </td>

      {/* Rule codes + actions */}
      <td className="py-3 px-3.5" onClick={onOpen}>
        <div className="flex gap-1 justify-end flex-wrap">
          {ruleCodes.map((code) => (
            <RuleBadge key={code} code={code} />
          ))}
        </div>
      </td>
    </tr>
  );
}

/** Chip активного фильтра с кнопкой × */
function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1.5 pl-2.5 pr-1 py-1 bg-accent-bg border border-accent/20 text-accent font-display text-[11px] tracking-wide rounded-full">
      {label}
      <button
        type="button"
        onClick={onRemove}
        className="inline-flex items-center justify-center size-[18px] rounded-full bg-accent/10 hover:bg-accent/20 transition-colors"
        aria-label={`Удалить фильтр: ${label}`}
      >
        <X size={10} aria-hidden="true" />
      </button>
    </span>
  );
}

/** Sticky bulk-action bar внизу экрана при выборе строк. */
function BulkBar({
  count,
  onDisable,
  onClear,
}: {
  count: number;
  onDisable: () => void;
  onClear: () => void;
}) {
  return (
    <div
      className={cn(
        "fixed bottom-6 left-1/2 -translate-x-1/2 z-[100]",
        "flex items-center gap-4 px-4 py-2.5",
        "bg-bg-2 border border-bg-7",
        "shadow-[0_8px_32px_rgba(0,0,0,0.6),inset_0_1px_0_rgba(255,255,255,0.04)]",
      )}
    >
      <span className="font-display text-[13px] text-bg-11">
        <span className="text-accent font-semibold">{count}</span> выбрано
      </span>
      <span aria-hidden="true" className="w-px h-5 bg-bg-6" />
      <Button
        variant="danger"
        size="sm"
        leftIcon={<XCircle size={14} aria-hidden="true" />}
        onClick={onDisable}
      >
        Отключить
      </Button>
      <Button variant="ghost" size="sm" onClick={onClear}>
        Сбросить
      </Button>
    </div>
  );
}
