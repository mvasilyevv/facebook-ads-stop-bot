/**
 * Быстрое дублирование структуры adset через draft-first контур.
 *
 * Preview ничего не пишет в Meta. POST /launch запускается только явной кнопкой
 * в web-preview; Telegram для этого сценария не требуется.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiSend } from "./client";

export type DuplicateBudgetLevel = "ABO" | "CBO";

export interface AdsetDuplicatePreviewIn {
  source_ad_id: string;
  selected_ad_ids: string[];
  campaign_count: number;
  adsets_per_campaign: number;
  budget_level: DuplicateBudgetLevel;
  daily_budget_cents: number;
  start_date: string | null;
  campaign_name_base?: string | null;
  adset_name_base?: string | null;
  idempotency_token: string;
}

export interface DuplicateSourceEntity {
  id: string;
  name: string;
}

export interface DuplicateSourceAd extends DuplicateSourceEntity {
  fb_ad_id: string;
  delivery_status?: string | null;
  creative_thumb_url?: string | null;
}

export interface AdsetDuplicateSource {
  account: DuplicateSourceEntity & {
    currency?: string | null;
  };
  campaign: DuplicateSourceEntity;
  adset: DuplicateSourceEntity;
  ads: DuplicateSourceAd[];
}

export interface AdsetDuplicateCounts {
  campaigns: number;
  adsets: number;
  ads: number;
  total_objects: number;
}

export interface AdsetDuplicateBudget {
  level: DuplicateBudgetLevel;
  unit_daily_budget_cents: number;
  total_daily_budget_cents: number;
  currency: string;
}

export interface AdsetDuplicateSchedule {
  timezone_name: string;
  offset: string;
  start_time_utc: string;
  start_time_local: string;
}

export interface AdsetDuplicateGeneratedNames {
  campaigns: string[];
  adsets: string[];
}

export interface AdsetDuplicatePreviewOut {
  preview_token: string;
  source: AdsetDuplicateSource;
  format_code: string;
  counts: AdsetDuplicateCounts;
  budget: AdsetDuplicateBudget;
  schedule: AdsetDuplicateSchedule;
  generated_names: AdsetDuplicateGeneratedNames;
  warnings: string[];
  expires_at: string;
}

export interface AdsetDuplicateLaunchIn {
  preview_token: string;
}

export interface AdsetDuplicateLaunchOut {
  task_id: number;
  status: string;
  expires_at: string;
}

export interface AdsetDuplicateProgress {
  phase?: string | null;
  completed?: number | null;
  total?: number | null;
  message?: string | null;
  [key: string]: unknown;
}

export interface AdsetDuplicateStatusOut {
  task_id: number;
  status: string;
  progress: AdsetDuplicateProgress | null;
  created_meta_ids: Record<string, string | string[]>;
  error: string | null;
  expires_at?: string | null;
}

export const TERMINAL_ADSET_DUPLICATE_STATUSES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "expired",
]);

/** Poll active tasks every 2s and stop immediately on every terminal state. */
export function adsetDuplicatePollInterval(status?: string): number | false {
  return status && TERMINAL_ADSET_DUPLICATE_STATUSES.has(status) ? false : 2_000;
}

export function usePreviewAdsetDuplicate() {
  return useMutation<AdsetDuplicatePreviewOut, Error, AdsetDuplicatePreviewIn>({
    mutationFn: (body) =>
      apiSend<AdsetDuplicatePreviewOut>("POST", "/tools/adset-duplicates/preview", body),
  });
}

export function useStartAdsetDuplicate() {
  const queryClient = useQueryClient();
  return useMutation<AdsetDuplicateLaunchOut, Error, AdsetDuplicateLaunchIn>({
    mutationFn: (body) =>
      apiSend<AdsetDuplicateLaunchOut>("POST", "/tools/adset-duplicates/launch", body),
    onSuccess: (draft) => {
      queryClient.setQueryData(["adset-duplicates", draft.task_id], {
        ...draft,
        progress: null,
        created_meta_ids: {},
        error: null,
      } satisfies AdsetDuplicateStatusOut);
    },
  });
}

export function useAdsetDuplicateStatus(taskId: number | null) {
  return useQuery<AdsetDuplicateStatusOut>({
    queryKey: ["adset-duplicates", taskId],
    queryFn: ({ signal }) =>
      apiGet<AdsetDuplicateStatusOut>(`/tools/adset-duplicates/${taskId}`, undefined, signal),
    enabled: taskId != null,
    staleTime: 1_000,
    refetchInterval: (query) => adsetDuplicatePollInterval(query.state.data?.status),
  });
}
