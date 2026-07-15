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
 *
 * Фильтры/выбор/keyboard-nav вынесены в хуки (lib/hooks/useAds*) — этот файл
 * держит data-fetching, money-mutations (disable/delete) и композицию JSX.
 */

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";

import { Eyebrow } from "@/components/data/Eyebrow";
import { Badge } from "@/components/ui/Badge";
import { Kbd } from "@/components/ui/Kbd";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DensityToggle } from "@/components/ui/DensityToggle";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import { MetaDelayedNote, TrackerLiveStrip } from "@/components/data/SourceStatus";

import { FilterBar } from "@/components/domain/ads/FilterBar";
import { AdsTable } from "@/components/domain/ads/AdsTable";
import { BulkActionBar } from "@/components/domain/ads/BulkActionBar";
import { AdDrawer } from "@/components/domain/ads/AdDrawer";

import { useAds, useBulkDisable, useDeleteAds } from "@/lib/api/ads";
import { useDashboardStats } from "@/lib/api/dashboard";
import { useStatsToday } from "@/lib/api/stats";
import { useRealtimeInvalidation } from "@/lib/websocket/useRealtimeInvalidation";
import { useUiStore, DENSITY_ROW_HEIGHT } from "@/stores/ui";
import { useAdsFilterState } from "@/lib/hooks/useAdsFilterState";
import { useFilteredAdsRows } from "@/lib/hooks/useFilteredAdsRows";
import { useAdsSelection } from "@/lib/hooks/useAdsSelection";
import { useAdsKeyboardNav } from "@/lib/hooks/useAdsKeyboardNav";

