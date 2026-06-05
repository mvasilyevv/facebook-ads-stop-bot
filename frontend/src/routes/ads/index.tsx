/**
 * Ads (`/ads`) — таблица объявлений с фильтрами, bulk-actions, drawer.
 *
 * Блоки (по docs/frontend_mockups/ads.html):
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
import { Search, Download, X, XCircle, Layers } from "lucide-react";

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
import { useRealtimeInvalidation } from "@/lib/websocket/useRealtimeInvalidation";
import { formatSpend, formatRelativeTime } from "@/lib/utils/format";
import { ALERT_STATE_LABELS } from "@/lib/constants/states";
import { cn } from "@/lib/utils/cn";
import type { AdSnapshot } from "@/lib/types/api";

export const Route = createFileRoute("/ads/")({
  component: AdsPage,
});

/** Лейблы FSM-состояний для Pill-чипов фильтра (человекочитаемые). */
const STATE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "normal", label: ALERT_STATE_LABELS.normal },
  { value: "warning_sent", label: ALERT_STATE_LABELS.warning_sent },
  { value: "stop_sent", label: ALERT_STATE_LABELS.stop_sent },
  { value: "claimed", label: ALERT_STATE_LABELS.claimed },
  { value: "disabled", label: ALERT_STATE_LABELS.disabled },
];

const PAGE_SIZE = 50;

/** Колонки, по которым доступна клиентская сортировка. */
type SortKey = "spend" | "cpl" | "ctr" | "frequency" | "leads" | "last_seen";

/** Число для сортировки: null/невалидное → -Infinity (уходит вниз при сортировке desc). */
function sortNum(v: number | string | null | undefined): number {
  if (v == null || v === "") return Number.NEGATIVE_INFINITY;
  const n = typeof v === "string" ? Number.parseFloat(v) : v;
  return Number.isNaN(n) ? Number.NEGATIVE_INFINITY : n;
}

