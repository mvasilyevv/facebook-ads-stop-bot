/**
 * Ads — рабочая лошадка оператора (канон design_handoff/ads-web.jsx).
 *
 * Раскладка (flex-колонка, заполняет высоту → таблица скроллится внутри):
 *   page-header: eyebrow «04 / УПРАВЛЕНИЕ · ОБЪЯВЛЕНИЯ» + h1 «Объявления»
 *               + 3 count-badge (Норма/Предупреждение/Стоп totals).
 *   FilterBar (search / state-pills / offer-dropdown / count / chips).
 *   AdsTable (виртуальная, fill height, internal scroll).
 *   keyboard-legend (J/K · X · D · Enter · /).
 *   [BulkActionBar — floating, при ≥1 выбранной].
 *   [ConfirmDialog DISABLE — confirm-with-typing].
 *   [AdDrawer — drawer деталей поверх таблицы (локальный стейт, scrim показывает
 *    таблицу под собой — как в эталоне). Deep-link /ads/$fbAdId — отдельный route.]
 *
 * MONEY-FLOW bulk-disable:
 *   1. Выбор строк → BulkActionBar.Disable → ConfirmDialog (confirmWord="DISABLE").
 *   2. onConfirm → useBulkDisable({ fb_ad_ids, reason c idempotency_token=randomUUID }).
 *   3. Успех → очистка выбора, инвалидация ["ads"]/["tasks","disable"]/["dashboard"].
 *
 * Keyboard: «/» фокус поиска · J/K|↑/↓ курсор · X выбор курсора · Enter drawer ·
 *           D disable выбранных · Esc закрыть/сбросить/blur.
 */

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Eyebrow } from "@/components/data/Eyebrow";
import { Badge } from "@/components/ui/Badge";
import { Kbd } from "@/components/ui/Kbd";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DensityToggle } from "@/components/ui/DensityToggle";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";

import { FilterBar, type AdsFilterState } from "@/components/domain/ads/FilterBar";
import { AdsTable } from "@/components/domain/ads/AdsTable";
import { BulkActionBar } from "@/components/domain/ads/BulkActionBar";
import { AdDrawer } from "@/components/domain/ads/AdDrawer";

import { useAds, useBulkDisable, useBulkSnooze, useDeleteAds } from "@/lib/api/ads";
import { useDashboardStats } from "@/lib/api/dashboard";
import { useRealtimeInvalidation } from "@/lib/websocket/useRealtimeInvalidation";
import { useUiStore, DENSITY_ROW_HEIGHT } from "@/stores/ui";

import { ALERT_STATE_LABELS, type AdSnapshot, type AlertState } from "@fb/shared";
import { adAccountId } from "@/components/domain/ads/adHelpers";

// ─── Route ────────────────────────────────────────────────────────────────────

/** Deep-link фильтра состояния: /ads?state=warning_sent,stop_sent (клик по KPI). */
interface AdsSearch {
  state?: string;
}

export const Route = createFileRoute("/ads/")({
  component: AdsPage,
  validateSearch: (search: Record<string, unknown>): AdsSearch => ({
    state: typeof search.state === "string" && search.state ? search.state : undefined,
  }),
});

/** Парсит ?state=a,b в Set валидных alert_state (мусорные токены отбрасываются). */
function parseStateParam(raw: string | undefined): Set<AlertState> {
  const out = new Set<AlertState>();
  for (const tok of (raw ?? "").split(",")) {
    const t = tok.trim();
    if (t && t in ALERT_STATE_LABELS) out.add(t as AlertState);
  }
  return out;
}

// Тянем большой батч строк (cursor-пагинация для 1000+: один крупный запрос,
// клиентская фильтрация/сортировка поверх — как в эталоне).
const FETCH_LIMIT = 1000;

// ─── Компонент ────────────────────────────────────────────────────────────────

