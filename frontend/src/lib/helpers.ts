export function getBadgeTone(value: string): "neutral" | "good" | "warn" | "bad" | "info" {
  const v = value.toLowerCase();
  if (v.includes("ok") || v.includes("active") || v.includes("succes")) {
    return "good";
  }
  if (
    v.includes("warn") ||
    v.includes("learning") ||
    v.includes("paused") ||
    v.includes("stopped")
  ) {
    return "warn";
  }
  if (v.includes("error") || v.includes("fail") || v.includes("reject")) {
    return "bad";
  }
  if (v.includes("manual") || v.includes("block") || v.includes("disabled")) {
    return "info";
  }
  return "neutral";
}

export function formatDeliveryStatusLabel(status: string): string {
  switch (status) {
    case "ACTIVE":
      return "активно";
    case "PAUSED":
      return "на паузе";
    case "LEARNING":
      return "обучение";
    case "NOT_DELIVERING":
      return "не показывается";
    case "UNKNOWN":
      return "неизвестно";
    default:
      return status.replaceAll("_", " ").toLowerCase();
  }
}

export const TRACKED_DELIVERY_STATUSES = [
  "ACTIVE",
  "PAUSED",
  "LEARNING",
  "NOT_DELIVERING",
  "UNKNOWN",
] as const;
