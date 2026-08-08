import type { ReactNode } from "react";

import {
  DATA_STATE_DESCRIPTION,
  DATA_STATE_LABEL,
} from "@fb/shared/operator/viewModel";
import { formatZonedDateTime } from "@fb/shared/format/time";
import type {
  DataState,
  OperatorIssue,
  OperatorSection,
} from "@fb/shared/operator/contracts";

export interface DataStateBadgeProps {
  state: DataState;
  compact?: boolean;
}

export function DataStateBadge({
  state,
  compact = false,
}: DataStateBadgeProps) {
  return (
    <span
      className="operator-state-badge"
      data-state={state}
      data-tone={stateTone(state)}
    >
      <span className="operator-state-dot" aria-hidden="true" />
      {compact ? shortStateLabel(state) : DATA_STATE_LABEL[state]}
    </span>
  );
}

export interface DataStateNoticeProps {
  state: Exclude<DataState, "ready">;
  issues?: OperatorIssue[];
  compact?: boolean;
}

export function DataStateNotice({
  state,
  issues = [],
  compact = false,
}: DataStateNoticeProps) {
  const firstIssue = issues[0];
  return (
    <div
      className="operator-state-notice"
      data-state={state}
      data-tone={stateTone(state)}
      role={state === "unavailable" ? "alert" : "status"}
    >
      <span className="operator-state-notice-mark" aria-hidden="true">
        {state === "unavailable" ? "!" : state === "empty" ? "○" : "△"}
      </span>
      <div>
        <strong>{firstIssue?.title ?? DATA_STATE_LABEL[state]}</strong>
        {!compact ? (
          <p>{firstIssue?.detail ?? DATA_STATE_DESCRIPTION[state]}</p>
        ) : null}
        {!compact && issues.length > 1 ? (
          <ul className="operator-state-notice-issues">
            {issues.slice(1, 4).map((issue) => (
              <li key={`${issue.code}:${issue.correlation_id}`}>
                <strong>{issue.title}</strong>
                {issue.detail ? <span>{issue.detail}</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}

export interface OperatorSectionFrameProps<T> {
  section: OperatorSection<T>;
  title: string;
  description?: string;
  action?: ReactNode;
  children: (data: T) => ReactNode;
  empty?: ReactNode;
  className?: string;
}

export function OperatorSectionFrame<T>({
  section,
  title,
  description,
  action,
  children,
  empty,
  className,
}: OperatorSectionFrameProps<T>) {
  const content = section.data;
  const canRender =
    content !== null &&
    section.state !== "unavailable" &&
    section.state !== "empty";
  return (
    <section
      className={`operator-section ${className ?? ""}`.trim()}
      aria-labelledby={slug(title)}
    >
      <header className="operator-section-header">
        <div>
          <div className="operator-section-kicker">Операторский снимок</div>
          <h2 id={slug(title)}>{title}</h2>
          {description ? <p>{description}</p> : null}
        </div>
        <div className="operator-section-meta">
          <DataStateBadge state={section.state} />
          {action}
        </div>
      </header>
      {section.state !== "ready" ? (
        <DataStateNotice state={section.state} issues={section.issues} />
      ) : null}
      {section.state === "empty"
        ? empty
        : canRender && content !== null
          ? children(content)
          : null}
    </section>
  );
}

export interface AccessibleChartFrameProps {
  title: string;
  summary: string;
  timezone: string;
  asOf: string | null;
  sources: string[];
  completeness: DataState;
  chart: ReactNode;
  table: ReactNode;
}

export function AccessibleChartFrame({
  title,
  summary,
  timezone,
  asOf,
  sources,
  completeness,
  chart,
  table,
}: AccessibleChartFrameProps) {
  const descriptionId = `${slug(title)}-description`;
  return (
    <figure className="operator-chart" aria-describedby={descriptionId}>
      <figcaption>
        <div>
          <h3>{title}</h3>
          <p id={descriptionId}>{summary}</p>
        </div>
        <DataStateBadge state={completeness} compact />
      </figcaption>
      <div className="operator-chart-meta">
        <span>Часовой пояс: {timezone}</span>
        <span>На: {formatTimestamp(asOf, timezone)}</span>
        <span>
          Источник: {sources.length ? sources.join(", ") : "не подтверждён"}
        </span>
      </div>
      <div
        className="operator-chart-visual"
        role="group"
        aria-label={`Интерактивный график «${title}»`}
      >
        {chart}
      </div>
      <details className="operator-chart-table">
        <summary>Данные графика</summary>
        <div className="operator-chart-table-scroll">{table}</div>
      </details>
    </figure>
  );
}

function shortStateLabel(state: DataState): string {
  if (state === "ready") return "Актуально";
  if (state === "empty") return "Пусто";
  if (state === "partial") return "Частично";
  if (state === "stale") return "Устарело";
  return "Недоступно";
}

function stateTone(state: DataState): "confirmed" | "degraded" | "neutral" {
  if (state === "ready") return "confirmed";
  if (state === "partial") return "degraded";
  return "neutral";
}

function slug(value: string): string {
  return `operator-${value.toLowerCase().replace(/[^a-zа-яё0-9]+/gi, "-")}`;
}

function formatTimestamp(value: string | null, timezone: string): string {
  const formatted = formatZonedDateTime(value, timezone);
  return formatted === "—" ? "не подтверждено" : formatted;
}
