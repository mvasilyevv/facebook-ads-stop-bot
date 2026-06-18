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
import { toast } from "@/components/ui/Toast";
import type { DraftOut } from "@fb/shared";

// M3/L5: реальный shape бэка — TmaDraftActionResponse { ok, detail }. Старый тип
// { ok, task_id, status } не совпадал, а отсутствие toast делало подтверждение
// money-мутации немым (403/409 проглатывались).
interface DraftActionResponse {
  ok: boolean;
  detail: string;
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
    onSuccess: (res) => {
      toast.success("Черновик подтверждён", res?.detail);
      qc.invalidateQueries({ queryKey: ["drafts"] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
    // ошибку (403/409) показывает глобальный MutationCache.onError
  });
}

// ─── Отклонить черновик ───────────────────────────────────────────────────────

export function useRejectDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) =>
      apiSend<DraftActionResponse>("POST", `/dashboard/draft-tasks/${taskId}/reject`),
    onSuccess: (res) => {
      toast.success("Черновик отклонён", res?.detail);
      qc.invalidateQueries({ queryKey: ["drafts"] });
    },
  });
}
