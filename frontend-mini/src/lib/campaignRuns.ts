import type { components } from "@fb/shared/api/generated";

export type CampaignRunSummary = components["schemas"]["RunSummaryOut"];
export type CampaignRunDetail = components["schemas"]["RunDetailOut"];

export type CampaignRunStatus =
  | "queued"
  | "uniquifying"
  | "uploading"
  | "creating"
  | "succeeded"
  | "failed"
  | "cancelled";

export const CAMPAIGN_RUN_STATUS_LABEL: Record<CampaignRunStatus, string> = {
  queued: "В очереди",
  uniquifying: "Уникализация",
  uploading: "Загрузка",
  creating: "Создание",
  succeeded: "Готово",
  failed: "Ошибка",
  cancelled: "Отменён",
};

export const TERMINAL_CAMPAIGN_RUN_STATUSES = new Set<CampaignRunStatus>([
  "succeeded",
  "failed",
  "cancelled",
]);

export function campaignRunStatusLabel(status: string): string {
  return CAMPAIGN_RUN_STATUS_LABEL[status as CampaignRunStatus] ?? status;
}

export function isTerminalCampaignRun(status: string): boolean {
  return TERMINAL_CAMPAIGN_RUN_STATUSES.has(status as CampaignRunStatus);
}
