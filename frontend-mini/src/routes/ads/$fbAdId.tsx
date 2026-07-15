/**
 * AdDetail — полноэкранная страница объявления.
 * Маршрут: /ads/$fbAdId (file-based TanStack Router).
 *
 * Канон: Eyebrow «ОБЪЯВЛЕНИЕ», имя mono text-bg-11, AlertStateBadge + Pill offer_code,
 * danger-callout с RulePills, MetricsGrid 3 колонки, AlertTimeline, кнопки 44px.
 *
 * API: useTmaAd(fbAdId) + useTmaDisable + useTmaClaim.
 * BackButton — нативный TG (TelegramBackButton в __root по паттерну /ads/.+).
 * TabBar скрывается (TabBar.tsx HIDDEN_ON: /^\/ads\/.+$/).
 */
import { createFileRoute } from "@tanstack/react-router";
import {
  useTmaAd,
  useTmaDisable,
  useTmaClaim,
} from "@/lib/api";
import {
  AlertStateBadge,
  Button,
  Pill,
  Skeleton,
  EmptyState,
  ErrorState,
} from "@/components/ui";
import { Eyebrow, RulePills } from "@/components/data";
import { MetricsGrid } from "@/components/domain/MetricsGrid";
import type { MetricCell } from "@/components/domain/MetricsGrid";
import { AlertTimeline } from "@/components/domain/AlertTimeline";
import {
  normalizeAlertState,
  formatSpend,
  formatPercent,
  formatInt,
} from "@fb/shared";
import type { TmaAdMetrics } from "@/lib/api";
import { haptic, tgConfirm, tgAlert, openLink } from "@/lib/tg";

export const Route = createFileRoute("/ads/$fbAdId")({
  component: AdDetailPage,
});

