/**
 * Единая лента «Решения» (issue #338): чистый селектор/сортировка/классификация
 * поверх `snapshot.attention`. Ни React, ни сети здесь нет — вход снимок,
 * выход данные для рендера. UI (web/mini) строится отдельными PR поверх этого
 * модуля.
 *
 * Источник строк один — `snapshot.attention.data.items`
 * (`apps/api/routers/v1/operator.py:_attention_section`). Второй сборки из
 * `snapshot.incidents`/`snapshot.actions` на клиенте не заводим — единственное
 * исключение см. `resolveActionState` ниже, и это не альтернативный отбор,
 * а точечное обогащение уже отобранной backend'ом строки.
 *
 * Канон: `null` — неизвестно, `0` — подтверждённый ноль; денег/сумм риска эта
 * лента не считает (buyer отклонил числовой «риск» — контракта для суммы нет).
 */
import { collapseOperatorAttentionItems } from "./ledgerSemantics";
import type {
  DataState,
  OperatorActionState,
  OperatorAttentionItem,
  OperatorSeverity,
  OperatorSnapshot,
} from "./contracts";

export type DecisionRowKind = "incident" | "action" | "source";

/** Только для kind=incident строк; для остальных — null (нет жизненного цикла). */
export type DecisionRowStatus = NonNullable<OperatorAttentionItem["status"]>;

export type DecisionPrimaryAction =
  | { readonly kind: "pause"; readonly adId: string }
  | { readonly kind: "acknowledge" }
  | { readonly kind: "check_meta"; readonly href: string | null };

interface DecisionRowBase {
  readonly kind: DecisionRowKind;
  readonly target: OperatorAttentionItem["target"];
  readonly status: DecisionRowStatus | null;
  /**
   * Состояние экшена (failed/unknown/running/...). У kind≠action всегда null.
   * У kind=action — результат join'а с `snapshot.actions`, см. `resolveActionState`;
   * `null` также означает «join не нашёл строку», а не «состояние ok».
   */
  readonly actionState: OperatorActionState | null;
  readonly href: string | null;
}

export interface DecisionRow extends DecisionRowBase {
  readonly id: string;
  readonly severity: OperatorSeverity;
  readonly title: string;
  /** Человеческая контекст-строка: причина, если она есть, иначе summary. */
  readonly contextLine: string;
  readonly occurred_at: string;
  readonly requiresUsdEvidence: boolean;
  /** Применимое primary-действие строки, см. `decisionPrimaryAction`. */
  readonly primaryAction: DecisionPrimaryAction | null;
  /** Исходная строка attention-секции — для навигации/копий, которых нет выше. */
  readonly source: OperatorAttentionItem;
}

const NUMERIC_TARGET_ID = /^\d+$/;
const ACTION_ATTENTION_ID_PREFIX = "task:";

/**
 * `OperatorAttentionItem` для kind=action не несёт структурного состояния
 * экшена — оно запечено строкой в `summary` ("{public_id} · {state}",
 * `operator.py:_attention_section`). Строки с бэкенда не парсим (raw-текст —
 * не источник фактов). Вместо этого джойним по id с уже присутствующим в том
 * же снимке `snapshot.actions.data.items`, где `OperatorActionState` — обычное
 * поле. attention.id для action-строк — `task:<action id>`.
 *
 * Если join не находит строку (например, секция `actions` сама ещё не
 * подтверждена), решение не принимается молча в пользу скрытия: строка
 * остаётся кандидатом ленты. Спрятать потенциальное failed/unknown дороже,
 * чем показать лишнюю строку (см. вердикт buyer про `approaching_stop`:
 * «ошибка должна быть в опасную сторону»).
 */
function resolveActionState(
  item: OperatorAttentionItem,
  actionStateById: ReadonlyMap<string, OperatorActionState>,
): OperatorActionState | null {
  if (item.kind !== "action") return null;
  const actionId = item.id.startsWith(ACTION_ATTENTION_ID_PREFIX)
    ? item.id.slice(ACTION_ATTENTION_ID_PREFIX.length)
    : item.id;
  return actionStateById.get(actionId) ?? null;
}

