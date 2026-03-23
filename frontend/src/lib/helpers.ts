import type { AdSummary } from "../types";

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

export function isAttentionAdSummary(ad: AdSummary): boolean {
  const deliveryStatus = ad.delivery_status.toUpperCase();
  const decision = ad.last_decision.toUpperCase();
  const executionState = ad.last_execution_state?.toUpperCase() ?? "";

  if (ad.scope_presence === "NOT_SEEN_THIS_SCAN") {
    return true;
  }
  if (deliveryStatus.includes("NOT_DELIVERING")) {
    return true;
  }
  if (executionState === "FAILED") {
    return true;
  }
  return decision === "WOULD_PAUSE" || decision === "ALERT_REJECTION";
}