function AdsPage() {
  useRealtimeInvalidation();
  const navigate = useNavigate({ from: "/ads/" });
  const { state: stateParam } = Route.useSearch();

  // ── Фильтры (state-фильтр инициализируется из ?state= — deep-link с Dashboard) ──
  const [filters, setFilters] = useState<AdsFilterState>(() => ({
    search: "",
    selectedStates: parseStateParam(stateParam),
    selectedOffers: new Set<string>(),
    selectedAccounts: new Set<string>(),
  }));

  // URL ↔ state-фильтр: тоггл пиллов обновляет ?state= (replace, без истории) —
  // текущий вид всегда можно шарить ссылкой.
  useEffect(() => {
    const next = [...filters.selectedStates].sort().join(",") || undefined;
    if (next !== (stateParam || undefined)) {
      void navigate({ search: { state: next }, replace: true });
    }
    // navigate стабилен; stateParam в deps вызвал бы цикл при внешней навигации.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.selectedStates]);

  // ── Выбор / курсор / drawer ──────────────────────────────────────────────
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [cursor, setCursor] = useState(-1);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  // Drawer — локальный стейт (scrim показывает таблицу под собой, как в эталоне).
  const [drawerAd, setDrawerAd] = useState<AdSnapshot | null>(null);

  const searchRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // ── Плотность строк ────────────────────────────────────────────────────────
  const density = useUiStore((s) => s.density);
  const rowHeight = DENSITY_ROW_HEIGHT[density];

  // ── Данные ──────────────────────────────────────────────────────────────────
  // state-фильтр уходит на сервер; search/offer — клиентские (как в эталоне).
  const alertStatesParam =
    filters.selectedStates.size > 0 ? [...filters.selectedStates].join(",") : undefined;

  const { data, isLoading, isError, error, refetch } = useAds({
    alert_states: alertStatesParam,
    limit: FETCH_LIMIT,
    offset: 0,
  });

  const statsQ = useDashboardStats();

  const allRows = useMemo<AdSnapshot[]>(() => data?.data ?? [], [data]);

  // Offer-опции из загруженных данных.
  const offerOptions = useMemo(() => {
    const set = new Set<string>();
    allRows.forEach((r) => r.offer_code && set.add(r.offer_code));
    return [...set].sort();
  }, [allRows]);

  // Кабинеты из загруженных данных (мульти-кабинет; ≤1 — dropdown скрыт в FilterBar).
  const accountOptions = useMemo(() => {
    const set = new Set<string>();
    allRows.forEach((r) => {
      const acc = adAccountId(r);
      if (acc) set.add(acc);
    });
    return [...set].sort();
  }, [allRows]);

  // ── Клиентская фильтрация + сортировка по spend desc ────────────────────
  const rows = useMemo<AdSnapshot[]>(() => {
    const q = filters.search.trim().toLowerCase();
    const offers = filters.selectedOffers;
    const accounts = filters.selectedAccounts;
    const out = allRows.filter((r) => {
      if (q) {
        const hit =
          r.ad_name.toLowerCase().includes(q) ||
          r.fb_ad_id.includes(q) ||
          (r.offer_code?.toLowerCase().includes(q) ?? false);
        if (!hit) return false;
      }
      if (offers.size > 0 && !(r.offer_code && offers.has(r.offer_code))) return false;
      if (accounts.size > 0) {
        const acc = adAccountId(r);
        if (!(acc && accounts.has(acc))) return false;
      }
      return true;
    });
    // Сортировка по spend desc (как в эталоне).
    out.sort((a, b) => {
      const sa = Number.parseFloat(a.metrics?.spend ?? "0") || 0;
      const sb = Number.parseFloat(b.metrics?.spend ?? "0") || 0;
      return sb - sa;
    });
    return out;
  }, [allRows, filters.search, filters.selectedOffers, filters.selectedAccounts]);

  // Курсор не должен выходить за пределы после фильтрации.
  useEffect(() => {
    setCursor((c) => (c >= rows.length ? rows.length - 1 : c));
  }, [rows.length]);

  // ── Мутации ──────────────────────────────────────────────────────────────
  const bulkDisable = useBulkDisable();
  const bulkSnooze = useBulkSnooze();
  const deleteAds = useDeleteAds();

  // ── Колбэки фильтров ───────────────────────────────────────────────────────
  const toggleState = useCallback((s: AlertState) => {
    setFilters((p) => {
      const next = new Set(p.selectedStates);
      if (next.has(s)) { next.delete(s); } else { next.add(s); }
      return { ...p, selectedStates: next };
    });
  }, []);

  const toggleOffer = useCallback((o: string) => {
    setFilters((p) => {
      const next = new Set(p.selectedOffers);
      if (next.has(o)) { next.delete(o); } else { next.add(o); }
      return { ...p, selectedOffers: next };
    });
  }, []);

  const toggleAccount = useCallback((a: string) => {
    setFilters((p) => {
      const next = new Set(p.selectedAccounts);
      if (next.has(a)) { next.delete(a); } else { next.add(a); }
      return { ...p, selectedAccounts: next };
    });
  }, []);

  const clearAll = useCallback(() => {
    setFilters({
      search: "",
      selectedStates: new Set(),
      selectedOffers: new Set(),
      selectedAccounts: new Set(),
    });
  }, []);

  // ── Выбор ──────────────────────────────────────────────────────────────────
  const toggleSelect = useCallback((id: string) => {
    setSelected((p) => {
      const n = new Set(p);
      if (n.has(id)) { n.delete(id); } else { n.add(id); }
      return n;
    });
  }, []);

  const clearSelection = useCallback(() => setSelected(new Set()), []);

  const selectAll = useCallback(() => {
    setSelected((prev) => {
      // Если уже все выбраны — снимаем; иначе выбираем все.
      if (prev.size === rows.length && rows.length > 0) return new Set();
      return new Set(rows.map((r) => r.fb_ad_id));
    });
  }, [rows]);

  // ── Открыть drawer ───────────────────────────────────────────────────────
  const openDrawer = useCallback((ad: AdSnapshot) => setDrawerAd(ad), []);

  // ── MONEY: bulk disable ────────────────────────────────────────────────────
  async function handleDisableConfirm() {
    const ids = [...selected];
    if (ids.length === 0) return;
    // idempotency_token = crypto.randomUUID() — отдельное поле, защита от двойного сабмита.
    const res = await bulkDisable.mutateAsync({
      fb_ad_ids: ids,
      idempotency_token: crypto.randomUUID(),
      reason: "bulk-disable via dashboard",
    });
    clearSelection();
    toast.success(`Создано ${res?.created?.length ?? ids.length} disable-задач`);
  }

  // ── Hard-delete выбранных из каталога (необратимо) ──────────────────────────
  async function handleDeleteConfirm() {
    const ids = [...selected];
    if (ids.length === 0) return;
    const res = await deleteAds.mutateAsync(ids);
    clearSelection();
    toast.success(`Удалено ${res?.count ?? ids.length} объявлений из базы`);
  }

  // ── Snooze выбранных ────────────────────────────────────────────────────
  function handleBulkSnooze(minutes: number) {
    const ids = [...selected];
    if (ids.length === 0) return;
    void bulkSnooze.mutateAsync({ fb_ad_ids: ids, minutes });
    clearSelection();
    toast.success(`Snooze ${ids.length} объявлений на ${minutes}м`);
  }

  // ── Keyboard nav ───────────────────────────────────────────────────────────
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      // В инпуте — только Esc (blur), остальное не перехватываем.
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") {
        if (e.key === "Escape") (target as HTMLInputElement).blur();
        return;
      }
      if (e.key === "/") {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setCursor((c) => Math.min(rows.length - 1, c + 1));
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setCursor((c) => Math.max(0, c - 1));
      } else if (e.key === "x" && cursor >= 0 && rows[cursor]) {
        e.preventDefault();
        toggleSelect(rows[cursor]!.fb_ad_id);
      } else if (e.key === "Enter" && cursor >= 0 && rows[cursor]) {
        e.preventDefault();
        openDrawer(rows[cursor]!);
      } else if (e.key === "d" && selected.size > 0) {
        e.preventDefault();
        setConfirmOpen(true);
      } else if (e.key === "Escape") {
        // Приоритет: drawer (закроется сам через Radix) → иначе сброс выбора.
        if (!drawerAd && selected.size > 0) clearSelection();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, cursor, selected.size, drawerAd, toggleSelect, openDrawer, clearSelection]);

  // Скроллим курсор во вью при навигации J/K.
  useEffect(() => {
    if (cursor < 0 || !scrollRef.current) return;
    const el = scrollRef.current;
    const top = cursor * rowHeight;
    const bottom = top + rowHeight;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (bottom > el.scrollTop + el.clientHeight) el.scrollTop = bottom - el.clientHeight;
  }, [cursor, rowHeight]);

  // ── Totals для count-badge (по всему кабинету, из stats) ────────────────
  const stats = statsQ.data;

  return (
    <div className="flex flex-col h-full min-h-0" aria-label="Объявления">
      {/* ── Page header ───────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between mb-5 shrink-0">
        <div>
          <Eyebrow num="04">УПРАВЛЕНИЕ · ОБЪЯВЛЕНИЯ</Eyebrow>
          <h1
            className="font-display text-[30px] font-medium text-bg-11 mt-2"
            style={{ letterSpacing: "-0.02em" }}
          >
            Объявления
          </h1>
        </div>
        <div className="flex gap-2.5 pt-1">
          <CountBadge variant="normal" value={stats?.ads_in_normal} />
          <CountBadge variant="warning" value={stats?.ads_in_warning} />
          <CountBadge variant="stop" value={stats?.ads_in_stop} />
        </div>
      </div>

      {/* ── Filter bar ────────────────────────────────────────────────────── */}
      <div className="mb-3 shrink-0">
        <FilterBar
          filterState={filters}
          offerOptions={offerOptions}
          accountOptions={accountOptions}
          count={rows.length}
          searchRef={searchRef}
          onSearchChange={(v) => setFilters((p) => ({ ...p, search: v }))}
          onStateToggle={toggleState}
          onOfferToggle={toggleOffer}
          onAccountToggle={toggleAccount}
          onClearAll={clearAll}
        />
      </div>

      {/* ── Таблица ───────────────────────────────────────────────────────── */}
      {isError ? (
        <ErrorState
          title="Не удалось загрузить объявления."
          error={error}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <TableSkeleton rowHeight={rowHeight} />
      ) : rows.length === 0 ? (
        <div className="flex-1 border border-bg-6 flex items-center justify-center">
          <EmptyState title="Объявлений нет" description="Попробуйте сбросить фильтры." />
        </div>
      ) : (
        <AdsTable
          rows={rows}
          selected={selected}
          cursor={cursor}
          rowHeight={rowHeight}
          scrollRef={scrollRef}
          onToggleSelect={toggleSelect}
          onOpen={openDrawer}
          onSelectAll={selectAll}
        />
      )}

      {/* ── Keyboard legend + density ─────────────────────────────────────── */}
      <div className="mt-2.5 flex items-center gap-3.5 shrink-0 text-[11px] text-bg-8 font-display">
        <Legend k="J/K" label="навигация" />
        <Legend k="X" label="выбор" />
        <Legend k="D" label="disable" />
        <Legend k="Enter" label="детали" />
        <Legend k="/" label="поиск" />
        <div className="flex-1" />
        {/* Плотность строк (persist в localStorage через ui-store) */}
        <DensityToggle />
      </div>

      {/* ── Bulk action bar ───────────────────────────────────────────────── */}
      {selected.size > 0 && (
        <BulkActionBar
          count={selected.size}
          isPending={bulkDisable.isPending || bulkSnooze.isPending || deleteAds.isPending}
          onDisable={() => setConfirmOpen(true)}
          onSnooze={handleBulkSnooze}
          onDelete={() => setConfirmDeleteOpen(true)}
          onClear={clearSelection}
        />
      )}

      {/* ── MONEY: confirm-with-typing DISABLE ────────────────────────────── */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Отключить ${selected.size} объявлений?`}
        description={`Будет создано ${selected.size} disable-задач в outbox. Действие необратимо без ручного включения.`}
        confirmWord="DISABLE"
        confirmLabel={`Отключить ${selected.size}`}
        confirmVariant="danger"
        onConfirm={handleDisableConfirm}
      />

      {/* ── confirm-with-typing DELETE (hard-delete из каталога) ──────────── */}
      <ConfirmDialog
        open={confirmDeleteOpen}
        onOpenChange={setConfirmDeleteOpen}
        title={`Удалить ${selected.size} объявлений из базы?`}
        description={`Безвозвратное удаление из каталога вместе со всеми метриками, алертами и историей (каскад). Восстановить нельзя — объявления вернутся только при следующем скане, если ещё существуют в кабинете.`}
        confirmWord="DELETE"
        confirmLabel={`Удалить ${selected.size}`}
        confirmVariant="danger"
        onConfirm={handleDeleteConfirm}
      />

      {/* ── Drawer деталей (поверх таблицы) ────────────────────────────────── */}
      {drawerAd && <AdDrawer ad={drawerAd} onClose={() => setDrawerAd(null)} />}
    </div>
  );
}

// ─── Count-badge в шапке ────────────────────────────────────────────────────

function CountBadge({
  variant,
  value,
}: {
  variant: "normal" | "warning" | "stop";
  value: number | undefined;
}) {
  return (
    <Badge variant={variant} size="md">
      {value != null ? value.toLocaleString("en-US") : "—"}
    </Badge>
  );
}

// ─── Keyboard-legend item ───────────────────────────────────────────────────

function Legend({ k, label }: { k: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <Kbd className="h-[18px] min-w-[18px] px-1 text-bg-9">{k}</Kbd>
      <span>{label}</span>
    </span>
  );
}

// ─── Skeleton таблицы ────────────────────────────────────────────────────────

function TableSkeleton({ rowHeight }: { rowHeight: number }) {
  return (
    <div className="flex-1 border border-bg-6 min-h-0 overflow-hidden" aria-label="Загрузка">
      <div className="h-8 bg-bg-2 border-b border-bg-6" />
      <div className="flex flex-col">
        {Array.from({ length: 14 }).map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-3 px-3 border-b border-bg-5"
            style={{ height: rowHeight }}
          >
            <Skeleton width={15} height={15} />
            <Skeleton width={40} height={24} />
            <Skeleton height={13} className="flex-1 max-w-[280px]" />
            <Skeleton width={60} height={18} className="ml-auto" />
          </div>
        ))}
      </div>
    </div>
  );
}
