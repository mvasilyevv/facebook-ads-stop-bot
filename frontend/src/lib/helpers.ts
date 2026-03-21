export function getBadgeTone(value: string): "neutral" | "good" | "warn" | "bad" | "info" {
  if (value.includes("ok") || value.includes("active") || value.includes("succes")) {
    return "good";
  }
  if (
    value.includes("warn") ||
    value.includes("learning") ||
    value.includes("paused") ||
    value.includes("stopped")
  ) {
    return "warn";
  }
  if (value.includes("error") || value.includes("fail") || value.includes("reject")) {
    return "bad";
  }
  if (value.includes("manual") || value.includes("block") || value.includes("disabled")) {
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
