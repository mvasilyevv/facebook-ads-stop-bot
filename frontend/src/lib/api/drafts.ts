/**
 * Hooks для /drafts-страницы.
 *
 * Drafts — это task_queue записи со status='draft' (любой task_type).
 * Используется существующий /dashboard/disable-tasks?status=draft + аналог для enable + meta_api_mutation.
 *
 * До появления отдельного /drafts endpoint'а на бэке — собираем 3 списка
 * через существующие фильтры и мерджим на фронте.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { TaskQueueRow } from "@/lib/types/api";

const KEYS = {
  drafts: (task_type?: string) => ["drafts", task_type] as const,
};

export function useDrafts(task_type?: string) {
  return useQuery({
    queryKey: KEYS.drafts(task_type),
    queryFn: async () => {
      const params = { status: "PENDING", limit: 100 };
      const [disable, enable] = await Promise.all([
        apiClient.get<TaskQueueRow[]>("/dashboard/disable-tasks", params),
        apiClient.get<TaskQueueRow[]>("/dashboard/enable-tasks", params),
      ]);
      const all = [...disable, ...enable];
      return task_type ? all.filter((t) => t.task_type === task_type) : all;
    },
    refetchInterval: 30_000,
  });
}

export function useApproveDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, task_type }: { id: string; task_type: string }) => {
      const endpoint =
        task_type === "enable" ? `/dashboard/enable-tasks/${id}/retry` : `/dashboard/disable-tasks/${id}/retry`;
      return apiClient.post<TaskQueueRow>(endpoint);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["drafts"] }),
  });
}

export function useCancelDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, task_type }: { id: string; task_type: string }) => {
      const endpoint =
        task_type === "enable" ? `/dashboard/enable-tasks/${id}` : `/dashboard/disable-tasks/${id}`;
      return apiClient.delete<void>(endpoint);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["drafts"] }),
  });
}

// ─── meta_api_mutation черновики (status='draft', через admin-роутер) ────────

/** DRAFT meta-mutation задача (AI-предложение действия через Marketing API). */
export interface MetaDraft {
  id: number;
  mutation_kind: string;
  target_id: string | null;
  ad_account_id: string | null;
  payload: Record<string, unknown>;
  requested_by: string;
  created_at: string | null;
}

/** GET /dashboard/draft-tasks — реальные DRAFT meta_api_mutation. */
export function useMetaDrafts() {
  return useQuery({
    queryKey: ["drafts", "meta"] as const,
    queryFn: () => apiClient.get<MetaDraft[]>("/dashboard/draft-tasks", { limit: 100 }),
    refetchInterval: 30_000,
  });
}

/** POST /dashboard/draft-tasks/{id}/confirm — DRAFT → PENDING (admin-зона). */
export function useConfirmMetaDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiClient.post<unknown>(`/dashboard/draft-tasks/${id}/confirm`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["drafts"] }),
  });
}

/** POST /dashboard/draft-tasks/{id}/reject — DRAFT → CANCELLED. */
export function useRejectMetaDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      apiClient.post<unknown>(`/dashboard/draft-tasks/${id}/reject`, {
        reason: "rejected via dashboard",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["drafts"] }),
  });
}

export const draftsKeys = KEYS;