function buildActionStateIndex(
  snapshot: OperatorSnapshot,
): ReadonlyMap<string, OperatorActionState> {
  const index = new Map<string, OperatorActionState>();
  for (const action of snapshot.actions?.data?.items ?? []) {
    index.set(action.id, action.state);
  }
  return index;
}

function isDecisionCandidate(
  item: OperatorAttentionItem,
  actionState: OperatorActionState | null,
): boolean {
  switch (item.kind) {
    case "incident":
      return true;
    case "action":
      // running — прогресс, не решение; failed/unknown — решение.
      // actionState === null означает «join не нашёл строку», см. resolveActionState.
      if (actionState === null) return true;
      return actionState === "failed" || actionState === "unknown";
    case "source":
      return item.severity !== "ok";
    default:
      // "recommendation" и любые будущие kind в спеку ленты решений не входят.
      return false;
  }
}

/**
 * Какое primary-действие применимо к строке. Проверяет собственные поля
 * строки (а не доверяет тому, что вызывающий уже отфильтровал состояние) —
 * это защита от неправильного использования: `running` экшен, если его
 * всё же передать сюда напрямую, всё равно получит `null`.
 *
 * Приоритет при совпадении условий: пауза важнее подтверждения — она решает
 * денежную проблему, подтверждение только гасит сигнал.
 */
export function decisionPrimaryAction(
  row: DecisionRowBase,
): DecisionPrimaryAction | null {
  if (
    row.kind === "incident" &&
    row.target.kind === "ad" &&
    row.target.id !== null &&
    NUMERIC_TARGET_ID.test(row.target.id)
  ) {
    return { kind: "pause", adId: row.target.id };
  }
  if (row.kind === "incident" && row.status === "open") {
    return { kind: "acknowledge" };
  }
  if (
    row.kind === "action" &&
    (row.actionState === "unknown" || row.actionState === "failed")
  ) {
    return { kind: "check_meta", href: row.href };
  }
  return null;
}

function toDecisionRow(
  item: OperatorAttentionItem,
  actionState: OperatorActionState | null,
): DecisionRow {
  const base: DecisionRowBase = {
    kind: item.kind as DecisionRowKind,
    target: item.target,
    status: item.kind === "incident" ? (item.status ?? null) : null,
    actionState,
    href: item.action?.href ?? null,
  };
  return {
    ...base,
    id: item.id,
    severity: item.severity,
    title: item.title,
    contextLine: item.reason?.trim() || item.summary,
    occurred_at: item.occurred_at,
    requiresUsdEvidence: Boolean(item.requires_usd_evidence),
    primaryAction: decisionPrimaryAction(base),
    source: item,
  };
}

/**
 * Отбор строк ленты «Решения» из `snapshot.attention`.
 * `incident` — всегда; `action` — только failed|unknown; `source` — при
 * severity ≠ ok. Порядок не гарантирован — сортировать `compareDecisionRows`.
 */
export function selectDecisionRows(snapshot: OperatorSnapshot): DecisionRow[] {
  const items = snapshot.attention.data?.items ?? [];
  if (items.length === 0) return [];
  const actionStateById = buildActionStateIndex(snapshot);
  const rows: DecisionRow[] = [];
  for (const item of items) {
    const actionState = resolveActionState(item, actionStateById);
    if (!isDecisionCandidate(item, actionState)) continue;
    rows.push(toDecisionRow(item, actionState));
  }
  return rows;
}

const SEVERITY_RANK: Record<OperatorSeverity, number> = {
  critical: 0,
  // unknown ранжируется выше warning: «неизвестно, что с деньгами» опаснее
  // подтверждённого предупреждения (решение buyer, issue #338).
  unknown: 1,
  warning: 2,
  ok: 3,
};

function moneyRank(target: OperatorAttentionItem["target"]): number {
  return target.kind === "system" ? 1 : 0;
}

function toMillis(iso: string): number {
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? 0 : ms;
}

