/**
 * Hooks для /drafts-страницы.
 *
 * «Черновики» = DRAFT meta_api_mutation (AI-предложения действий через Marketing API),
 * требующие ручного подтверждения (DRAFT → PENDING через /dashboard/draft-tasks/{id}/confirm).
 * disable/enable не имеют draft-фазы (auto-stop/manual создают pending сразу) и здесь НЕ
 * показываются — их статус виден на Dashboard/Ads. Раньше страница тянула их pending через
 * /retry → 409 (retry только для failed/cancelled); этот мёртвый источник убран.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client";

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