function AdsPage() {
  const navigate = useNavigate();

  // Live-обновление: после скана/алерта WS инвалидирует список — без ручного рефреша.
  useRealtimeInvalidation();

  // ─── Состояние фильтров ──────────────────────────────────────────────────
  const [search, setSearch] = useState("");
  /** Множество выбранных alert_state-фильтров. Пустое = все. */
  const [selectedStates, setSelectedStates] = useState<Set<string>>(new Set());
  const [selectedOffer, setSelectedOffer] = useState<string>("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" } | null>(null);

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
    const raw = adsQuery.data?.items ?? [];
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

  // ─── Сортировка (клиентская, поверх отфильтрованной страницы) ────────────
  const sortedAds = useMemo(() => {
    if (!sort) return filteredAds;
    const val = (ad: AdSnapshot): number => {
      switch (sort.key) {
        case "spend":
          return sortNum(ad.metrics?.spend);
        case "cpl":
          return sortNum(ad.metrics?.cost_per_lead);
        case "ctr":
          return sortNum(ad.metrics?.ctr);
        case "frequency":
          return sortNum(ad.metrics?.frequency);
        case "leads":
          return sortNum(ad.metrics?.leads);
        case "last_seen":
          return ad.last_seen_at ? new Date(ad.last_seen_at).getTime() : Number.NEGATIVE_INFINITY;
      }
    };
    const arr = [...filteredAds].sort((a, b) => val(a) - val(b));
    return sort.dir === "desc" ? arr.reverse() : arr;
  }, [filteredAds, sort]);

  /** Клик по заголовку: desc → asc → сброс. */
  const toggleSort = useCallback((key: SortKey) => {
    setSort((prev) => {
      if (prev?.key !== key) return { key, dir: "desc" };
      if (prev.dir === "desc") return { key, dir: "asc" };
      return null;
    });
  }, []);

  // ─── Уникальные offer_codes для Select ──────────────────────────────────
  const offerCodes = useMemo(() => {
    const codes = new Set<string>();
    for (const ad of adsQuery.data?.items ?? []) {
      if (ad.offer_code) codes.add(ad.offer_code);
    }
    return [...codes].sort();
  }, [adsQuery.data]);

  // ─── Aggregate stats для subtitle ────────────────────────────────────────
  const stats = useMemo(() => {
    const all = adsQuery.data?.items ?? [];
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
    setOffset(0);
  }, []);

  /** Экспорт текущего (отфильтрованного) списка в CSV — выгрузка на клиенте. */
  const handleExport = useCallback(() => {
    if (filteredAds.length === 0) {
      toast.warning("Нечего экспортировать", "Список пуст.");
      return;
    }
    const header = [
      "ad_name",
      "fb_ad_id",
      "offer_code",
      "alert_state",
      "spend",
      "cpl",
      "ctr",
      "frequency",
      "leads",
      "last_seen_at",
    ];
    const lines = filteredAds.map((a) =>
      [
        a.ad_name,
        a.fb_ad_id,
        a.offer_code ?? "",
        a.alert_state,
        a.metrics?.spend ?? "",
        a.metrics?.cost_per_lead ?? "",
        a.metrics?.ctr ?? "",
        a.metrics?.frequency ?? "",
        a.metrics?.leads ?? "",
        a.last_seen_at ?? "",
      ]
        .map(csvCell)
        .join(","),
    );
    const csv = [header.join(","), ...lines].join("\n");
    const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `ads_export_${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success("Экспортировано", `${filteredAds.length} строк в CSV.`);
  }, [filteredAds]);

  const hasActiveFilters =
    selectedStates.size > 0 || selectedOffer !== "" || search !== "" || includeInactive;

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
        eyebrowNum="01"
        eyebrow="ОПЕРИРОВАТЬ · ПРОВЕРЯТЬ · ВМЕШИВАТЬСЯ"
        title="Объявления"
        displayNumber="01"
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
          <Button
            variant="secondary"
            size="md"
            leftIcon={<Download size={14} aria-hidden="true" />}
            onClick={handleExport}
            title="Скачать текущий список (с учётом фильтров) в CSV"
          >
            Экспорт CSV
          </Button>
        }
      />

      {/* 2. FilterBar */}
      <div className="flex flex-wrap items-center gap-2 p-3 bg-bg-1 border border-bg-5 mb-2">
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

        {/* Разделитель: отделяем тогл данных от фильтров по статусу. */}
        <span aria-hidden="true" className="w-px h-6 bg-bg-6 self-center" />

        {/* Include inactive toggle */}
        <button
          type="button"
          title="Показать также отключённые/неактивные объявления"
          onClick={() => setIncludeInactive((v) => !v)}
          className={cn(
            "h-8 px-3 font-display text-[11px] tracking-wider uppercase border transition-colors",
            includeInactive
              ? "bg-accent-bg border-accent/30 text-accent"
              : "bg-bg-2 border-bg-6 text-bg-9 hover:border-bg-7 hover:text-bg-11",
          )}
        >
          + неактивные
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
              label={`Статус: ${ALERT_STATE_LABELS[state as keyof typeof ALERT_STATE_LABELS] ?? state}`}
              onRemove={() => removeStateFilter(state)}
            />
          ))}
          {selectedOffer && (
            <FilterChip label={`Оффер: ${selectedOffer}`} onRemove={() => setSelectedOffer("")} />
          )}
          {search && <FilterChip label={`Поиск: ${search}`} onRemove={() => setSearch("")} />}
          {includeInactive && (
            <FilterChip label="+ неактивные" onRemove={() => setIncludeInactive(false)} />
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

      {/* Предупреждение: мультивыбор статусов фильтруется на клиенте по текущей странице. */}
      {selectedStates.size > 1 && (
        <div className="flex items-center gap-2 mb-4 -mt-1 text-[11px] font-display text-warning">
          Выбрано несколько статусов — фильтр применяется к загруженной странице ({filteredAds.length}{" "}
          из {stats.total}). Для полного поиска оставьте один статус.
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
                <Th>Оффер</Th>
                <Th>Статус</Th>
                <SortableTh align="right" sortKey="spend" sort={sort} onSort={toggleSort} title="Расход за сутки">
                  Spend
                </SortableTh>
                <SortableTh align="right" sortKey="cpl" sort={sort} onSort={toggleSort} title="Cost Per Lead — стоимость лида">
                  CPL
                </SortableTh>
                <SortableTh align="right" sortKey="ctr" sort={sort} onSort={toggleSort} title="Click-Through Rate — кликабельность">
                  CTR
                </SortableTh>
                <SortableTh align="right" sortKey="frequency" sort={sort} onSort={toggleSort} title="Frequency — показов на пользователя">
                  Частота
                </SortableTh>
                <SortableTh align="right" sortKey="leads" sort={sort} onSort={toggleSort}>
                  Лиды
                </SortableTh>
                <SortableTh align="right" sortKey="last_seen" sort={sort} onSort={toggleSort}>
                  Посл. скан
                </SortableTh>
                <th className="bg-bg-1 border-b border-bg-5 w-10" />
              </tr>
            </thead>
            <tbody>
              {adsQuery.isLoading
                ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} />)
                : filteredAds.length === 0
                  ? null
                  : sortedAds.map((ad) => (
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

      {/* Pagination — серверная (offset/limit), общее число из X-Total-Count. */}
      {!adsQuery.isLoading &&
        !adsQuery.isError &&
        (() => {
          const total = adsQuery.data?.total ?? null;
          const pageLen = adsQuery.data?.items.length ?? 0;
          const multiPage = total != null ? total > PAGE_SIZE : pageLen >= PAGE_SIZE;
          if (!multiPage) return null;
          const hasNext = total != null ? offset + pageLen < total : pageLen >= PAGE_SIZE;
          return (
            <div className="flex items-center justify-between font-display text-[11.5px] text-bg-9 tracking-wide pb-4">
              <span>
                Показано{" "}
                <span className="text-bg-11">
                  {offset + 1}–{offset + pageLen}
                </span>
                {total != null ? (
                  <>
                    {" "}
                    из <span className="text-bg-11">{total}</span>
                  </>
                ) : null}
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
                  disabled={!hasNext}
                  onClick={() => {
                    setOffset(offset + PAGE_SIZE);
                    setSelectedIds(new Set());
                  }}
                >
                  Вперёд →
                </Button>
              </div>
            </div>
          );
        })()}

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

/** Заголовок колонки таблицы. title — расшифровка аббревиатуры при наведении. */
function Th({
  children,
  align = "left",
  title,
}: {
  children?: ReactNode;
  align?: "left" | "right";
  title?: string;
}) {
  return (
    <th
      title={title}
      className={cn(
        "bg-bg-1 border-b border-bg-5 py-3 px-3.5",
        "font-display text-[10px] tracking-[0.12em] uppercase text-bg-8 font-medium",
        "sticky top-0",
        align === "right" ? "text-right" : "text-left",
        title && "cursor-help",
      )}
    >
      {children}
    </th>
  );
}

/** Сортируемый заголовок колонки: клик переключает desc → asc → сброс. */
function SortableTh({
  children,
  align = "left",
  title,
  sortKey,
  sort,
  onSort,
}: {
  children?: ReactNode;
  align?: "left" | "right";
  title?: string;
  sortKey: SortKey;
  sort: { key: SortKey; dir: "asc" | "desc" } | null;
  onSort: (key: SortKey) => void;
}) {
  const active = sort?.key === sortKey;
  return (
    <th
      title={title}
      aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : "none"}
      className={cn(
        "bg-bg-1 border-b border-bg-5 py-3 px-3.5 sticky top-0",
        align === "right" ? "text-right" : "text-left",
      )}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          "inline-flex items-center gap-1 font-display text-[10px] tracking-[0.12em] uppercase font-medium transition-colors",
          align === "right" && "flex-row-reverse",
          active ? "text-accent" : "text-bg-8 hover:text-bg-11",
        )}
      >
        {children}
        <span aria-hidden="true" className="text-[9px] leading-none">
          {active ? (sort!.dir === "asc" ? "↑" : "↓") : "↕"}
        </span>
      </button>
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
        <div className="min-w-0">
          <div className="font-display text-[13px] text-bg-11 truncate tracking-tight">
            {ad.ad_name}
          </div>
          <div className="font-display text-[10.5px] text-bg-9 tracking-wide mt-0.5 truncate">
            {ad.fb_ad_id}
            {ad.offer_code ? ` · ${ad.offer_code}` : ""}
          </div>
          {ad.adset_name ? (
            <div className="font-display text-[10.5px] text-bg-8 tracking-wide mt-0.5 truncate">
              <span className="text-bg-7">адсет</span> {ad.adset_name}
            </div>
          ) : null}
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
          {ALERT_STATE_LABELS[ad.alert_state as keyof typeof ALERT_STATE_LABELS] ?? ad.alert_state}
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

/** Экранирование значения для CSV-ячейки. */
function csvCell(v: unknown): string {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
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
