import type {
  DataState,
  OperatorActionItem,
  OperatorActionState,
  OperatorActionsResponse,
  OperatorAdRow,
  OperatorAdsResponse,
  OperatorIssue,
  OperatorSection,
  OperatorSeverity,
  OperatorSnapshot,
} from "./contracts";

export const DATA_STATE_LABEL: Record<DataState, string> = {
  ready: "Данные актуальны",
  empty: "Нет данных",
  partial: "Данные неполные",
  stale: "Данные устарели",
  unavailable: "Источник недоступен",
};

export const DATA_STATE_DESCRIPTION: Record<DataState, string> = {
  ready: "Источник подтвердил актуальный снимок.",
  empty: "Источник ответил, но в выбранном периоде данных нет.",
  partial: "Часть источников недоступна. Значения ниже нельзя считать полными.",
  stale:
    "Показан последний подтверждённый снимок. Он больше не считается актуальным.",
  unavailable: "Подтверждённых значений нет. Нули не подставляются.",
};

export const SEVERITY_LABEL: Record<OperatorSeverity, string> = {
  ok: "В норме",
  warning: "Требует внимания",
  critical: "Критично",
  unknown: "Не подтверждено",
};

export const ACTION_STATE_LABEL: Record<OperatorActionState, string> = {
  queued: "В очереди",
  running: "Выполняется",
  confirmed: "Подтверждено",
  failed: "Ошибка",
  cancelled: "Отменено",
  unknown: "Результат уточняется",
};

const WORKER_STATUS_LABEL: Record<string, string> = {
  online: "В работе",
  offline: "Недоступен",
  degraded: "С ограничениями",
  unknown: "Не подтверждено",
};

export function workerStatusLabel(status: string): string {
  return WORKER_STATUS_LABEL[status.trim().toLowerCase()] ?? status;
}

export function sectionHasData<T>(
  section: OperatorSection<T>,
): section is OperatorSection<T> & {
  data: T;
} {
  return section.data !== null;
}

export function sectionCanShowData<T>(section: OperatorSection<T>): boolean {
  return section.state !== "unavailable" && sectionHasData(section);
}

const REALTIME_RECONCILING_ISSUE: OperatorIssue = {
  code: "REALTIME_RECONCILING",
  title: "Live-связь восстанавливается",
  detail: "Последний снимок больше не считается актуальным.",
  severity: "unknown",
  correlation_id: null,
};

/**
 * Cached catalog rows are useful context during reconnect, but they are never
 * a confirmed empty result or a safe basis for a money command.
 */
export function adsForRealtimeState(
  response: OperatorAdsResponse,
  realtimeConnected: boolean,
): OperatorAdsResponse {
  const state: DataState = realtimeConnected
    ? response.state
    : response.state === "unavailable"
      ? "unavailable"
      : "stale";
  const rows = response.rows.map((row) =>
    adRowForDataState(row, rowStateForCollection(row.data_state, state)),
  );
  const unchangedRows = rows.every(
    (row, index) => row === response.rows[index],
  );
  if (realtimeConnected && state === response.state && unchangedRows) {
    return response;
  }
  return {
    ...response,
    state,
    issues: realtimeConnected
      ? response.issues
      : [
          REALTIME_RECONCILING_ISSUE,
          ...response.issues.filter(
            (issue) => issue.code !== REALTIME_RECONCILING_ISSUE.code,
          ),
        ],
    rows,
  };
}

const UNKNOWN_AD_METRICS: OperatorAdRow["metrics"] = {
  spend: null,
  impressions: null,
  clicks: null,
  registrations: null,
  ftd: null,
  confirmed_deposits: null,
  cpc: null,
  cost_per_registration: null,
};

/**
 * `unavailable` means that the row has no current evidence. Cached numbers and
 * delivery state must not survive that transition because a zero would then
 * look confirmed and a stale delivery status could influence a money action.
 */
function adRowForDataState(
  row: OperatorAdRow,
  state: DataState,
): OperatorAdRow {
  if (state !== "unavailable") {
    return row.data_state === state ? row : { ...row, data_state: state };
  }
  const metricsAlreadyUnknown = Object.values(row.metrics).every(
    (value) => value === null,
  );
  if (
    row.data_state === "unavailable" &&
    row.delivery_status === null &&
    metricsAlreadyUnknown
  ) {
    return row;
  }
  return {
    ...row,
    data_state: "unavailable",
    delivery_status: null,
    metrics: { ...UNKNOWN_AD_METRICS },
  };
}

/**
 * An actions page may retain useful lifecycle history during reconciliation,
 * but a cached `confirmed` result is not proof of the target's current state.
 * Keep failures and in-flight states visible while projecting confirmations to
 * `unknown` until the collection is current again.
 */
