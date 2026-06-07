/**
 * AdsPage — список объявлений с фильтрами и поиском.
 * Мобильная адаптация: карточки вместо таблицы.
 * Фильтр-чипы по alert_state, поиск на клиенте по имени/кампании/офферу.
 * Тап на карточку → /tma/ads/:id.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import {
  normalizeAlertState,
  alertStateToBadgeVariant,
  formatRelativeTime,
  formatSpend,
} from "@fb/shared";
import type { AdSnapshot } from "@fb/shared";
import { useDashboardAds } from "@/lib/api";
import { haptic } from "@/lib/tg";
import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  AlertStateBadge,
  AdCardSkeleton,
  EmptyState,
  ErrorState,
  Pill,
} from "@/components/ui";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/ads/")({
  component: AdsPage,
});

// ─── Фильтры ─────────────────────────────────────────────────────────────

interface FilterChip {
  id: string;
  label: string;
}

const FILTER_CHIPS: FilterChip[] = [
  { id: "", label: "Все" },
  { id: "stop_sent", label: "Стоп" },
  { id: "warning_sent", label: "Предупр." },
  { id: "normal", label: "Норма" },
  { id: "claimed", label: "В работе" },
  { id: "disabled", label: "Откл." },
];

// ─── Компонент карточки ───────────────────────────────────────────────────

interface AdCardProps {
  ad: AdSnapshot;
  onClick: () => void;
}

function AdCard({ ad, onClick }: AdCardProps) {
  const state = normalizeAlertState(ad.alert_state);
  const variant = alertStateToBadgeVariant(state);

  // Левая граница карточки по severity
  const leftBorderColor = cn(
    variant === "stop"    && "border-l-[var(--color-danger)]",
    variant === "warning" && "border-l-[var(--color-warning)]",
    variant === "claimed" && "border-l-[var(--color-info)]",
    variant === "disabled" && "border-l-[var(--color-bg-7)]",
    (variant === "normal" || !variant) && "border-l-[var(--color-bg-5)]",
  );

  const m = (ad as { metrics?: Record<string, string | number | null> }).metrics ?? {};

  // Коды правил из snap (stop + warning)
  const stopCodes: string[] = (ad as { stop_rule_codes?: string[] }).stop_rule_codes ?? [];
  const warnCodes: string[] = (ad as { warning_rule_codes?: string[] }).warning_rule_codes ?? [];

  return (
    <button
      type="button"
      onClick={() => {
        haptic.selection();
        onClick();
      }}
      className={cn(
        "w-full text-left",
        "bg-[var(--color-bg-1)] border border-[var(--color-bg-5)]",
        "border-l-4", leftBorderColor,
        "p-3 flex flex-col gap-2",
        "active:bg-[var(--color-bg-2)] transition-colors duration-[var(--dur-fast)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]",
      )}
    >
      {/* Строка 1: кампания / адсет */}
      {ad.campaign_name && (
        <p className="text-[11px] text-[var(--color-bg-9)] truncate leading-tight">
          {ad.campaign_name}
        </p>
      )}

      {/* Строка 2: имя + badge */}
      <div className="flex items-start justify-between gap-2">
        <p className="text-[14px] font-medium text-[var(--color-bg-11)] leading-tight flex-1 min-w-0 break-words">
          {ad.ad_name ?? ad.fb_ad_id}
        </p>
        <AlertStateBadge state={state} />
      </div>

      {/* Оффер */}
      {ad.offer_code && (
        <Pill variant="accent" className="self-start">{ad.offer_code}</Pill>
      )}

      {/* Метрики 4 колонки */}
      <div className="grid grid-cols-4 gap-1 mt-1">
        {([
          { label: "Расход", value: formatSpend(m.spend) },
          { label: "CPC", value: formatSpend(m.cpc) },
          { label: "Лиды", value: m.leads != null ? String(m.leads) : "—" },
          {
            label: "Деп",
            value: m.deposits != null ? String(m.deposits) : "—",
            danger: Number(m.deposits) === 0 && Number(m.spend) > 0,
          },
        ] as { label: string; value: string; danger?: boolean }[]).map((metric) => (
          <div key={metric.label} className="flex flex-col gap-0.5">
            <p className="text-[10px] text-[var(--color-bg-9)] leading-none">{metric.label}</p>
            <p className={cn(
              "text-[12px] font-mono font-medium leading-none tabular-nums",
              metric.danger ? "text-[var(--color-danger)]" : "text-[var(--color-bg-11)]",
            )}>
              {metric.value}
            </p>
          </div>
        ))}
      </div>

      {/* Коды правил */}
      {(stopCodes.length > 0 || warnCodes.length > 0) && (
        <div className="flex flex-wrap gap-1 mt-1">
          {stopCodes.slice(0, 3).map((code) => (
            <Pill key={code} variant="stop">{code}</Pill>
          ))}
          {warnCodes.slice(0, 3).map((code) => (
            <Pill key={code} variant="warning">{code}</Pill>
          ))}
        </div>
      )}

      {/* Время последнего скана */}
      {ad.last_seen_at && (
        <p className="text-[10px] text-[var(--color-bg-9)] font-mono self-end">
          {formatRelativeTime(ad.last_seen_at)} назад
        </p>
      )}
    </button>
  );
}

