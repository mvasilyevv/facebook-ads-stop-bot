import type { AdSummary, DecisionExecutionState, DecisionItem } from "../types";

export type StatusTone = "neutral" | "good" | "warn" | "bad" | "info";

export type AdActivitySummary = {
  label: string;
  detail: string;
  tone: StatusTone;
  occurredAt?: string | null;
  occurredLabel: string;
};

export function formatMoney(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  const normalized = typeof value === "number" ? value : Number.parseFloat(String(value));
  if (Number.isNaN(normalized)) {
    return String(value);
  }
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(normalized);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

export function formatRelativeStatus(status: string): string {
  return status.replaceAll("_", " ").toLowerCase();
}

export function formatMetricText(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  return String(value);
}

export function formatCompactId(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const normalized = value.trim();
  if (normalized.length <= 18) {
    return normalized;
  }
  return `${normalized.slice(0, 8)}...${normalized.slice(-6)}`;
}

export function formatCountdown(secondsLeft: number | null | undefined): string {
  if (secondsLeft == null) {
    return "нет данных";
  }
  if (secondsLeft <= 0) {
    return "сейчас";
  }

  const minutes = Math.floor(secondsLeft / 60);
  const seconds = secondsLeft % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function formatDecisionHuman(decision: string, reason: string): string {
  const decisionMap: Record<string, string> = {
    WOULD_PAUSE: "было бы выключено",
    WOULD_RESUME: "было бы включено",
    NO_ACTION: "без изменений",
    SKIPPED_BY_POLICY: "пропущено по политике",
    INSUFFICIENT_DATA: "недостаточно данных",
    AMBIGUOUS: "неоднозначное решение",
    ALERT_REJECTION: "отклонено/не показывается",
    KEPT_PAUSED_BY_VIABILITY: "остаётся на паузе",
  };

  const humanDecision = decisionMap[decision] || decision.toLowerCase();
  return `${humanDecision}: ${reason}`;
}

export function resolveDecisionExecutionState(decision: DecisionItem): DecisionExecutionState {
  if (decision.execution_state) {
    return decision.execution_state;
  }
  if (decision.action_status === "PENDING") {
    return "PENDING";
  }
  if (decision.action_status === "SUCCEEDED" || decision.action_executed) {
    return "SUCCEEDED";
  }
  if (decision.action_status === "FAILED") {
    return "FAILED";
  }
  if (decision.decision === "NO_ACTION") {
    return "NOT_REQUIRED";
  }
  if (decision.decision === "WOULD_PAUSE" || decision.decision === "WOULD_RESUME") {
    return "SKIPPED_BY_MODE";
  }
  return "NOT_REQUIRED";
}

export function formatDecisionExecutionState(state: DecisionExecutionState): string {
  switch (state) {
    case "NOT_REQUIRED":
      return "не требовалось";
    case "SKIPPED_BY_MODE":
      return "пропущено режимом";
    case "PENDING":
      return "выполняется";
    case "SUCCEEDED":
      return "успешно";
    case "FAILED":
      return "ошибка";
  }
}

function toDateScore(value: string | null | undefined): number {
  if (!value) {
    return 0;
  }
  const stamp = new Date(value).getTime();
  return Number.isNaN(stamp) ? 0 : stamp;
}

function resolveManualActionSummary(ad: AdSummary): AdActivitySummary | null {
  const actionStamp = toDateScore(ad.last_action_at);
  const decisionStamp = toDateScore(ad.last_decision_at);
  if (actionStamp === 0 || actionStamp < decisionStamp) {
    return null;
  }

  const source = ad.last_action_source?.trim() || "оператор";
  if (ad.tracking_mode === "MANUAL_BLOCK") {
    return {
      label: "заблокировано вручную",
      detail: `Объявление переведено в ручную блокировку (${source})`,
      tone: "info",
      occurredAt: ad.last_action_at,
      occurredLabel: "Действие",
    };
  }

  if (ad.tracking_mode === "TRACKED" && ad.last_decision === "NO_ACTION") {
    return {
      label: "возвращено в отслеживание",
      detail: `Объявление снова разрешено для автоматической обработки (${source})`,
      tone: "good",
      occurredAt: ad.last_action_at,
      occurredLabel: "Действие",
    };
  }

  return {
    label: "изменено вручную",
    detail: `Последнее ручное изменение выполнено через ${source}`,
    tone: "info",
    occurredAt: ad.last_action_at,
    occurredLabel: "Действие",
  };
}

export function resolveAdActivitySummary(ad: AdSummary): AdActivitySummary {
  const manualSummary = resolveManualActionSummary(ad);
  if (manualSummary != null) {
    return manualSummary;
  }

  const reason = ad.last_decision_reason?.trim();
  const actionMessage = ad.last_action_message?.trim();
  const occurredAt = ad.last_decision_at ?? ad.last_seen_at ?? null;
  const occurredLabel = ad.last_decision_at ? "Решение" : "Проверка";

  switch (ad.last_decision) {
    case "WOULD_PAUSE":
      switch (ad.last_execution_state) {
        case "SUCCEEDED":
          return {
            label: "автопауза выполнена",
            detail: actionMessage || reason || "Объявление автоматически поставлено на паузу",
            tone: "warn",
            occurredAt,
            occurredLabel,
          };
        case "FAILED":
          return {
            label: "автопауза не выполнена",
            detail: actionMessage || "Не удалось поставить объявление на паузу",
            tone: "bad",
            occurredAt,
            occurredLabel,
          };
        case "PENDING":
          return {
            label: "автопауза выполняется",
            detail: reason || "Подготовлено действие на остановку объявления",
            tone: "info",
            occurredAt,
            occurredLabel,
          };
        case "SKIPPED_BY_MODE":
          return {
            label: "действие не выполнено",
            detail: reason || "Причина для паузы найдена, но режим не позволил выполнить действие",
            tone: "neutral",
            occurredAt,
            occurredLabel,
          };
        default:
          return {
            label: "пауза рекомендована",
            detail: reason || "Объявление следует поставить на паузу",
            tone: "warn",
            occurredAt,
            occurredLabel,
          };
      }
    case "WOULD_RESUME":
      switch (ad.last_execution_state) {
        case "SUCCEEDED":
          return {
            label: "авторезюм выполнен",
            detail: actionMessage || reason || "Объявление автоматически снова запущено",
            tone: "good",
            occurredAt,
            occurredLabel,
          };
        case "FAILED":
          return {
            label: "авторезюм не выполнен",
            detail: actionMessage || "Не удалось снова запустить объявление",
            tone: "bad",
            occurredAt,
            occurredLabel,
          };
        case "PENDING":
          return {
            label: "авторезюм выполняется",
            detail: reason || "Подготовлено действие на запуск объявления",
            tone: "info",
            occurredAt,
            occurredLabel,
          };
        case "SKIPPED_BY_MODE":
          return {
            label: "действие не выполнено",
            detail: reason || "Объявление уже безопасно для запуска, но режим не позволил выполнить действие",
            tone: "neutral",
            occurredAt,
            occurredLabel,
          };
        default:
          return {
            label: "готово к запуску",
            detail: reason || "Объявление снова можно запускать",
            tone: "good",
            occurredAt,
            occurredLabel,
          };
      }
    case "SKIPPED_BY_POLICY":
      return {
        label: "заблокировано политикой",
        detail: reason || "Объявление не участвует в автоматических действиях",
        tone: "info",
        occurredAt,
        occurredLabel,
      };
    case "INSUFFICIENT_DATA":
      return {
        label: "действий не было",
        detail: reason || "Недостаточно данных для решения",
        tone: "neutral",
        occurredAt,
        occurredLabel,
      };
    case "AMBIGUOUS":
      return {
        label: "действий не было",
        detail: reason || "Ситуация требует ручной оценки",
        tone: "neutral",
        occurredAt,
        occurredLabel,
      };
    case "ALERT_REJECTION":
      return {
        label: "требует проверки",
        detail: reason || "Объявление отклонено или не доставляется",
        tone: "bad",
        occurredAt,
        occurredLabel,
      };
    case "KEPT_PAUSED_BY_VIABILITY":
      return {
        label: "оставлено на паузе",
        detail: reason || "Объявление пока небезопасно для повторного запуска",
        tone: "warn",
        occurredAt,
        occurredLabel,
      };
    case "NO_ACTION":
      return {
        label: "действий не было",
        detail: reason || "Последняя проверка не потребовала действий",
        tone: "neutral",
        occurredAt,
        occurredLabel,
      };
    default:
      return {
        label: "действий ещё не было",
        detail: "После следующего скана здесь появится итог по объявлению",
        tone: "neutral",
        occurredAt: ad.last_seen_at,
        occurredLabel: "Проверка",
      };
  }
}