export function actionsForRealtimeState(
  response: OperatorActionsResponse,
  realtimeConnected: boolean,
): OperatorActionsResponse {
  const state = realtimeDataState(response.state, realtimeConnected);
  const items = response.items.map((item) =>
    actionItemForDataState(item, state),
  );
  const unchangedItems = items.every(
    (item, index) => item === response.items[index],
  );

  if (realtimeConnected && state === response.state && unchangedItems) {
    return response;
  }

  return {
    ...response,
    state,
    issues: issuesForRealtimeState(response.issues, realtimeConnected),
    items,
  };
}

/**
 * Preserve the generated response metadata when selecting a single action.
 * This prevents a detail route from losing the server's stale/partial state.
 */
export function actionProjectionFromResponse(
  response: OperatorActionsResponse,
  actionId: string,
): OperatorSection<OperatorActionItem> {
  const item =
    response.items.find((candidate) => candidate.id === actionId) ?? null;
  return {
    state: response.state,
    as_of: response.as_of,
    freshness_seconds: response.freshness_seconds,
    sources: response.sources,
    issues: response.issues,
    data: item ? actionItemForDataState(item, response.state) : null,
  };
}

/** Apply the same fail-closed realtime semantics to an action detail. */
export function actionForRealtimeState(
  projection: OperatorSection<OperatorActionItem>,
  realtimeConnected: boolean,
): OperatorSection<OperatorActionItem> {
  const state = realtimeDataState(projection.state, realtimeConnected);
  const data = projection.data
    ? actionItemForDataState(projection.data, state)
    : null;

  if (
    realtimeConnected &&
    state === projection.state &&
    data === projection.data
  ) {
    return projection;
  }

  return {
    ...projection,
    state,
    issues: issuesForRealtimeState(projection.issues, realtimeConnected),
    data,
  };
}

function realtimeDataState(
  state: DataState,
  realtimeConnected: boolean,
): DataState {
  if (realtimeConnected) return state;
  return state === "unavailable" ? "unavailable" : "stale";
}

function issuesForRealtimeState(
  issues: OperatorIssue[],
  realtimeConnected: boolean,
): OperatorIssue[] {
  if (realtimeConnected) return issues;
  return [
    REALTIME_RECONCILING_ISSUE,
    ...issues.filter((issue) => issue.code !== REALTIME_RECONCILING_ISSUE.code),
  ];
}

function actionItemForDataState(
  item: OperatorActionItem,
  state: DataState,
): OperatorActionItem {
  if (state === "ready" || item.state !== "confirmed") return item;
  return { ...item, state: "unknown" };
}

function rowStateForCollection(
  rowState: DataState,
  collectionState: DataState,
): DataState {
  if (collectionState === "ready" || collectionState === "empty") {
    return rowState;
  }
  if (collectionState === "unavailable") {
    return "unavailable";
  }
  if (collectionState === "stale") {
    return rowState === "unavailable" ? "unavailable" : "stale";
  }
  if (rowState === "unavailable" || rowState === "stale") {
    return rowState;
  }
  return "partial";
}

/**
 * A cached HTTP snapshot is not current while the realtime stream is waiting
 * for reconciliation. Preserve the last values for context, but downgrade
 * every previously usable section so no confirmed/green state leaks into UI.
 */
export function snapshotForRealtimeState(
  snapshot: OperatorSnapshot,
  realtimeConnected: boolean,
): OperatorSnapshot {
  if (realtimeConnected) {
    const actions = actionSectionForDataState(snapshot.actions);
    return actions === snapshot.actions ? snapshot : { ...snapshot, actions };
  }

  const staleSection = <T extends OperatorSection<unknown>>(section: T): T => {
    if (section.state === "unavailable") return section;
    return {
      ...section,
      state: "stale",
      issues: [
        REALTIME_RECONCILING_ISSUE,
        ...(section.issues ?? []).filter(
          (issue) => issue.code !== REALTIME_RECONCILING_ISSUE.code,
        ),
      ],
    };
  };

  const actions = actionSectionForDataState(staleSection(snapshot.actions));
  return {
    ...snapshot,
    attention: staleSection(snapshot.attention),
    economy: staleSection(snapshot.economy),
    funnel: staleSection(snapshot.funnel),
    actions,
    system: staleSection(snapshot.system),
  };
}

function actionSectionForDataState(
  section: OperatorSnapshot["actions"],
): OperatorSnapshot["actions"] {
  if (!section.data) return section;
  const items = section.data.items.map((item) =>
    actionItemForDataState(item, section.state),
  );
  if (items.every((item, index) => item === section.data?.items[index])) {
    return section;
  }
  return { ...section, data: { ...section.data, items } };
}

export function attentionCount(snapshot: OperatorSnapshot): number | null {
  return snapshot.attention.data?.items.length ?? null;
}

function signalCountLabel(count: number): string {
  const absolute = Math.abs(count);
  const lastTwo = absolute % 100;
  const last = absolute % 10;
  const noun =
    lastTwo >= 11 && lastTwo <= 14
      ? "сигналов"
      : last === 1
        ? "сигнал"
        : last >= 2 && last <= 4
          ? "сигнала"
          : "сигналов";
  return `${count} ${noun}`;
}

