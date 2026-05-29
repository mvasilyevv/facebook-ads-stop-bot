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

export const draftsKeys = KEYS;
