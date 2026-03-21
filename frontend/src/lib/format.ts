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
