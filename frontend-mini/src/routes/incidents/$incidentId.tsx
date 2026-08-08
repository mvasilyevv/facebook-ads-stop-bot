import { useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";

import { formatZonedDateTime } from "@fb/shared/format/time";
import type { OperatorSeverity } from "@fb/shared/operator/contracts";
import { DataStateBadge, DataStateNotice } from "@fb/operator-ui";

import { Button } from "@/components/ui/Button";
import {
  operatorProblemMessage,
  useAcknowledgeOperatorIncident,
  useOperatorIncident,
} from "@/lib/operatorApi";
import { MiniSeverityBadge } from "@/features/operator/OperatorAds";

export const Route = createFileRoute("/incidents/$incidentId")({
  component: MiniIncidentDetailRoute,
});

function MiniIncidentDetailRoute() {
  const { incidentId } = Route.useParams();
  return <MiniIncidentDetail incidentId={incidentId} />;
}

export function MiniIncidentDetail({ incidentId }: { incidentId: string }) {
  const navigate = useNavigate();
  const incidentQuery = useOperatorIncident(incidentId);
  const acknowledge = useAcknowledgeOperatorIncident();
  const [actionError, setActionError] = useState<string | null>(null);
  const detail = incidentQuery.data;
  const incident = detail?.incident;

  if (incidentQuery.isPending)
    return (
      <div role="status" className="p-4 text-[14px] text-bg-9">
        Загрузка инцидента…
      </div>
    );
  if (incidentQuery.isError)
    return (
      <div
        role="alert"
        className="m-4 rounded-[var(--radius-2)] bg-danger-bg p-4 text-[14px] text-danger"
      >
        {operatorProblemMessage(incidentQuery.error)}
      </div>
    );
  if (!detail || !incident)
    return (
      <div
        role="alert"
        className="m-4 rounded-[var(--radius-2)] bg-danger-bg p-4 text-[14px] text-danger"
      >
        Инцидент не найден или уже разрешён.
      </div>
    );

  const acknowledgeIncident = async () => {
    setActionError(null);
    try {
      await acknowledge.mutateAsync({
        params: {
          path: { incident_id: incidentId },
          header: { "X-Operator-Principal": "operator:tma" },
        },
      });
      await incidentQuery.refetch();
      await navigate({ to: "/" });
    } catch (error) {
      setActionError(operatorProblemMessage(error));
    }
  };
  const severityTone = incidentSeverityTone(incident.severity);

  return (
    <article className="px-4 pb-6 pt-4">
      <header
        data-severity={incident.severity}
        className={`rounded-[var(--radius-3)] border p-4 ${severityTone.surface}`}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <MiniSeverityBadge severity={incident.severity} />
          <DataStateBadge state={detail.state} compact />
        </div>
        <h1 className="m-0 mt-3 font-display text-[26px] leading-8 text-bg-11">
          {incident.title}
        </h1>
        <p className="mt-2 text-[14px] leading-5 text-bg-10">
          {incident.summary}
        </p>
      </header>
      {detail.state !== "ready" ? (
        <div className="mt-4">
          <DataStateNotice state={detail.state} issues={detail.issues} />
        </div>
      ) : null}
      <dl className="mt-4 divide-y divide-[var(--color-hairline)] rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1">
        <Row
          label="Объект"
          value={
            incident.target.label ?? incident.target.id ?? incident.target.kind
          }
        />
        <Row
          label="Открыт"
          value={formatZonedDateTime(
            incident.occurred_at,
            detail.timezone,
          )}
        />
        <Row label="Причина" value={incident.reason ?? "не указана"} />
        <Row label="Статус" value={incidentStatusLabel(detail.status)} />
        <Row
          label="Данные на"
          value={formatZonedDateTime(detail.as_of, detail.timezone)}
        />
        <Row
          label="Источник"
          value={detail.sources.length ? detail.sources.join(", ") : "не подтверждён"}
        />
      </dl>
      {!detail.timezone_known ? (
        <p
          role="status"
          className="mt-4 rounded-[var(--radius-2)] border border-warning/35 bg-warning-bg p-3 text-[14px] leading-5 text-bg-11"
        >
          Часовой пояс кабинета не подтверждён; время показано как оценочное.
        </p>
      ) : null}
      {actionError ? (
        <p
          role="alert"
          className="mt-4 rounded-[var(--radius-2)] border border-danger/35 bg-danger-bg p-3 text-[14px] leading-5 text-danger"
        >
          {actionError}
        </p>
      ) : null}
      {detail.status === "open" ? (
        <Button
          fullWidth
          className="mt-4 min-h-11"
          loading={acknowledge.isPending}
          onClick={() => void acknowledgeIncident()}
        >
          Подтвердить получение
        </Button>
      ) : null}
    </article>
  );
}

function incidentStatusLabel(status: string): string {
  return {
    open: "Открыт",
    acknowledged: "Получение подтверждено",
    executing: "Действие выполняется",
    resolved: "Разрешён",
    failed: "Завершён с ошибкой",
  }[status] ?? "Не подтверждено";
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-4">
      <dt className="text-[12px] font-semibold uppercase tracking-[.05em] text-bg-8">
        {label}
      </dt>
      <dd className="m-0 mt-2 break-words text-[14px] text-bg-11">{value}</dd>
    </div>
  );
}

function incidentSeverityTone(severity: OperatorSeverity): { surface: string } {
  if (severity === "critical") {
    return { surface: "border-danger/35 bg-danger-bg" };
  }
  if (severity === "warning") {
    return { surface: "border-warning/35 bg-warning-bg" };
  }
  if (severity === "ok") {
    return { surface: "border-success/35 bg-success-bg" };
  }
  return { surface: "border-[var(--color-hairline-strong)] bg-bg-2" };
}