import { ALERT_STATE_LABELS, type AdSnapshot, type AlertState } from "@fb/shared";

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

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  // Drawer — локальный стейт (scrim показывает таблицу под собой, как в эталоне).
  const [drawerAd, setDrawerAd] = useState<AdSnapshot | null>(null);

  const searchRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // ── Плотность строк ────────────────────────────────────────────────────────
  // +14px на вторую строку ячейки (кампания · адсет) под названием — родитель
  // объявления нужен, чтобы различать дубли (два CR004 в разных адсетах).
  const density = useUiStore((s) => s.density);
  const rowHeight = DENSITY_ROW_HEIGHT[density] + 14;

  // ── Фильтры (state-фильтр инициализируется из ?state= — deep-link с Dashboard) ──
  // selectedStates нужен ДО fetch'а (server-side query-параметр), поэтому состояние
  // фильтров и производные данные (rows/options из загруженных строк) — два хука.
  const [initialStates] = useState(() => parseStateParam(stateParam));
  const {
    filters,
    setSearch,
    toggleState,
    toggleOffer,
    toggleAccount,
    toggleCampaign,
    toggleAdset,
    clearAll,
  } = useAdsFilterState(initialStates);

  // state-фильтр уходит на сервер; search/offer — клиентские (как в эталоне).
  const alertStatesParam =
    filters.selectedStates.size > 0 ? [...filters.selectedStates].join(",") : undefined;

  const { data, isLoading, isError, error, refetch } = useAds({
    alert_state: alertStatesParam, // M1: бэк-параметр называется alert_state (CSV)
    limit: FETCH_LIMIT,
    offset: 0,
  });

  const statsQ = useDashboardStats();
  const trackerQ = useStatsToday();

  const allRows = data?.data ?? [];
  const { rows, offerOptions, accountOptions, campaignOptions, adsetOptions } =
    useFilteredAdsRows(allRows, filters);

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

  // ── Выбор / курсор ───────────────────────────────────────────────────────
  const { selected, cursor, setCursor, toggleSelect, clearSelection, selectAll } =
    useAdsSelection(rows);

  // ── Мутации ──────────────────────────────────────────────────────────────
  const bulkDisable = useBulkDisable();
  const deleteAds = useDeleteAds();

  // ── Открыть drawer ───────────────────────────────────────────────────────
  const openDrawer = useCallback((ad: AdSnapshot) => setDrawerAd(ad), []);
  const requestDisableConfirm = useCallback(() => setConfirmOpen(true), []);

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
    // Partial-failure виден оператору (аудит 2026-07-12, H-8): молчание про failed
    // означало «все остановятся», пока часть адов продолжала жечь бюджет.
    const failed = res?.failed ?? [];
    const skippedNonDup = (res?.skipped ?? []).filter((s) => s.reason !== "duplicate");
    if (failed.length > 0) {
      toast.error(
        `Не удалось создать задач на отключение: ${failed.length}`,
        failed.map((f) => `${f.fb_ad_id}: ${f.reason}`).join("; "),
      );
    }
    if (skippedNonDup.length > 0) {
      toast.error(
        `Пропущено ${skippedNonDup.length} объявлений`,
        skippedNonDup.map((s) => `${s.fb_ad_id}: ${s.reason}`).join("; "),
      );
    }
    const createdCount = res?.created?.length ?? 0;
    if (createdCount > 0 || (failed.length === 0 && skippedNonDup.length === 0)) {
      toast.success(`Создано задач на отключение: ${createdCount}`);
    }
  }

  // ── Hard-delete выбранных из каталога (необратимо) ──────────────────────────
  async function handleDeleteConfirm() {
    const ids = [...selected];
    if (ids.length === 0) return;
    const res = await deleteAds.mutateAsync(ids);
    clearSelection();
    toast.success(`Удалено ${res?.count ?? ids.length} объявлений из базы`);
  }

  // ── Keyboard nav ───────────────────────────────────────────────────────────
  useAdsKeyboardNav({
    rows,
    cursor,
    setCursor,
    selectedSize: selected.size,
    drawerOpen: drawerAd !== null,
    searchRef,
    scrollRef,
    rowHeight,
    toggleSelect,
    openDrawer,
    clearSelection,
    requestDisableConfirm,
  });

  // ── Totals для count-badge (по всему кабинету, из stats) ────────────────
  const stats = statsQ.data;

  return (
    <div className="flex flex-col h-full min-h-0" aria-label="Объявления">
      {/* ── Page header ───────────────────────────────────────────────────── */}
      <div className="mb-5 flex shrink-0 flex-col items-start justify-between gap-3 sm:flex-row">
        <div>
          <Eyebrow num="04">УПРАВЛЕНИЕ · ОБЪЯВЛЕНИЯ</Eyebrow>
          <h1
            className="font-display text-[30px] font-medium text-bg-11 mt-2"
            style={{ letterSpacing: "-0.02em" }}
          >
            Объявления
          </h1>
        </div>
        <div className="flex flex-col items-end gap-2 pt-1">
          <MetaDelayedNote />
          <div className="flex flex-wrap gap-2" aria-label="Сводка по состояниям">
            <CountBadge variant="normal" value={stats?.ads_in_normal} />
            <CountBadge variant="warning" value={stats?.ads_in_warning} />
            <CountBadge variant="stop" value={stats?.ads_in_stop} />
          </div>
        </div>
      </div>

      <TrackerLiveStrip data={trackerQ.data} className="mb-3 shrink-0" />

      {/* ── Filter bar ────────────────────────────────────────────────────── */}
      <div className="mb-3 shrink-0">
        <FilterBar
          filterState={filters}
          offerOptions={offerOptions}
          accountOptions={accountOptions}
          campaignOptions={campaignOptions}
          adsetOptions={adsetOptions}
          count={rows.length}
          searchRef={searchRef}
          onSearchChange={setSearch}
          onStateToggle={toggleState}
          onOfferToggle={toggleOffer}
          onAccountToggle={toggleAccount}
          onCampaignToggle={toggleCampaign}
          onAdsetToggle={toggleAdset}
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
        <div className="flex-1 border border-[var(--hairline)] rounded-[var(--radius-3)] flex items-center justify-center">
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
        <Legend k="D" label="отключить" />
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
          isPending={bulkDisable.isPending || deleteAds.isPending}
          onDisable={requestDisableConfirm}
          onDelete={() => setConfirmDeleteOpen(true)}
          onClear={clearSelection}
        />
      )}

      {/* ── MONEY: confirm-with-typing DISABLE ────────────────────────────── */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Отключить ${selected.size} объявлений?`}
        description={`Будут созданы задачи на отключение: ${selected.size}. Вернуть объявления в показ можно только ручным включением.`}
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
      {drawerAd && (
        <AdDrawer
          ad={drawerAd}
          onClose={() => setDrawerAd(null)}
          trackerData={trackerQ.data?.tracker}
          trackerDataLoading={trackerQ.isLoading}
        />
      )}
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
  const labels = {
    normal: "Норма",
    warning: "Внимание",
    stop: "Стоп",
  } as const;
  return (
    <Badge variant={variant} size="md">
      <span>{labels[variant]}</span>
      <span className="ml-1 font-display tabular-nums">
        {value != null ? value.toLocaleString("ru-RU") : "—"}
      </span>
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
    <div className="flex-1 border border-[var(--hairline)] rounded-[var(--radius-3)] min-h-0 overflow-hidden" aria-label="Загрузка">
      <div className="h-8 bg-bg-2 border-b border-[var(--hairline)]" />
      <div className="flex flex-col">
        {Array.from({ length: 14 }).map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-3 px-3 border-b border-[var(--hairline)]"
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
