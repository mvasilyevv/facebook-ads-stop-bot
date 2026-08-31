import { createFileRoute } from "@tanstack/react-router";

import {
  formatZonedDateTime,
  timezoneEvidenceLabel,
} from "@fb/shared/format/time";
import { formatDecimalValue, formatSpend } from "@fb/shared/format/number";
import {
  confirmedOperatorCurrency,
  formatOperatorCount,
  operatorDeliveryLabel,
} from "@fb/shared/operator/adsViewModel";
import { describeStopProximity } from "@fb/shared/operator/stopProximity";
import {
  adsForRealtimeState,
  severityForDataState,
} from "@fb/shared/operator/viewModel";
import {
  DataStateBadge,
  DataStateNotice,
  StopProximityReadout,
} from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { EmptyState, ErrorState, Skeleton } from "@/components/ui";
import {
  MiniAdCommand,
  MiniSeverityBadge,
} from "@/features/operator/OperatorAds";
import { operatorProblemMessage, useOperatorAds } from "@/lib/operatorApi";

export const Route = createFileRoute("/ads/$fbAdId")({
  component: AdDetailRoute,
});

function AdDetailRoute() {
  const { fbAdId } = Route.useParams();
  return <MiniAdDetail fbAdId={fbAdId} />;
}

// Экспортируется отдельно: /open (routes/open.tsx) переиспользует этот же
// вид без смены URL, чтобы id цели никогда не попадал в адресную строку.
// Этот компонент завязан на @fb/operator-ui + OperatorAds — те же модули,
// что и главному экрану (OperatorMiniDashboard), поэтому статический
// импорт дешевле по итоговому бюджету, чем вынос в отдельный ленивый чанк
// (см. commit history / отчёт issue #349: разбиение создаёт отдельный
// gzip-поток хуже сжимаемый, чем общий с уже нужным дашборду кодом).
export function MiniAdDetail({ fbAdId }: { fbAdId: string }) {
  const realtimeStatus = useOperatorRealtimeStatus();
  const ads = useOperatorAds({ search: fbAdId, page: 1, page_size: 10 });
  const displayPayload = ads.data
    ? adsForRealtimeState(
        ads.data,
        realtimeStatus === "connected" && !ads.isError,
      )
    : null;
  const ad =
    displayPayload?.rows.find((candidate) => candidate.fb_ad_id === fbAdId) ??
    null;

  if (ads.isPending && !ads.data) {
    return (
      <div
        role="status"
        aria-label="Загрузка объявления"
        className="grid gap-3 p-4"
      >
        <Skeleton className="h-36 w-full" />
        <Skeleton className="h-52 w-full" />
      </div>
    );
  }
  if (ads.isError && !ads.data) {
    return (
      <div className="p-4">
        <ErrorState
          message={operatorProblemMessage(ads.error)}
          onRetry={() => void ads.refetch()}
        />
      </div>
    );
  }
  if (!ad && displayPayload?.state === "empty") {
    return (
      <div className="p-4">
        <EmptyState
          title="Объявление не найдено"
          description="Ссылка устарела или строка отсутствует в актуальном каталоге."
        />
      </div>
    );
  }
  if (!ad) {
    return (
      <div className="p-4">
        <EmptyState
          title="Карточка не подтверждена"
          description="Дождитесь сверки live-снимка. Отсутствие строки пока не подтверждает, что объявления нет."
        />
      </div>
    );
  }
  const scope = displayPayload?.scope;
  const currency = confirmedOperatorCurrency(scope);
  const timestampTimezone =
    scope?.cabinet_timezone_state === "single"
      ? scope.cabinet_timezone
      : scope?.display_timezone;
  const timezoneLabel = scope
    ? timezoneEvidenceLabel(
        scope.cabinet_timezone,
        scope.cabinet_timezone_state,
      )
    : timezoneEvidenceLabel(null, "unknown");
  const timezoneContext =
    scope?.cabinet_timezone_state === "single"
      ? timezoneLabel
      : `${timezoneLabel}${scope?.display_timezone ? ` · отображение ${scope.display_timezone}` : ""}`;

  return (
    <article className="pb-6">
      <header className="border-b border-[var(--color-hairline)] bg-bg-0 px-4 pb-4 pt-3">
        <div className="font-display text-[12px] uppercase tracking-[.08em] text-bg-8">
          Объявление
        </div>
        <h1 className="mt-3 break-words font-display text-[26px] font-medium leading-tight text-bg-11">
          {ad.name}
        </h1>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <MiniSeverityBadge
            severity={severityForDataState(ad.severity, ad.data_state)}
          />
          <DataStateBadge state={ad.data_state} compact />
        </div>
        <p className="mt-3 break-all font-display text-[12px] text-bg-8">
          Meta ID {ad.fb_ad_id}
        </p>
      </header>

      <div
        className="grid gap-4 px-4 pt-4"
        style={{
          // Резерв под sticky-кнопку «Отключить»/«Включить»: без него последние
          // строки «Экономики и воронки» не докручиваются из-под неё.
          paddingBottom:
            "calc(84px + var(--tg-content-safe-bottom, env(safe-area-inset-bottom, 0px)))",
        }}
      >
        {ad.data_state !== "ready" ? (
          <DataStateNotice state={ad.data_state} compact />
        ) : null}

        <section
          className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4"
          aria-labelledby="mini-ad-stop-proximity"
        >
          <h2
            id="mini-ad-stop-proximity"
            className="m-0 font-display text-[18px] text-bg-11"
          >
            До стопа
          </h2>
          <div className="mt-4">
            <StopProximityReadout
              proximity={describeStopProximity(ad.rule_context, { currency })}
            />
          </div>
        </section>

        <section
          className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4"
          aria-labelledby="mini-ad-metrics"
        >
          <h2
            id="mini-ad-metrics"
            className="m-0 font-display text-[18px] text-bg-11"
          >
            Экономика и воронка
          </h2>
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-5">
            <Metric
              label="Расход"
              value={formatSpend(ad.metrics.spend, currency)}
            />
            <Metric
              label="Показы"
              value={formatOperatorCount(ad.metrics.impressions)}
            />
            <Metric
              label="Клики"
              value={formatOperatorCount(ad.metrics.clicks)}
            />
            <Metric
              label="Регистрации"
              value={formatOperatorCount(ad.metrics.registrations)}
            />
            <Metric label="FTD" value={formatOperatorCount(ad.metrics.ftd)} />
            <Metric
              label="Депозиты"
              value={formatOperatorCount(ad.metrics.confirmed_deposits)}
            />
            <Metric label="CPC" value={formatSpend(ad.metrics.cpc, currency)} />
            <Metric
              label="Цена рег."
              value={formatSpend(ad.metrics.cost_per_registration, currency)}
            />
            <Metric
              label="Цена деп."
              value={formatSpend(ad.metrics.cost_per_ftd, currency)}
            />
            <Metric
              label="Частота"
              value={formatDecimalValue(ad.metrics.frequency)}
            />
          </dl>
        </section>

        <section
          className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4"
          aria-labelledby="mini-ad-context"
        >
          <h2
            id="mini-ad-context"
            className="m-0 font-display text-[18px] text-bg-11"
          >
            Контекст
          </h2>
          <dl className="mt-4 grid gap-3">
            <Field
              label="Доставка"
              value={operatorDeliveryLabel(ad.delivery_status)}
            />
            <Field label="Кампания" value={ad.campaign_name} />
            <Field label="Адсет" value={ad.adset_name} />
            <Field
              label="Данные на"
              value={
                timestampTimezone
                  ? formatZonedDateTime(ad.as_of, timestampTimezone)
                  : "—"
              }
            />
            <Field label="Часовой пояс" value={timezoneContext} />
          </dl>
        </section>

        <div className="sticky bottom-[calc(12px+var(--tg-content-safe-bottom,0px))] rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-0/95 p-3 backdrop-blur">
          <MiniAdCommand ad={ad} full />
        </div>
      </div>
    </article>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[12px] text-bg-8">{label}</dt>
      <dd className="mt-1 font-display text-[18px] tabular-nums text-bg-11">
        {value}
      </dd>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-b border-[var(--color-hairline)] pb-3 last:border-0">
      <dt className="text-[12px] text-bg-8">{label}</dt>
      <dd className="mt-1 break-words text-[14px] text-bg-11">{value}</dd>
    </div>
  );
}