function AdDetailPage() {
  const { fbAdId } = Route.useParams();

  const { data, isLoading, isError, error, refetch } = useTmaAd(fbAdId);
  const disable = useTmaDisable();
  const claim = useTmaClaim();

  const busy = disable.isPending || claim.isPending;

  /** Отключить через API: confirm → мутация. */
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

  /** Claim — снять алерт вручную. */
  async function handleClaim() {
    haptic.selection();
    try {
      await claim.mutateAsync({ fbAdId });
      await tgAlert("Алерт снят");
    } catch (e) {
      await tgAlert((e as Error).message ?? "Ошибка");
    }
  }

  /** Открыть в Ads Manager. */
  function handleOpenAdsManager() {
    if (!data?.account_id) return;
    haptic.selection();
    openLink(
      `https://adsmanager.facebook.com/adsmanager/manage/ads?act=${data.account_id}&selected_ad_ids=${fbAdId}`,
    );
  }

  // ─── Состояния ────────────────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4 p-4">
        {/* шапка */}
        <div className="space-y-2">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-3/4" />
          <div className="flex gap-2">
            <Skeleton className="h-5 w-16" />
            <Skeleton className="h-5 w-12" />
          </div>
        </div>
        {/* метрики */}
        <Skeleton className="h-28 w-full" />
        {/* таймлайн */}
        <Skeleton className="h-20 w-full" />
        {/* кнопки */}
        <div className="space-y-2">
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
        </div>
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
    can_open_in_ads_manager,
    recent_alerts = [],
  } = data;

  const metrics: TmaAdMetrics = (data.metrics ?? {}) as TmaAdMetrics;
  const normalized = normalizeAlertState(state);

  // Превью креатива: image приоритетнее thumb (крупнее). Нет ни того, ни другого → секции нет.
  const creativeSrc = data.creative_image_url || data.creative_thumb_url || null;

  // Инцидент активен → показываем Claim
  const hasIncident = ["warning_sent", "stop_sent", "claimed"].includes(normalized);

  // Сработавшие правила: TmaRecentAlert не содержит rule_codes — callout не рендерится
  const alertRuleCodes: string[] = [];
  // Собираем коды правил из recent_alerts (у TmaRecentAlert нет rule_codes — только reason_title)
  // Коды будут пустые, если backend не возвращает — callout рендерится только если есть коды

  // Ячейки метрик
  const cplValue = metrics.cost_per_lead != null ? parseFloat(String(metrics.cost_per_lead)) : null;
  const ctrValue = metrics.ctr != null ? parseFloat(String(metrics.ctr)) : null;

  const metricCells: MetricCell[] = [
    {
      label: "Spend",
      value: formatSpend(metrics.spend),
    },
    {
      label: "CPL",
      value: cplValue != null ? formatSpend(cplValue) : "—",
    },
    {
      label: "CTR",
      value: ctrValue != null ? formatPercent(ctrValue) : "—",
    },
    {
      label: "Leads",
      value: metrics.leads != null ? formatInt(metrics.leads) : "—",
    },
    {
      label: "Regs",
      value: metrics.registrations != null ? formatInt(metrics.registrations) : "—",
    },
    {
      label: "Deposits",
      value: metrics.deposits != null ? formatInt(metrics.deposits) : "—",
    },
  ];

  return (
    <div className="flex flex-col pb-8">
      {/* ── Шапка ─────────────────────────────────────────────────────────── */}
      <header
        className="px-4 pt-4 pb-3 border-b border-[var(--hairline)]"
        style={{ background: "var(--color-bg-0)" }}
      >
        {/* Eyebrow «ОБЪЯВЛЕНИЕ» + контекст кампании */}
        <Eyebrow className="mb-2.5">
          ОБЪЯВЛЕНИЕ
          {campaign_name ? (
            <>
              <span className="text-bg-7">·</span>
              <span className="text-bg-8 normal-case tracking-normal font-normal truncate max-w-[180px]">
                {campaign_name}
              </span>
            </>
          ) : null}
        </Eyebrow>

        {/* Имя объявления */}
        <h1
          className="font-display text-bg-11 mb-2.5 leading-tight"
          style={{ fontSize: 16, fontWeight: 500, letterSpacing: "-0.01em" }}
        >
          {ad_name ?? fbAdId}
        </h1>

        {/* FSM-бейдж + код оффера + кабинет (мульти-кабинет) */}
        <div className="flex items-center gap-2 flex-wrap">
          <AlertStateBadge state={state} withDot />
          {offer_code && (
            <Pill variant="accent">{offer_code}</Pill>
          )}
          {data.account_id && (
            <span
              className="font-display tabular-nums text-bg-9 border border-[var(--hairline)] rounded-[var(--radius-1)] px-1.5"
              style={{ fontSize: 10, lineHeight: "18px" }}
              title={`Кабинет ${data.account_id}`}
            >
              act {data.account_id}
            </span>
          )}
          {/* fb_ad_id как мелкая метка */}
          <span
            className="font-display tabular-nums text-bg-8 ml-auto"
            style={{ fontSize: 10 }}
          >
            {fbAdId}
          </span>
        </div>

        {/* Adset контекст */}
        {adset_name && (
          <p
            className="font-display text-bg-8 mt-1.5 truncate"
            style={{ fontSize: 10 }}
          >
            {adset_name}
          </p>
        )}

      </header>

      <div className="flex flex-col gap-5 p-4">
        {/* ── Danger-callout с правилами (если есть коды) ─────────────────── */}
        {alertRuleCodes.length > 0 && (
          <div
            style={{
              background: "var(--color-danger-bg)",
              borderLeft: "2px solid var(--color-danger)",
              border: "1px solid color-mix(in srgb, var(--color-danger) 30%, transparent)",
              borderLeftWidth: 2,
              borderRadius: "var(--radius-2)",
              padding: "10px 12px",
              display: "flex",
              gap: 6,
              flexWrap: "wrap" as const,
            }}
          >
            <RulePills codes={alertRuleCodes} />
          </div>
        )}

        {/* ── Креатив (превью, если бэк отдал URL) ───────────────────────── */}
        {creativeSrc && (
          <section>
            <Eyebrow className="mb-2.5">КРЕАТИВ</Eyebrow>
            <a
              href={creativeSrc}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => haptic.selection()}
              className="block rounded-[var(--radius-3)] overflow-hidden border border-[var(--hairline)]"
              style={{ background: "var(--color-bg-1)" }}
              aria-label="Открыть креатив в полном размере"
            >
              <img
                src={creativeSrc}
                alt="Превью креатива"
                loading="lazy"
                className="w-full max-h-[320px] object-contain"
              />
            </a>
          </section>
        )}

        {/* ── Метрики ────────────────────────────────────────────────────── */}
        <section>
          <Eyebrow className="mb-2.5">МЕТРИКИ</Eyebrow>
          {metricCells.length > 0 ? (
            <MetricsGrid cells={metricCells} />
          ) : (
            <EmptyState title="Нет данных" description="Метрики появятся после первого скана" />
          )}
        </section>

        {/* ── Лента алертов ──────────────────────────────────────────────── */}
        {recent_alerts.length > 0 && (
          <section>
            <Eyebrow className="mb-2.5">ИСТОРИЯ АЛЕРТОВ</Eyebrow>
            <div
              className="border border-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden"
              style={{ background: "var(--color-bg-1)" }}
            >
              <div className="px-3 py-0.5">
                <AlertTimeline alerts={recent_alerts} />
              </div>
            </div>
          </section>
        )}

        {/* ── Действия ──────────────────────────────────────────────────── */}
        <section className="flex flex-col gap-2.5">
          <Eyebrow className="mb-0.5">ДЕЙСТВИЯ</Eyebrow>

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
              Снять алерт
            </Button>
          )}

          {/* Disable — опасное действие */}
          <Button
            variant="danger"
            size="md"
            fullWidth
            loading={disable.isPending}
            disabled={busy}
            onClick={() => void handleDisable()}
          >
            Отключить объявление
          </Button>

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

        {/* ── Пустой стейт если нет алертов ─────────────────────────────── */}
        {recent_alerts.length === 0 && normalized === "normal" && (
          <EmptyState
            title="Нет активных алертов"
            description="Объявление в норме"
          />
        )}
      </div>
    </div>
  );
}
