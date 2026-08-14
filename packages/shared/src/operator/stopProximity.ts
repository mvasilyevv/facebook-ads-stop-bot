/**
 * Близость объявления к стоп-правилу — общая семантика web и TMA.
 *
 * Оператор принимает денежное решение «останавливать или нет», поэтому
 * представление обязано различать четыре разных состояния и никогда не
 * подменять одно другим:
 *
 * - `tracked` — правило подтверждено, есть значение, порог и доля пути;
 * - `unevaluated` — оффер сопоставлен, но подтверждённого порога для строки нет;
 * - `not_monitored` — оффер не сопоставлен, авто-стоп это объявление не защищает;
 * - `unknown` — данных нет; это прочерк, а не ноль и не «безопасно».
 *
 * Стадии `warning` и `stop` различаются не только цветом: у них разные символ,
 * форма (`shape`) и текстовый лейбл, поэтому состояние читается в ч/б,
 * в forced-colors и при дальтонизме.
 */

import { ruleCodeLabel } from "../constants/rules";
import {
  compareDecimalStrings,
  formatDecimalPercent,
  formatDecimalValue,
  formatSpend,
  isDecimalString,
} from "../format/number";
import type { OperatorAdRow } from "./contracts";

export type OperatorRuleContext = OperatorAdRow["rule_context"];
export type OperatorStopStage = NonNullable<OperatorRuleContext["stage"]>;

export type OperatorStopProximityKind =
  | "tracked"
  | "unevaluated"
  | "not_monitored"
  | "unknown";

/** Форма маркера. Дублирует цвет, чтобы стадия читалась без него. */
export type OperatorStopProximityShape =
  | "square"
  | "triangle"
  | "circle"
  | "cross"
  | "dot"
  | "question";

export type OperatorStopProximityTone =
  | "critical"
  | "warning"
  | "ok"
  | "neutral";

export interface OperatorStopProximity {
  kind: OperatorStopProximityKind;
  stage: OperatorStopStage | null;
  shape: OperatorStopProximityShape;
  /** Печатный символ той же формы: работает даже без CSS. */
  mark: string;
  label: string;
  tone: OperatorStopProximityTone;
  /** Доля пройденного пути до стопа: «85.4%» либо «—». */
  percentText: string;
  /** Сырое подтверждённое значение доли — для сортировки, не для показа. */
  percent: string | null;
  ruleText: string | null;
  /** «$0.41 из $0.48» либо null, когда значения не подтверждены. */
  detail: string | null;
  offerCode: string | null;
  /** Развёрнутое пояснение для title и screen reader. */
  hint: string;
}

/**
 * Правила, чьи value и threshold выражены в деньгах. Совпадает с серверным
 * `_MONEY_RULE_CODES`: сервер скрывает эти значения без подтверждённой валюты,
 * а клиент обязан форматировать их как деньги, а не как безразмерное число.
 */
const MONEY_RULE_CODES = new Set([
  "cpc_stop",
  "cpl_stop",
  "cpr_stop",
  "spend_no_dep_range",
  "spend_with_dep_range",
]);

const UNKNOWN: OperatorStopProximity = {
  kind: "unknown",
  stage: null,
  shape: "question",
  mark: "?",
  label: "Не подтверждено",
  tone: "neutral",
  percentText: "—",
  percent: null,
  ruleText: null,
  detail: null,
  offerCode: null,
  hint: "Близость к стопу не подтверждена. Прочерк не означает запас.",
};

const STAGE_PRESENTATION: Record<
  OperatorStopStage,
  {
    shape: OperatorStopProximityShape;
    mark: string;
    label: string;
    tone: OperatorStopProximityTone;
  }
> = {
  stop: {
    shape: "square",
    mark: "■",
    label: "Порог пройден",
    tone: "critical",
  },
  warning: {
    shape: "triangle",
    mark: "▲",
    label: "Подходит к стопу",
    tone: "warning",
  },
  none: {
    shape: "circle",
    mark: "○",
    label: "В пределах порога",
    tone: "ok",
  },
};

