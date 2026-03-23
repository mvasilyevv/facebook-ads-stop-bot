import { useEffect, useMemo, useState, startTransition } from "react";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { SectionCard } from "../components/SectionCard";
import { useAutoRefresh } from "../hooks/useAutoRefresh";
import { fetchActionJobs, fetchDecisions } from "../lib/api";
import {
  formatCompactId,
  formatDateTime,
  formatDecisionExecutionState,
  formatDecisionHuman,
  formatMoney,
  formatRelativeStatus,
  resolveDecisionExecutionState,
} from "../lib/format";
import { getBadgeTone } from "../lib/helpers";
import type { ActionJobItem, DecisionItem } from "../types";

function getActionTone(status: ActionJobItem["status"]): "neutral" | "good" | "warn" | "bad" | "info" {
  switch (status) {
    case "SUCCEEDED":
      return "good";
    case "FAILED":
      return "bad";
    case "RUNNING":
      return "info";
    case "QUEUED":
    case "RETRYING":
      return "warn";
    default:
      return "neutral";
  }
}

function formatActionType(actionType: ActionJobItem["action_type"]): string {
  switch (actionType) {
    case "PAUSE":
      return "пауза";
    case "RESUME":
      return "возобновление";
    default:
      return String(actionType).toLowerCase();
  }
}

function formatActionStatus(status: ActionJobItem["status"]): string {
  switch (status) {
    case "QUEUED":
      return "в очереди";
    case "RUNNING":
      return "в работе";
    case "RETRYING":
      return "повтор";
    case "SUCCEEDED":
      return "выполнено";
    case "FAILED":
      return "ошибка";
    case "CANCELLED":
      return "отменено";
    default:
      return String(status).toLowerCase();
  }
}

export default function DecisionsPage() {
  const [decisions, setDecisions] = useState<DecisionItem[]>([]);
  const [actionJobs, setActionJobs] = useState<ActionJobItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function reload(silent = false) {
    if (!silent) {
      setLoading(true);
    }
    setError(null);
    const [decisionsResult, actionJobsResult] = await Promise.allSettled([
      fetchDecisions(),
      fetchActionJobs(),
    ]);

    if (decisionsResult.status === "fulfilled") {
      startTransition(() => setDecisions(decisionsResult.value));
    } else {
      setError(decisionsResult.reason instanceof Error ? decisionsResult.reason.message : "Не удалось загрузить решения");
    }

    if (actionJobsResult.status === "fulfilled") {
      startTransition(() => setActionJobs(actionJobsResult.value));
    } else {
      setError((current) =>
        current
          ? `${current} · ${actionJobsResult.reason instanceof Error ? actionJobsResult.reason.message : "Не удалось загрузить очередь действий"}`
          : actionJobsResult.reason instanceof Error
            ? actionJobsResult.reason.message
            : "Не удалось загрузить очередь действий",
      );
    }

    startTransition(() => setLoading(false));
  }

  useEffect(() => {
    void reload();
  }, []);

  useAutoRefresh(reload, { enabled: !loading });

  const visibleDecisions = useMemo(() => {
    const normalized = search.toLowerCase();
    return decisions.filter((decision) =>
      `${decision.fb_ad_id} ${decision.decision} ${decision.reason} ${decision.scan_run_id}`.toLowerCase().includes(normalized),
    );
  }, [decisions, search]);

  const visibleJobs = useMemo(() => {
    const normalized = search.toLowerCase();
    return actionJobs.filter((job) =>
      `${job.fb_ad_id} ${job.action_type} ${job.status} ${job.last_error ?? ""}`.toLowerCase().includes(normalized),
    );
  }, [actionJobs, search]);

  if (loading) {
    return <div className="page-loading">Загрузка решений...</div>;
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Решения и очередь</h1>
          <p className="page-subtitle">Журнал решений сканера и фактическая очередь действий для паузы и возобновления</p>
        </div>
        <div className="page-header__actions">
          <input
            className="input input--compact"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Поиск по ID, причине или статусу"
          />
          <button type="button" className="button button--primary" onClick={() => void reload(true)}>
            Обновить
          </button>
        </div>
      </div>

      {error ? <div className="inline-error">{error}</div> : null}

      <SectionCard title="Журнал решений" subtitle="Последние решения и статус выполнения действий">
        {visibleDecisions.length === 0 ? (
          <EmptyState
            title="Решений нет"
            description="После первого скана здесь появится список решений по объявлениям."
          />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Объявление</th>
                  <th>Решение</th>
                  <th>Выполнение</th>
                  <th>CPA</th>
                  <th>Время</th>
                </tr>
              </thead>
              <tbody>
                {visibleDecisions.map((decision) => {
                  const executionState = resolveDecisionExecutionState(decision);
                  return (
                    <tr key={decision.id}>
                      <td>
                        <div className="mono" title={decision.fb_ad_id}>
                          {formatCompactId(decision.fb_ad_id)}
                        </div>
                        <div className="section-note">{decision.scan_run_id}</div>
                      </td>
                      <td>
                        <Badge tone={getBadgeTone(decision.decision)}>{formatRelativeStatus(decision.decision)}</Badge>
                        <div className="section-note" title={decision.reason}>
                          {formatDecisionHuman(decision.decision, decision.reason)}
                        </div>
                      </td>
                      <td>
                        <Badge tone={getBadgeTone(executionState)}>
                          {formatDecisionExecutionState(executionState)}
                        </Badge>
                        <div className="section-note">{decision.action_status || "нет статуса"}</div>
                      </td>
                      <td>{formatMoney(decision.resolved_cpa_usd)}</td>
                      <td>{formatDateTime(decision.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <SectionCard title="Очередь действий" subtitle="Фактические задания на паузу и возобновление">
        {visibleJobs.length === 0 ? (
          <EmptyState
            title="Очередь действий пуста"
            description="Когда задания попадут в обработку, здесь появятся записи и их статусы."
          />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Объявление</th>
                  <th>Тип</th>
                  <th>Статус</th>
                  <th>Приоритет</th>
                  <th>Попытки</th>
                  <th>Следующая попытка</th>
                  <th>Ошибка</th>
                </tr>
              </thead>
              <tbody>
                {visibleJobs.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <div className="mono" title={job.fb_ad_id}>
                        {formatCompactId(job.fb_ad_id)}
                      </div>
                      <div className="section-note">{job.ad_name || job.campaign_name || "—"}</div>
                    </td>
                    <td>{formatActionType(job.action_type)}</td>
                    <td>
                      <Badge tone={getActionTone(job.status)}>{formatActionStatus(job.status)}</Badge>
                    </td>
                    <td>{job.priority_score}</td>
                    <td>{job.attempt_count}</td>
                    <td>{formatDateTime(job.next_attempt_at)}</td>
                    <td>{job.last_error || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </>
  );
}
