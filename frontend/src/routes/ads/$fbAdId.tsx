import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";

import { formatZonedDateTime, timezoneEvidenceLabel } from "@fb/shared/format/time";
import { formatDecimalValue, formatSpend } from "@fb/shared/format/number";
import {
  confirmedOperatorCurrency,
  formatOperatorCount,
  operatorDeliveryLabel,
} from "@fb/shared/operator/adsViewModel";
import { describeStopProximity } from "@fb/shared/operator/stopProximity";
import { adsForRealtimeState, severityForDataState } from "@fb/shared/operator/viewModel";
import { DataStateBadge, DataStateNotice, StopProximityReadout } from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { EmptyState } from "@/components/ui/EmptyState";
import {
  OperatorCardSkeleton,
  OperatorPageBoundary,
  OperatorUnavailableState,
} from "@/components/layout/OperatorPageBoundary";
import { AdCommandButtons, OperatorSeverityBadge } from "@/features/operator/OperatorAds";
import { operatorProblemMessage, useOperatorAds } from "@/lib/api/operator";

export const Route = createFileRoute("/ads/$fbAdId")({ component: AdDetailRoute });

function AdDetailRoute() {
  const { fbAdId } = Route.useParams();
  const realtimeStatus = useOperatorRealtimeStatus();
  const ads = useOperatorAds({ search: fbAdId, page: 1, page_size: 10 });
  const displayPayload = ads.data
    ? adsForRealtimeState(ads.data, realtimeStatus === "connected" && !ads.isError)
    : null;
  const ad = displayPayload?.rows.find((candidate) => candidate.fb_ad_id === fbAdId) ?? null;

  if (ads.isError && !ads.data) {
    return (
      <OperatorPageBoundary
        eyebrowNum="02"
        eyebrow="РЕКЛАМА · ОБЪЯВЛЕНИЯ"
        title="Карточка объявления"
        navigation={<AdBreadcrumb />}
      >
        <OperatorUnavailableState
          title="Карточка объявления недоступна"
          resource="карточку объявления"
          details={operatorProblemMessage(ads.error)}
          onRetry={() => void ads.refetch()}
        />
      </OperatorPageBoundary>
    );
  }

  if (ads.isPending && !ads.data) {
    return (
      <OperatorPageBoundary
        eyebrowNum="02"
        eyebrow="РЕКЛАМА · ОБЪЯВЛЕНИЯ"
        title="Карточка объявления"
        navigation={<AdBreadcrumb />}
      >
        <OperatorCardSkeleton label="Загрузка объявления" />
      </OperatorPageBoundary>
    );
  }

  if (!ad && displayPayload?.state === "empty") {
    return (
      <OperatorPageBoundary
        eyebrowNum="02"
        eyebrow="РЕКЛАМА · ОБЪЯВЛЕНИЯ"
        title="Карточка объявления"
        navigation={<AdBreadcrumb />}
      >
        <EmptyState
          title="Объявление не найдено"
          description="Оно отсутствует в актуальном операторском каталоге или ссылка устарела."
          action={
            <Link
              to="/ads"
              className="inline-flex min-h-11 items-center rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] px-4 text-[14px] text-bg-11"
            >
              К объявлениям
            </Link>
          }
        />
      </OperatorPageBoundary>
    );
  }

  if (!ad) {
    return (
      <OperatorPageBoundary
        eyebrowNum="02"
        eyebrow="РЕКЛАМА · ОБЪЯВЛЕНИЯ"
        title="Карточка объявления"
        navigation={<AdBreadcrumb />}
      >
        <EmptyState
          title="Карточка не подтверждена"
          description="Дождитесь сверки live-снимка. Отсутствие строки не считается подтверждённым нулём."
        />
      </OperatorPageBoundary>
    );
  }
  const scope = displayPayload?.scope;
  const currency = confirmedOperatorCurrency(scope);
  const timestampTimezone =
    scope?.cabinet_timezone_state === "single" ? scope.cabinet_timezone : scope?.display_timezone;
  const timezoneLabel = scope
    ? timezoneEvidenceLabel(scope.cabinet_timezone, scope.cabinet_timezone_state)
    : timezoneEvidenceLabel(null, "unknown");
  const timezoneContext =
    scope?.cabinet_timezone_state === "single"
      ? timezoneLabel
      : `${timezoneLabel}${scope?.display_timezone ? ` · отображение ${scope.display_timezone}` : ""}`;

  return (
    <article className="mx-auto max-w-5xl">
      <Link
        to="/ads"
        className="mb-4 inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] px-2 text-[14px] text-bg-9 outline-none hover:text-bg-11 focus-visible:ring-2 focus-visible:ring-accent"
      >
        <ArrowLeft aria-hidden="true" size={16} /> Объявления
      </Link>

      <header className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5 sm:p-7">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <OperatorSeverityBadge severity={severityForDataState(ad.severity, ad.data_state)} />
              <DataStateBadge state={ad.data_state} />
            </div>
            <h1 className="mt-4 break-words font-display text-[clamp(28px,5vw,44px)] font-medium leading-tight text-bg-11">
              {ad.name}
            </h1>
            <p className="mt-2 break-all font-numeric text-[13px] text-bg-8">
              Идентификатор объявления в Meta: {ad.fb_ad_id}
            </p>
          </div>
          <AdCommandButtons ad={ad} />
        </div>
        {ad.data_state !== "ready" ? (
          <div className="mt-5">
            <DataStateNotice state={ad.data_state} />
          </div>
        ) : null}
      </header>

      <section
        className="mt-4 rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5"
        aria-labelledby="ad-stop-proximity"
      >
        <h2 id="ad-stop-proximity" className="m-0 font-display text-[20px] text-bg-11">
          До стопа
        </h2>
        <div className="mt-4 max-w-md">
          <StopProximityReadout proximity={describeStopProximity(ad.rule_context, { currency })} />
        </div>
      </section>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1.2fr_.8fr]">
        <section
          className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5"
          aria-labelledby="ad-economy"
        >
          <h2 id="ad-economy" className="m-0 font-display text-[20px] text-bg-11">
            Экономика и воронка
          </h2>
          <dl className="mt-5 grid grid-cols-2 gap-x-5 gap-y-6 sm:grid-cols-4">
            <Metric label="Расход" value={formatSpend(ad.metrics.spend, currency)} />
            <Metric label="Показы" value={formatOperatorCount(ad.metrics.impressions)} />
            <Metric label="Клики" value={formatOperatorCount(ad.metrics.clicks)} />
            <Metric label="Регистрации" value={formatOperatorCount(ad.metrics.registrations)} />
            <Metric label="FTD" value={formatOperatorCount(ad.metrics.ftd)} />
            <Metric label="Депозиты" value={formatOperatorCount(ad.metrics.confirmed_deposits)} />
            <Metric label="CPC" value={formatSpend(ad.metrics.cpc, currency)} />
            <Metric
              label="Цена регистрации"
              value={formatSpend(ad.metrics.cost_per_registration, currency)}
            />
            <Metric label="Цена депозита" value={formatSpend(ad.metrics.cost_per_ftd, currency)} />
            <Metric label="Частота" value={formatDecimalValue(ad.metrics.frequency)} />
          </dl>
        </section>

        <section
          className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5"
          aria-labelledby="ad-context"
        >
          <h2 id="ad-context" className="m-0 font-display text-[20px] text-bg-11">
            Контекст
          </h2>
          <dl className="mt-5 grid gap-4 text-[14px]">
            <Field label="Доставка" value={operatorDeliveryLabel(ad.delivery_status)} />
            <Field label="Кампания" value={ad.campaign_name} />
            <Field label="Адсет" value={ad.adset_name} />
            <Field label="Кабинет" value={ad.account_id ?? "Не указан"} />
            <Field
              label="Данные на"
              value={timestampTimezone ? formatZonedDateTime(ad.as_of, timestampTimezone) : "—"}
            />
            <Field label="Часовой пояс" value={timezoneContext} />
          </dl>
        </section>
      </div>
    </article>
  );
}

function AdBreadcrumb() {
  return (
    <Link
      to="/ads"
      className="inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] px-2 text-[14px] text-bg-9 outline-none hover:text-bg-11 focus-visible:ring-2 focus-visible:ring-accent"
    >
      <ArrowLeft aria-hidden="true" size={16} /> Объявления
    </Link>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[12px] text-bg-8">{label}</dt>
      <dd className="mt-1 font-numeric text-[18px] text-bg-11">{value}</dd>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1 border-b border-[var(--color-hairline)] pb-3 last:border-0">
      <dt className="text-[12px] text-bg-8">{label}</dt>
      <dd className="m-0 break-words text-bg-11">{value}</dd>
    </div>
  );
}