/** Единая интерпретация `rule_context` для списка, карточки и дашборда. */
export function describeStopProximity(
  context: OperatorRuleContext | null | undefined,
  options: { currency?: string | null } = {},
): OperatorStopProximity {
  // `stage: null` — единственный признак «неизвестно» во всём объекте:
  // строка без подтверждённой оценки не имеет права выглядеть как «none».
  if (!context || context.stage === null) return UNKNOWN;

  if (context.offer_code === null) {
    return {
      kind: "not_monitored",
      stage: context.stage,
      shape: "cross",
      mark: "×",
      label: "Правило не применяется",
      tone: "neutral",
      percentText: "—",
      percent: null,
      ruleText: null,
      detail: null,
      offerCode: null,
      hint: "Объявление не сопоставлено с оффером: стоп-правила к нему не применяются, авто-стоп его не остановит.",
    };
  }

  const percent = isDecimalString(context.percent_to_stop)
    ? context.percent_to_stop
    : null;
  const ruleText = context.rule_code
    ? (context.rule_title ?? ruleCodeLabel(context.rule_code))
    : null;

  if (percent === null || ruleText === null) {
    return {
      kind: "unevaluated",
      stage: context.stage,
      shape: "dot",
      mark: "·",
      label: "Порог не рассчитан",
      tone: "neutral",
      percentText: "—",
      percent: null,
      ruleText,
      detail: null,
      offerCode: context.offer_code,
      hint: `Оффер ${context.offer_code} сопоставлен, но подтверждённого порога для этой строки пока нет.`,
    };
  }

  const presentation = STAGE_PRESENTATION[context.stage];
  const detail = formatRuleAmounts(context, options.currency ?? null);
  const percentText = formatDecimalPercent(percent);
  return {
    kind: "tracked",
    stage: context.stage,
    shape: presentation.shape,
    mark: presentation.mark,
    label: presentation.label,
    tone: presentation.tone,
    percentText,
    percent,
    ruleText,
    detail,
    offerCode: context.offer_code,
    hint: `${presentation.label}. Правило «${ruleText}»${
      detail ? `: ${detail}` : ""
    }, пройдено ${percentText} пути до стопа.`,
  };
}

/**
 * Ширина заполнения шкалы в процентах, decimal-строкой для CSS.
 *
 * Доля выше 100% не растягивает полосу, а неизвестная доля не даёт нулевой:
 * пустая шкала читалась бы как «до стопа далеко».
 */
export function stopProximityBarWidth(
  proximity: OperatorStopProximity,
): string | null {
  if (proximity.percent === null) return null;
  if (compareDecimalStrings(proximity.percent, "100") >= 0) return "100";
  if (compareDecimalStrings(proximity.percent, "0") <= 0) return "0";
  const [whole = "0", fraction = ""] = proximity.percent.split(".");
  const kept = fraction.slice(0, 1).replace(/0+$/, "");
  return kept ? `${whole}.${kept}` : whole;
}

/**
 * Ранжирование строк по близости к стопу.
 *
 * Строки без подтверждённой доли уходят вниз списка с сохранением исходного
 * порядка: неизвестность не занимает место среди подтверждённых оценок, но и
 * не выдаётся за «дальше всех от стопа» — в самой строке она помечена явно.
 */
export function rankAdsByStopProximity<
  T extends Pick<OperatorAdRow, "rule_context">,
>(rows: readonly T[]): T[] {
  return [...rows].sort((left, right) => {
    const leftPercent = describeStopProximity(left.rule_context).percent;
    const rightPercent = describeStopProximity(right.rule_context).percent;
    if (leftPercent === null && rightPercent === null) return 0;
    if (leftPercent === null) return 1;
    if (rightPercent === null) return -1;
    return compareDecimalStrings(rightPercent, leftPercent);
  });
}

function formatRuleAmounts(
  context: OperatorRuleContext,
  currency: string | null,
): string | null {
  if (!isDecimalString(context.value) || !isDecimalString(context.threshold)) {
    return null;
  }
  if (context.rule_code && MONEY_RULE_CODES.has(context.rule_code)) {
    const value = formatSpend(context.value, currency);
    const threshold = formatSpend(context.threshold, currency);
    return value === "—" || threshold === "—"
      ? null
      : `${value} из ${threshold}`;
  }
  return `${formatDecimalValue(context.value)} из ${formatDecimalValue(
    context.threshold,
  )}`;
}