export function snapshotHeadline(snapshot: OperatorSnapshot): {
  severity: OperatorSeverity;
  title: string;
  detail: string;
} {
  const systemSeverity = snapshot.system.data?.severity ?? "unknown";
  const count = attentionCount(snapshot);
  const overviewState = snapshotOverviewState(snapshot);
  const timezoneState =
    snapshot.meta.cabinet_timezone_state ??
    (snapshot.meta.cabinet_timezone_known ? "single" : "unknown");
  const currencyState = snapshot.meta.currency_state ?? "single";
  const systemConfirmed =
    snapshot.system.state === "ready" && snapshot.system.data !== null;
  // A partial section can still carry confirmed critical evidence (for example,
  // one failed safety source while another source is unavailable). Preserve the
  // section's partial state, but never let the generic degraded headline hide
  // that confirmed critical signal. Cached stale/unavailable evidence remains
  // untrusted and is handled as unknown below.
  const systemSeverityConfirmed =
    (snapshot.system.state === "ready" ||
      snapshot.system.state === "partial") &&
    snapshot.system.data !== null;
  const attentionConfirmed =
    snapshot.attention.state === "ready" && snapshot.attention.data !== null;

  if (systemSeverityConfirmed && systemSeverity === "critical") {
    return {
      severity: "critical",
      title: "Контур требует немедленного внимания",
      detail:
        count == null
          ? "Список рисков пока не подтверждён."
          : `Активных сигналов: ${count}.`,
    };
  }
  if (systemConfirmed && systemSeverity === "warning") {
    return {
      severity: "warning",
      title: "Есть отклонения, требующие решения",
      detail:
        count == null
          ? "Часть данных недоступна."
          : `${signalCountLabel(count)} в работе.`,
    };
  }
  if (overviewState === "stale" || overviewState === "unavailable") {
    return {
      severity: "unknown",
      title: "Состояние ещё не подтверждено",
      detail:
        "Дождитесь актуального снимка или откройте диагностику источников.",
    };
  }
  if (timezoneState !== "single") {
    return {
      severity: "warning",
      title: "Границы суток требуют проверки",
      detail:
        timezoneState === "mixed"
          ? "В выборке несколько часовых поясов; границы рассчитаны отдельно по кабинетам."
          : "Часовой пояс кабинета не подтверждён; дневные границы и суммы оценочные.",
    };
  }
  if (currencyState !== "single") {
    return {
      severity: "warning",
      title: "Денежный контекст требует проверки",
      detail:
        currencyState === "mixed"
          ? "В выборке несколько валют; денежные итоги и сравнения скрыты."
          : "Валюта кабинета не подтверждена; денежные значения скрыты.",
    };
  }
  if (overviewState === "partial") {
    return {
      severity: "warning",
      title: "Данные требуют проверки",
      detail:
        count == null
          ? "Экономика, воронка или источники подтверждены не полностью."
          : `${signalCountLabel(count)} в работе; часть данных подтверждена не полностью.`,
    };
  }
  if (!systemConfirmed || !attentionConfirmed) {
    return {
      severity: "unknown",
      title: "Состояние ещё не подтверждено",
      detail:
        "Дождитесь актуального снимка или откройте диагностику источников.",
    };
  }
  if (
    systemSeverity === "ok" &&
    count === 0 &&
    snapshot.attention.state === "ready"
  ) {
    return {
      severity: "ok",
      title: "Активных рисков нет",
      detail: "Контур и источники подтвердили актуальный снимок.",
    };
  }
  return {
    severity: "unknown",
    title: "Состояние ещё не подтверждено",
    detail: "Дождитесь актуального снимка или откройте диагностику источников.",
  };
}

/** Aggregate the operator overview without treating confirmed empty as degraded. */
export function snapshotOverviewState(snapshot: OperatorSnapshot): DataState {
  const states = [
    snapshot.system?.state,
    snapshot.attention?.state,
    snapshot.economy?.state,
    snapshot.funnel?.state,
  ];
  if (states.includes("unavailable")) return "unavailable";
  if (states.includes("stale")) return "stale";
  const timezoneState =
    snapshot.meta.cabinet_timezone_state ??
    (snapshot.meta.cabinet_timezone_known ? "single" : "unknown");
  const currencyState = snapshot.meta.currency_state ?? "single";
  if (
    states.includes("partial") ||
    timezoneState !== "single" ||
    currencyState !== "single"
  ) {
    return "partial";
  }
  return "ready";
}

export function decimalToNumber(value: string | null): number | null {
  if (value === null || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Prevent cached section data from looking current after freshness is lost. */
export function severityForDataState(
  severity: OperatorSeverity,
  state: DataState,
): OperatorSeverity {
  if (state === "ready") return severity;
  if (state === "partial")
    return severity === "critical" ? "critical" : "warning";
  return "unknown";
}
