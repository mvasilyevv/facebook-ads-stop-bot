import { useEffect, useState, startTransition } from "react";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { SectionCard } from "../components/SectionCard";
import { useAutoRefresh } from "../hooks/useAutoRefresh";
import { fetchScanRuns } from "../lib/api";
import {
  formatCompactId,
  formatDateTime,
  formatMetricText,
} from "../lib/format";
import { getBadgeTone } from "../lib/helpers";
import type { ScanRunItem } from "../types";

function formatDurationMs(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return "—";
  }
  return `${value} мс`;
}

function formatPipelineKind(kind: ScanRunItem["pipeline_kind"]): string {
  switch (kind) {
    case "FULL_SCAN":
      return "полный скан";
    case "TARGETED_RECHECK":
      return "быстрая перепроверка";
    default:
      return String(kind).replaceAll("_", " ").toLowerCase();
  }
}

function formatTriggerSource(source: string): string {
  switch (source.toLowerCase()) {
    case "scheduler":
      return "планировщик";
    case "manual":
      return "вручную";
    case "watchlist":
      return "список наблюдения";
    case "action_queue":
      return "очередь действий";
    default:
      return source.replaceAll("_", " ").toLowerCase();
  }
}

function formatScanStatusLabel(status: string): string {
  switch (status.toUpperCase()) {
    case "QUEUED":
      return "в очереди";
    case "RUNNING":
      return "в работе";
    case "SUCCEEDED":
      return "выполнено";
    case "FAILED":
      return "ошибка";
    case "CANCELLED":
      return "отменено";
    case "SKIPPED":
      return "пропущено";
    default:
      return String(status).replaceAll("_", " ").toLowerCase();
  }
}

function renderScopeSummary(summary: ScanRunItem["scope_summary"]) {
  if (!summary) {
    return "—";
  }

  const rawEntries: Array<[string, unknown]> = [
    ["В охвате", summary.rows_in_scope],
    ["Не увидели", summary.rows_not_seen_this_scan],
    ["Активных", summary.active_rows],
    ["На паузе", summary.paused_rows],
  ];
  const summaryEntries = rawEntries.filter(([, value]) => value != null);

  if (summaryEntries.length === 0) {
    return "—";
  }

  return (
    <div className="scan-summary">
      {summaryEntries.map(([label, value]) => (
        <div key={label} className="scan-summary__item">
          <span>{label}</span>
          <strong>{String(value)}</strong>
        </div>
      ))}
    </div>
  );
}

export default function ScansPage() {
  const [scans, setScans] = useState<ScanRunItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function reload(silent = false) {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const data = await fetchScanRuns();
      startTransition(() => {
        setScans(data);
        setLoading(false);
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  useAutoRefresh(reload, { enabled: !loading });

  const visibleScans = scans.filter((scan) => {
    const text = `${scan.id} ${scan.browser_host_id} ${scan.profile_id} ${scan.status} ${scan.pipeline_kind} ${scan.trigger_source}`.toLowerCase();
    return text.includes(search.toLowerCase());
  });
  const fullScanCount = scans.filter((scan) => scan.pipeline_kind === "FULL_SCAN").length;
  const recheckCount = scans.filter((scan) => scan.pipeline_kind === "TARGETED_RECHECK").length;
  const actionJobsEnqueued = scans.reduce((sum, scan) => sum + scan.action_jobs_enqueued, 0);

  if (loading) {
    return <div className="page-loading">Загрузка сканов...</div>;
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Сканирование объявлений</h1>
          <p className="page-subtitle">История полного скана и быстрой перепроверки с timing-метриками</p>
        </div>
        <div className="page-header__actions">
          <button type="button" className="button button--primary" onClick={() => void reload(true)}>
            Обновить
          </button>
        </div>
      </div>

      {error && <div className="inline-error">{error}</div>}

      <div className="metric-grid dashboard-summary-grid">
        <article className="metric-tile metric-tile--accent">
          <span>Полный скан</span>
          <strong>{fullScanCount}</strong>
          <div className="mini-row">
            <span>Быстрая перепроверка</span>
            <span>{recheckCount}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Очередь действий</span>
          <strong>{actionJobsEnqueued}</strong>
          <div className="mini-row">
            <span>Последний запуск</span>
            <span>{visibleScans[0] ? formatScanStatusLabel(visibleScans[0].status) : "нет данных"}</span>
          </div>
        </article>
      </div>

      <SectionCard
        title="Запуски сканирования"
        subtitle="История выполненных сканов"
        actions={
          <input
            className="input input--compact"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Поиск по ID, хосту, профилю или статусу"
          />
        }
      >
        {visibleScans.length === 0 ? (
          <EmptyState title="Сканов не загружено" description="История сканирования появится после выполнения первого скана." />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Хост</th>
                  <th>Профиль</th>
                  <th>Статус</th>
                  <th>Тип</th>
                  <th>Источник</th>
                  <th>Просмотрено</th>
                  <th>Разобрано</th>
                  <th>Сбор</th>
                  <th>Оценка</th>
                  <th>Сохранение</th>
                  <th>Очередь</th>
                  <th>Задачи</th>
                  <th>Сводка по объёму</th>
                  <th>Ошибка</th>
                  <th>Начало</th>
                  <th>Завершение</th>
                </tr>
              </thead>
              <tbody>
                {visibleScans.map((scan) => (
                  <tr key={scan.id}>
                    <td>
                      <span className="mono scan-identifier" title={scan.id}>
                        {formatCompactId(scan.id)}
                      </span>
                    </td>
                    <td>
                      <span className="mono scan-identifier scan-identifier--host" title={scan.browser_host_id}>
                        {scan.browser_host_id}
                      </span>
                    </td>
                    <td>
                      <span className="mono scan-identifier" title={scan.profile_id}>
                        {formatCompactId(scan.profile_id)}
                      </span>
                    </td>
                    <td>
                      <Badge tone={getBadgeTone(scan.status)}>{formatScanStatusLabel(scan.status)}</Badge>
                    </td>
                    <td>{formatPipelineKind(scan.pipeline_kind)}</td>
                    <td>{formatTriggerSource(scan.trigger_source)}</td>
                    <td>{formatMetricText(scan.rows_seen)}</td>
                    <td>{formatMetricText(scan.rows_parsed)}</td>
                    <td title={`${scan.collect_ms} мс`}>{formatDurationMs(scan.collect_ms)}</td>
                    <td title={`${scan.evaluate_ms} мс`}>{formatDurationMs(scan.evaluate_ms)}</td>
                    <td title={`${scan.persist_ms} мс`}>{formatDurationMs(scan.persist_ms)}</td>
                    <td title={`${scan.queue_ms} мс`}>{formatDurationMs(scan.queue_ms)}</td>
                    <td>{formatMetricText(scan.action_jobs_enqueued)}</td>
                    <td>{renderScopeSummary(scan.scope_summary)}</td>
                    <td>
                      {scan.error_message ? (
                        <span className="scan-error-text" title={scan.error_message}>
                          {scan.error_message}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{formatDateTime(scan.started_at)}</td>
                    <td>{scan.finished_at ? formatDateTime(scan.finished_at) : "в процессе"}</td>
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
