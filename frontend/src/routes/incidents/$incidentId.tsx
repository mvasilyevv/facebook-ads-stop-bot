import { useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, ArrowRight, ShieldCheck } from "lucide-react";

import { formatZonedDateTime } from "@fb/shared/format/time";
import {
  OPERATOR_INCIDENT_STATUS_LABEL,
  operatorIncidentCopy,
  operatorIncidentDataState,
  operatorIncidentTargetLabel,
} from "@fb/shared/operator/incidentViewModel";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { operatorSourceLabel } from "@fb/shared/operator/ledgerSemantics";
import { safeOperatorAttentionHref } from "@fb/shared/operator/attentionNavigation";
import { DataStateBadge, DataStateNotice, incidentSeverityTone } from "@fb/operator-ui";

import { Button } from "@/components/ui/Button";
import {
  OperatorCardSkeleton,
  OperatorPageBoundary,
  OperatorUnavailableState,
} from "@/components/layout/OperatorPageBoundary";
import {
  operatorIncidentProblemMessage,
  useAcknowledgeOperatorIncident,
  useOperatorIncident,
} from "@/lib/api/operator";
import { OperatorSeverityBadge } from "@/features/operator/OperatorAds";

export const Route = createFileRoute("/incidents/$incidentId")({ component: IncidentDetailPage });

function IncidentDetailPage() {
  const { incidentId } = Route.useParams();
  const incidentQuery = useOperatorIncident(incidentId);
  const acknowledge = useAcknowledgeOperatorIncident();
  const realtimeStatus = useOperatorRealtimeStatus();
  const [actionError, setActionError] = useState<string | null>(null);
  const detail = incidentQuery.data;
  const incident = detail?.incident;

  if (incidentQuery.isError) {
    return (
      <OperatorPageBoundary
        title="Инцидент"
        navigation={<IncidentBreadcrumb />}
      >
        <OperatorUnavailableState
          title="Инцидент недоступен"
          resource="инцидент"
          details={operatorIncidentProblemMessage(incidentQuery.error)}
          onRetry={() => void incidentQuery.refetch()}
        />
      </OperatorPageBoundary>
    );
  }
  if (incidentQuery.isPending)
    return (
      <OperatorPageBoundary
        title="Инцидент"
        navigation={<IncidentBreadcrumb />}
      >
        <OperatorCardSkeleton label="Загрузка инцидента" />
      </OperatorPageBoundary>
    );
  if (!detail || !incident)
    return (
      <OperatorPageBoundary
        title="Инцидент"
        navigation={<IncidentBreadcrumb />}
      >
        <OperatorUnavailableState
          title="Инцидент не найден"
          resource="инцидент"
          details="Он мог быть уже разрешён; откройте актуальную ленту внимания."
        />
      </OperatorPageBoundary>
    );

  const acknowledgeIncident = async () => {
    setActionError(null);
    try {
      await acknowledge.mutateAsync({
        params: {
          path: { incident_id: incidentId },
          header: { "X-Operator-Principal": "operator:web" },
        },
      });
      await incidentQuery.refetch();
    } catch (error) {
      setActionError(operatorIncidentProblemMessage(error));
    }
  };
  const displayState = operatorIncidentDataState(
    detail.state,
    realtimeStatus === "connected" && !incidentQuery.isError,
  );
  const severityTone = incidentSeverityTone(incident.severity, displayState);
  const copy = operatorIncidentCopy(incident, detail.scope);
  const actionHref = safeOperatorAttentionHref(incident.action.href);

  return (
    <article className="mx-auto max-w-3xl">
      <Link
        to="/incidents"
        className="mb-5 inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] px-2 text-[14px] font-semibold text-bg-9 hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-accent"
      >
        <ArrowLeft size={16} aria-hidden="true" /> Все инциденты
      </Link>
      <header
        data-severity={incident.severity}
        className={`rounded-[var(--radius-3)] border p-5 sm:p-6 ${severityTone.surface}`}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <OperatorSeverityBadge severity={incident.severity} />
            <span className="text-[14px] text-bg-9">{operatorIncidentTargetLabel(incident)}</span>
          </div>
          <DataStateBadge state={displayState} compact />
        </div>
        <h1 className="m-0 mt-4 font-display text-[clamp(26px,4vw,38px)] font-medium leading-tight text-bg-11">
          {copy.title}
        </h1>
        {copy.summary ? (
          <p className="mt-3 text-[16px] leading-6 text-bg-10">{copy.summary}</p>
        ) : null}
      </header>
      {displayState !== "ready" ? (
        <div className="mt-5">
          <DataStateNotice state={displayState} issues={detail.issues} />
        </div>
      ) : null}
      <section className="mt-5 rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5 sm:p-6">
        <h2 className="m-0 font-display text-[20px] text-bg-11">Контекст</h2>
        <dl className="mt-4 grid gap-px overflow-hidden rounded-[var(--radius-2)] bg-[var(--color-hairline)] sm:grid-cols-2">
          <Item label="Объект" value={operatorIncidentTargetLabel(incident)} />
          <Item label="Открыт" value={formatZonedDateTime(incident.occurred_at, detail.timezone)} />
          <Item label="Причина" value={copy.reason ?? "не подтверждена"} />
          <Item label="Статус" value={OPERATOR_INCIDENT_STATUS_LABEL[incident.status]} />
          <Item label="Данные на" value={formatZonedDateTime(detail.as_of, detail.timezone)} />
          <Item
            label="Источник"
            value={
              detail.sources.length
                ? detail.sources.map(operatorSourceLabel).join(", ")
                : "не подтверждён"
            }
          />
        </dl>
        {!detail.timezone_known ? (
          <p
            role="status"
            className="mt-5 rounded-[var(--radius-2)] border border-warning/35 bg-warning-bg p-3 text-[14px] leading-5 text-bg-11"
          >
            Часовой пояс кабинета не подтверждён; время показано как оценочное.
          </p>
        ) : null}
        {actionHref ? (
          <a
            href={actionHref}
            className="mt-5 inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] px-4 text-[14px] font-semibold text-bg-11 hover:bg-bg-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            {incident.action.label}
            <ArrowRight size={16} aria-hidden="true" />
          </a>
        ) : null}
        {actionError ? (
          <p
            role="alert"
            className="mt-5 rounded-[var(--radius-2)] border border-danger/35 bg-danger-bg p-3 text-[14px] leading-5 text-danger"
          >
            {actionError}
          </p>
        ) : null}
        {incident.status === "open" ? (
          <Button
            variant="secondary"
            size="lg"
            className="mt-5 min-h-11 w-full sm:w-auto"
            leftIcon={<ShieldCheck size={17} aria-hidden="true" />}
            loading={acknowledge.isPending}
            onClick={() => void acknowledgeIncident()}
          >
            Подтвердить получение
          </Button>
        ) : null}
      </section>
    </article>
  );
}

function IncidentBreadcrumb() {
  return (
    <Link
      to="/incidents"
      className="inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] px-2 text-[14px] font-semibold text-bg-9 hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-accent"
    >
      <ArrowLeft size={16} aria-hidden="true" /> Все инциденты
    </Link>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-bg-2 p-4">
      <dt className="text-[12px] font-semibold uppercase tracking-[.05em] text-bg-8">{label}</dt>
      <dd className="m-0 mt-2 break-words text-[14px] text-bg-11">{value}</dd>
    </div>
  );
}

