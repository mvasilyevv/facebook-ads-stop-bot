import { useId, type ReactNode } from "react";

import {
  DATA_STATE_DESCRIPTION,
  DATA_STATE_LABEL,
} from "@fb/shared/operator/viewModel";
import { formatZonedDateTime } from "@fb/shared/format/time";
import type {
  DataState,
  OperatorIncidentStatus,
  OperatorIssue,
  OperatorSection,
} from "@fb/shared/operator/contracts";
import { OPERATOR_INCIDENT_STATUS_LABEL } from "@fb/shared/operator/incidentViewModel";
import {
  stopProximityBarWidth,
  type OperatorStopProximity,
} from "@fb/shared/operator/stopProximity";

export interface DataStateBadgeProps {
  state: DataState;
  compact?: boolean;
}

export interface ChoiceTagOption {
  value: string;
  label: string;
}

export interface ChoiceTagListInputProps {
  label: string;
  values: string[];
  options: ChoiceTagOption[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  helpText?: string;
  errorMessage?: string;
  disabled?: boolean;
  selectAllLabel?: string;
}

/** Multi-value field backed only by authoritative choices; no free-form tokens. */
export function ChoiceTagListInput({
  label,
  values,
  options,
  onChange,
  placeholder = "Добавить значение",
  helpText,
  errorMessage,
  disabled = false,
  selectAllLabel,
}: ChoiceTagListInputProps) {
  const id = useId();
  const helpId = helpText ? `${id}-help` : undefined;
  const errorId = errorMessage ? `${id}-error` : undefined;
  const optionByValue = new Map(
    options.map((option) => [option.value, option]),
  );
  const available = options.filter((option) => !values.includes(option.value));

  return (
    <div className="operator-choice-tags">
      <div className="operator-choice-tags__label-row">
        <label htmlFor={id}>{label}</label>
        {selectAllLabel && available.length > 0 ? (
          <button
            type="button"
            disabled={disabled}
            onClick={() => onChange(options.map((option) => option.value))}
          >
            {selectAllLabel}
          </button>
        ) : null}
      </div>
      <div
        className="operator-choice-tags__control"
        data-invalid={errorMessage ? "true" : "false"}
      >
        <div className="operator-choice-tags__values" aria-live="polite">
          {values.map((value, index) => (
            <span className="operator-choice-tags__tag" key={value}>
              <span>
                {optionByValue.get(value)?.label ?? value}
                {index === 0 && values.length > 1 ? " · основной" : ""}
              </span>
              <button
                type="button"
                disabled={disabled}
                aria-label={`Убрать ${optionByValue.get(value)?.label ?? value}`}
                onClick={() =>
                  onChange(values.filter((item) => item !== value))
                }
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <select
          id={id}
          value=""
          disabled={disabled || available.length === 0}
          aria-invalid={Boolean(errorMessage)}
          aria-describedby={errorId ?? helpId}
          onChange={(event) => {
            const value = event.target.value;
            if (value) onChange([...values, value]);
          }}
        >
          <option value="">
            {available.length > 0 ? placeholder : "Все выбраны"}
          </option>
          {available.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      {errorMessage ? (
        <span id={errorId} className="operator-choice-tags__error" role="alert">
          {errorMessage}
        </span>
      ) : helpText ? (
        <span id={helpId} className="operator-choice-tags__help">
          {helpText}
        </span>
      ) : null}
    </div>
  );
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

export function OperatorIncidentStatusBadge({
  status,
}: {
  status: OperatorIncidentStatus;
}) {
  return (
    <span className="operator-incident-status" data-status={status}>
      <span className="operator-incident-status-mark" aria-hidden="true" />
      {OPERATOR_INCIDENT_STATUS_LABEL[status]}
    </span>
  );
}

export interface StopProximityProps {
  proximity: OperatorStopProximity;
}

/**
 * Стадия правила одним тегом.
 *
 * `data-shape` и печатный символ дублируют цвет, поэтому «Подходит к стопу» и
 * «Порог пройден» различимы в ч/б, в forced-colors и при дальтонизме.
 */
export function StopProximityBadge({ proximity }: StopProximityProps) {
  return (
    <span
      className="operator-stop-proximity"
      data-kind={proximity.kind}
      data-stage={proximity.stage ?? "unknown"}
      data-shape={proximity.shape}
      data-tone={proximity.tone}
      title={proximity.hint}
    >
      <span className="operator-stop-proximity-mark" aria-hidden="true">
        {proximity.mark}
      </span>
      <span>{proximity.label}</span>
      <span className="operator-stop-proximity-percent">
        {proximity.percentText}
      </span>
    </span>
  );
}

/**
 * Полное чтение близости к стопу: стадия, правило, «значение из порога» и доля.
 *
 * Шкала рисуется только для подтверждённой доли: пустая полоса на строке без
 * данных читалась бы как «до стопа далеко».
 */
export function StopProximityReadout({ proximity }: StopProximityProps) {
  const width = stopProximityBarWidth(proximity);
  return (
    <div
      className="operator-stop-proximity-readout"
      data-kind={proximity.kind}
      data-stage={proximity.stage ?? "unknown"}
    >
      <StopProximityBadge proximity={proximity} />
      {proximity.ruleText || proximity.detail ? (
        <span className="operator-stop-proximity-rule">
          {[proximity.ruleText, proximity.detail].filter(Boolean).join(" · ")}
        </span>
      ) : null}
      {width !== null ? (
        <span
          className="operator-stop-proximity-bar"
          data-tone={proximity.tone}
          aria-hidden="true"
        >
          <span style={{ width: `${width}%` }} />
        </span>
      ) : null}
      <span className="sr-only">{proximity.hint}</span>
    </div>
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
      {/* Форма метки — второй канал различия, независимый от цвета. */}
      <span className="operator-state-notice-mark" aria-hidden="true">
        {state === "unavailable"
          ? "!"
          : state === "empty"
            ? "○"
            : state === "stale"
              ? "↺"
              : "△"}
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

export type DataStateTone =
  | "confirmed"
  | "degraded"
  | "stale"
  | "unavailable"
  | "neutral";

/**
 * Каждое непроверенное состояние получает собственный тон. Раньше empty, stale
 * и unavailable схлопывались в один серый: подтверждённый пустой результат,
 * устаревший снимок и отсутствие источника выглядели одинаково, а unavailable
 * читался спокойнее, чем partial. Тон дополняется формой метки в styles.css,
 * поэтому цвет не является единственным каналом различия.
 */
function stateTone(state: DataState): DataStateTone {
  if (state === "ready") return "confirmed";
  if (state === "partial") return "degraded";
  if (state === "stale") return "stale";
  if (state === "unavailable") return "unavailable";
  return "neutral";
}

function slug(value: string): string {
  return `operator-${value.toLowerCase().replace(/[^a-zа-яё0-9]+/gi, "-")}`;
}

function formatTimestamp(value: string | null, timezone: string): string {
  const formatted = formatZonedDateTime(value, timezone);
  return formatted === "—" ? "не подтверждено" : formatted;
}