// ─── Основная страница ────────────────────────────────────────────────────

function AdsPage() {
  const navigate = useNavigate();
  const [stateFilter, setStateFilter] = useState("");
  const [search, setSearch] = useState("");

  const { data: allAds = [], isLoading, isError, error, refetch, dataUpdatedAt } = useDashboardAds(
    stateFilter,
    search,
  );

  // Клиентский поиск (бэкенд отдаёт 300 объявлений без поискового фильтра)
  const filtered = useMemo(() => {
    if (!search.trim()) return allAds;
    const q = search.toLowerCase();
    return allAds.filter((ad) => {
      const name = (ad.ad_name ?? "").toLowerCase();
      const campaign = ((ad as { campaign_name?: string }).campaign_name ?? "").toLowerCase();
      const adset = ((ad as { adset_name?: string }).adset_name ?? "").toLowerCase();
      const offer = (ad.offer_code ?? "").toLowerCase();
      const id = ad.fb_ad_id.toLowerCase();
      return name.includes(q) || campaign.includes(q) || adset.includes(q) || offer.includes(q) || id.includes(q);
    });
  }, [allAds, search]);

  const lastScanLabel = dataUpdatedAt ? formatRelativeTime(new Date(dataUpdatedAt)) : null;

  return (
    <div className="flex flex-col gap-0">
      <MiniHeader
        eyebrow="Объявления"
        title={`${filtered.length} объявл.`}
        right={
          lastScanLabel ? (
            <span className="text-[11px] text-[var(--color-bg-9)] font-mono">{lastScanLabel}</span>
          ) : undefined
        }
      />

      {/* ── Фильтр-чипы ── */}
      <div
        role="group"
        aria-label="Фильтр по статусу"
        className="flex gap-1 px-4 py-2 overflow-x-auto scrollbar-none border-b border-[var(--color-bg-5)] bg-[var(--color-bg-0)]"
      >
        {FILTER_CHIPS.map((chip) => (
          <button
            key={chip.id}
            type="button"
            role="radio"
            aria-checked={stateFilter === chip.id}
            onClick={() => {
              haptic.selection();
              setStateFilter(chip.id);
            }}
            className={cn(
              "shrink-0 px-3 min-h-[32px] text-[12px] font-body whitespace-nowrap",
              "border transition-colors duration-[var(--dur-fast)]",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]",
              stateFilter === chip.id
                ? "bg-[var(--color-accent)] text-[#0a0a0b] border-[var(--color-accent)] font-semibold"
                : "bg-[var(--color-bg-0)] text-[var(--color-bg-9)] border-[var(--color-bg-5)] hover:border-[var(--color-bg-7)]",
            )}
          >
            {chip.label}
          </button>
        ))}
      </div>

      {/* ── Поиск ── */}
      <div className="relative px-4 py-2 bg-[var(--color-bg-0)] border-b border-[var(--color-bg-5)]">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Поиск по имени, кампании, офферу..."
          className={cn(
            "w-full min-h-[40px] px-3 pr-8",
            "bg-[var(--color-bg-2)] border border-[var(--color-bg-5)]",
            "text-[13px] text-[var(--color-bg-11)] placeholder-[var(--color-bg-8)] font-body",
            "focus:outline-none focus:border-[var(--color-accent)]",
          )}
        />
        {search && (
          <button
            type="button"
            aria-label="Очистить поиск"
            onClick={() => setSearch("")}
            className="absolute right-6 top-1/2 -translate-y-1/2 text-[var(--color-bg-9)] min-w-[44px] min-h-[44px] flex items-center justify-center"
          >
            ✕
          </button>
        )}
      </div>

      {/* ── Ошибка ── */}
      {isError && (
        <ErrorState
          message={(error as Error)?.message ?? "Ошибка загрузки"}
          onRetry={() => void refetch()}
        />
      )}

      {/* ── Список ── */}
      <div className="flex flex-col gap-px bg-[var(--color-bg-5)]">
        {isLoading ? (
          <>
            {[...Array(4)].map((_, i) => (
              <AdCardSkeleton key={i} />
            ))}
          </>
        ) : filtered.length === 0 ? (
          <div className="bg-[var(--color-bg-0)]">
            <EmptyState
              title="Объявлений не найдено"
              description={search ? "Попробуйте другой поиск или сбросьте фильтр" : "Нет объявлений в данном статусе"}
              action={
                search
                  ? { label: "Сбросить поиск", onClick: () => { setSearch(""); setStateFilter(""); } }
                  : undefined
              }
            />
          </div>
        ) : (
          filtered.map((ad) => (
            <AdCard
              key={ad.fb_ad_id}
              ad={ad}
              onClick={() => void navigate({ to: `/ads/${ad.fb_ad_id}` })}
            />
          ))
        )}
      </div>
    </div>
  );
}
