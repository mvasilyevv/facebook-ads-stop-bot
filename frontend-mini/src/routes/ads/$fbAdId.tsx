/**
 * AdDetail — полноэкранная страница объявления.
 * Маршрут: /ads/$fbAdId (file-based TanStack Router).
 *
 * API: useTmaAd(fbAdId) + useTmaDisable + useTmaSnooze + useTmaClaim из @/lib/api.
 * BackButton — нативный TG (TelegramBackButton в __root автоматически по паттерну /ads/.+).
 * TabBar скрывается (TabBar.tsx HIDDEN_ON: /^\/ads\/.+$/).
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  useTmaAd,
  useTmaDisable,
  useTmaSnooze,
  useTmaClaim,
} from "@/lib/api";
import {
  AlertStateBadge,
  Button,
  Card,
  ErrorState,
  Sheet,
  Skeleton,
} from "@/components/ui";
import { MetricsGrid, type MetricCell } from "@/components/domain/MetricsGrid";
import { AlertTimeline } from "@/components/domain/AlertTimeline";
import { normalizeAlertState, formatSpend, formatInt } from "@fb/shared";
import type { TmaAdMetrics } from "@/lib/api";
import { haptic, tgConfirm, tgAlert, openLink } from "@/lib/tg";

export const Route = createFileRoute("/ads/$fbAdId")({
  component: AdDetailPage,
});

// Минуты для кнопок снуза
const SNOOZE_OPTIONS = [30, 60, 120] as const;

function AdDetailPage() {
  const { fbAdId } = Route.useParams();
  const [snoozeOpen, setSnoozeOpen] = useState(false);

  const { data, isLoading, isError, error, refetch } = useTmaAd(fbAdId);
  const disable = useTmaDisable();
  const snooze = useTmaSnooze();
  const claim = useTmaClaim();

  const busy = disable.isPending || snooze.isPending || claim.isPending;

  /** Обработчик Disable — нативный confirm + мутация. */
  async function handleDisable() {
    haptic.impact("heavy");
    const ok = await tgConfirm("Отключить объявление через API?");
    if (!ok) return;
    try {
      await disable.mutateAsync({ fbAdId });
      await tgAlert("Задача отключения поставлена");
    } catch (e) {
      await tgAlert((e as Error).message ?? "Ошибка");
    }
  }

  /** Снуз на N минут. */
  async function handleSnooze(minutes: number) {
    haptic.selection();
    setSnoozeOpen(false);
    try {
      const res = await snooze.mutateAsync({ fbAdId, minutes });
      await tgAlert(`Снуз до ${new Date(res.snoozed_until).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`);
    } catch (e) {
      await tgAlert((e as Error).message ?? "Ошибка");
    }
  }

  /** Claim — взять под контроль вручную. */
  async function handleClaim() {
    haptic.selection();
    try {
      await claim.mutateAsync({ fbAdId });
      await tgAlert("Алерт снят");
    } catch (e) {
      await tgAlert((e as Error).message ?? "Ошибка");
    }
  }

  /** Открыть в Ads Manager */
  function handleOpenAdsManager() {
    if (!data?.account_id) return;
    haptic.selection();
    openLink(
      `https://adsmanager.facebook.com/adsmanager/manage/ads?act=${data.account_id}&selected_ad_ids=${fbAdId}`,
    );
  }

  // ─── Состояния загрузки/ошибки ───────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4 p-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="p-4">
        <ErrorState
          message={(error as Error | null)?.message ?? "Не удалось загрузить объявление"}
          onRetry={() => void refetch()}
        />
      </div>
    );
  }

  const {
    ad_name,
    campaign_name,
    adset_name,
    offer_code,
    state,
    snooze_until,
    can_open_in_ads_manager,
    recent_alerts = [],
  } = data;

  // metrics может прийти как TmaAdMetrics (из TmaAdDetail) — кастуем явно
  const metrics: TmaAdMetrics = (data.metrics ?? {}) as TmaAdMetrics;

  const normalized = normalizeAlertState(state);

  // Активный инцидент — Claim показывается только при warning_sent/stop_sent/claimed
  const hasIncident = ["warning_sent", "stop_sent", "claimed"].includes(normalized);

  // Снуз активен?
  const snoozeActive = snooze_until != null && new Date(snooze_until).getTime() > Date.now();

  // Ячейки метрик
  const metricCells: MetricCell[] = [
    {
      label: "Spend",
      value: formatSpend(metrics.spend),
    },
    {
      label: "Leads",
      value: metrics.leads != null ? formatInt(metrics.leads) : null,
    },
    {
      label: "Deposits",
      value: metrics.deposits != null ? formatInt(metrics.deposits) : null,
    },
    {
      label: "CPC",
      value: formatSpend(metrics.cpc),
    },
    {
      label: "CTR",
      value: metrics.ctr != null ? `${Number(metrics.ctr).toFixed(2)}%` : null,
    },
    {
      label: "Regs",
      value: metrics.registrations != null ? formatInt(metrics.registrations) : null,
    },
  ];

  return (
    <>
      <div className="flex flex-col gap-0 pb-6">
        {/* ─── Шапка ─────────────────────────────────────── */}
        <header className="px-4 pt-4 pb-3 border-b border-[var(--color-bg-5)] bg-[var(--color-bg-0)]">
          {/* Иерархия campaign → adset */}
          {campaign_name && (
            <p className="text-[10px] font-mono uppercase tracking-[0.08em] text-[var(--color-bg-9)] mb-0.5 truncate">
              {campaign_name}
            </p>
          )}
          {adset_name && (
            <p className="text-[10px] font-mono text-[var(--color-bg-8)] mb-1.5 truncate">
              {adset_name}
            </p>
          )}

          {/* Название + FSM-бейдж */}
          <div className="flex flex-wrap items-start gap-2 mb-2">
            <h1 className="text-[16px] font-display font-semibold text-[var(--color-bg-11)] leading-tight flex-1 min-w-0">
              {ad_name ?? fbAdId}
            </h1>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <AlertStateBadge state={state} />
            {offer_code && (
              <span className="font-mono text-[10px] px-[5px] py-[2px] bg-[var(--color-bg-3)] text-[var(--color-bg-10)]">
                {offer_code}
              </span>
            )}
            <span className="font-mono text-[10px] text-[var(--color-bg-9)] ml-auto">
              {fbAdId}
            </span>
          </div>

          {/* Снуз-баннер */}
          {snoozeActive && snooze_until && (
            <div className="mt-2 px-3 py-2 bg-[var(--color-warning-bg)] border border-[var(--color-warning)] flex items-center gap-2">
              <span className="text-[12px] font-mono text-[var(--color-warning)]">
                СНУЗ до{" "}
                {new Date(snooze_until).toLocaleTimeString("ru-RU", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </div>
          )}
        </header>

        {/* ─── Метрики ───────────────────────────────────── */}
        <section className="px-4 pt-4">
          <p className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-bg-9)] font-mono mb-2">
            Метрики
          </p>
          <MetricsGrid cells={metricCells} />
        </section>

        {/* ─── Лента алертов ─────────────────────────────── */}
        {recent_alerts.length > 0 && (
          <section className="px-4 pt-4">
            <Card eyebrow="История алертов" padding="sm">
              <AlertTimeline alerts={recent_alerts} />
            </Card>
          </section>
        )}

        {/* ─── Действия ──────────────────────────────────── */}
        <section className="px-4 pt-4 flex flex-col gap-2">
          <p className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-bg-9)] font-mono">
            Действия
          </p>

          {/* Disable */}
          <Button
            variant="danger"
            size="lg"
            fullWidth
            loading={disable.isPending}
            disabled={busy}
            onClick={() => void handleDisable()}
          >
            Отключить объявление
          </Button>

          {/* Снуз: кнопка → open Sheet */}
          <Button
            variant="secondary"
            size="md"
            fullWidth
            disabled={busy}
            onClick={() => { haptic.selection(); setSnoozeOpen(true); }}
          >
            Снуз...
          </Button>

          {/* Claim — только при активном инциденте */}
          {hasIncident && (
            <Button
              variant="secondary"
              size="md"
              fullWidth
              loading={claim.isPending}
              disabled={busy}
              onClick={() => void handleClaim()}
            >
              Снять алерт (Claim)
            </Button>
          )}

          {/* Открыть в Ads Manager */}
          {can_open_in_ads_manager && (
            <Button
              variant="ghost"
              size="md"
              fullWidth
              onClick={handleOpenAdsManager}
            >
              Открыть в Ads Manager ↗
            </Button>
          )}
        </section>
      </div>

      {/* ─── Sheet выбора снуза ─────────────────────────── */}
      <Sheet
        open={snoozeOpen}
        onClose={() => setSnoozeOpen(false)}
        eyebrow="Объявление"
        title="Снуз"
      >
        <div className="flex flex-col gap-2 pb-4">
          {SNOOZE_OPTIONS.map((min) => (
            <Button
              key={min}
              variant="secondary"
              size="lg"
              fullWidth
              loading={snooze.isPending}
              onClick={() => void handleSnooze(min)}
            >
              {min} минут
            </Button>
          ))}
        </div>
      </Sheet>
    </>
  );
}