/**
 * Детерминированный компаратор: severity → деньги раньше системного →
 * старейшее первым (незакрытая команда дорожает со временем, это не лента
 * новостей) → id как последний детерминированный tie-break.
 * Сортировку по сумме риска не вводим — в контракте нет суммы, выдумывать
 * нельзя (buyer REJECT по этому пункту).
 */
export function compareDecisionRows(a: DecisionRow, b: DecisionRow): number {
  const severityDelta = SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
  if (severityDelta !== 0) return severityDelta;
  const moneyDelta = moneyRank(a.target) - moneyRank(b.target);
  if (moneyDelta !== 0) return moneyDelta;
  const occurredDelta = toMillis(a.occurred_at) - toMillis(b.occurred_at);
  if (occurredDelta !== 0) return occurredDelta;
  if (a.id < b.id) return -1;
  if (a.id > b.id) return 1;
  return 0;
}

export interface CollapsedDecisionRow {
  readonly row: DecisionRow;
  readonly count: number;
}

/**
 * Свёртка повторов ленты «Решения» — тонкая обёртка над
 * `collapseOperatorAttentionItems` (ledgerSemantics.ts), применяется ПОСЛЕ
 * сортировки и ТОЛЬКО к строкам без primary-действия: свернуть две строки с
 * «Отключить» нельзя — непонятно, какое объявление гасится (issue #338, п.6).
 * `rows` ожидается уже отсортированным `compareDecisionRows` — порядок входа
 * сохраняется как есть.
 */
export function collapseDecisionRows(
  rows: readonly DecisionRow[],
  usdScopeConfirmed: boolean,
): CollapsedDecisionRow[] {
  const withoutAction = rows.filter((row) => row.primaryAction === null);
  const collapsed = collapseOperatorAttentionItems(
    withoutAction.map((row) => row.source),
    usdScopeConfirmed,
  );
  const countById = new Map(
    collapsed.map((entry) => [entry.item.id, entry.count] as const),
  );
  const keptIds = new Set(countById.keys());

  const result: CollapsedDecisionRow[] = [];
  for (const row of rows) {
    if (row.primaryAction !== null) {
      result.push({ row, count: 1 });
      continue;
    }
    if (!keptIds.has(row.id)) continue; // свёрнуто в более раннюю строку
    result.push({ row, count: countById.get(row.id) ?? 1 });
  }
  return result;
}

/**
 * Возраст строки для показа оператору («висит 3 ч»). Условие одобрения byer:
 * без видимого возраста порядок «старейшее первым» читается как случайный.
 * `now` передаётся явно — иначе функция недетерминирована и её нельзя
 * протестировать фиксированным снимком.
 */
export function decisionRowAge(
  row: Pick<DecisionRow, "occurred_at">,
  now: Date | string | number,
): string {
  const nowMs = now instanceof Date ? now.getTime() : new Date(now).getTime();
  const occurredMs = new Date(row.occurred_at).getTime();
  if (Number.isNaN(nowMs) || Number.isNaN(occurredMs)) return "—";
  const seconds = Math.max(0, Math.round((nowMs - occurredMs) / 1000));
  if (seconds < 45) return "меньше минуты";
  if (seconds < 3600) return `${Math.round(seconds / 60)} мин`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} ч`;
  return `${Math.round(seconds / 86400)} дн`;
}

/**
 * Комбинация DataState нескольких источников ленты (сегодня — только
 * `attention`; появится второй источник — считать через эту функцию, не
 * писать союз состояний заново). Пустой список источников — fail-closed
 * `unavailable`, а не `ready`: ноль источников не значит «всё подтверждено».
 */
export function combineDecisionFeedState(
  states: readonly DataState[],
): DataState {
  if (states.length === 0) return "unavailable";
  if (states.every((state) => state === "unavailable")) return "unavailable";
  if (states.some((state) => state === "unavailable")) return "partial";
  if (states.some((state) => state === "stale")) return "stale";
  if (states.some((state) => state === "partial")) return "partial";
  if (states.every((state) => state === "empty")) return "empty";
  return "ready";
}
