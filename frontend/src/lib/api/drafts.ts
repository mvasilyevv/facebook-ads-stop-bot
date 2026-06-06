/**
 * API-хуки для Drafts-страницы (мета-мутации, ожидающие подтверждения).
 *
 * Эндпоинты:
 *   GET  /api/dashboard/draft-tasks               → DraftOut[]  (список черновиков)
 *   POST /api/dashboard/draft-tasks/{id}/confirm  → DraftActionResponse
 *   POST /api/dashboard/draft-tasks/{id}/reject   → DraftActionResponse
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";
import type { DraftOut } from "@fb/shared";

interface DraftActionResponse {
  ok: boolean;
  task_id: string;
  status: string;
}

// ─── Список черновиков ────────────────────────────────────────────────────────

export function useMetaDrafts() {
  return useQuery<DraftOut[]>({
    queryKey: ["drafts"],
    queryFn: ({ signal }) => apiGet<DraftOut[]>("/dashboard/draft-tasks", undefined, signal),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

// ─── Подтвердить черновик ─────────────────────────────────────────────────────

export function useConfirmDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) =>
      apiSend<DraftActionResponse>("POST", `/dashboard/draft-tasks/${taskId}/confirm`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drafts"] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

// ─── Отклонить черновик ───────────────────────────────────────────────────────

export function useRejectDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) =>
      apiSend<DraftActionResponse>("POST", `/dashboard/draft-tasks/${taskId}/reject`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drafts"] });
    },
  });
}
