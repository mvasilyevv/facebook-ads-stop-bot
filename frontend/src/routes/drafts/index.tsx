/**
 * Drafts — страница черновиков AI-мутаций (ожидающих подтверждения).
 *
 * Макет (docs/frontend_mockups/drafts.html):
 *   PageHeader eyebrow "03" / "РУЧНОЙ КОНТРОЛЬ · ПОДТВЕРДИТЬ · ВЫПОЛНИТЬ"
 *   Filter-pills по mutation_kind + sort expiring-first
 *   Список DraftCard (approve/cancel через ConfirmDialog)
 *
 * Решение current_state для DiffTable:
 *   TmaDraftOut из списка не содержит current_state — detail-эндпоинт
 *   (GET /drafts/{id}) также не добавляет его (по спеке только у TMA есть контекст).
 *   Передаём currentState=null → DiffTable показывает "—" в колонке Current.
 *   Это валидный fallback: пользователь видит target-значение и описание мутации.
 *   При наличии target_id можно в будущем подтянуть AdSnapshot,
 *   но для MVP достаточно — DiffTable это явно обрабатывает.
 */

import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { FileEdit } from "lucide-react";

import { useMetaDrafts, useConfirmDraft, useRejectDraft } from "@/lib/api/drafts";
import { DraftCard } from "@/components/domain/drafts/DraftCard";
import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { FilterPill } from "@/components/ui/Pill";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  MUTATION_KINDS,
  mutationKindLabel,
  draftExpiresAt,
  isExpiringSoon,
  isDraftExpired,
} from "@fb/shared";
import type { DraftOut, MutationKind } from "@fb/shared";

export const Route = createFileRoute("/drafts/")({
  component: DraftsPage,
});

// ─── Типы ────────────────────────────────────────────────────────────────────

type FilterKind = "all" | MutationKind;

// ─── Хелперы ─────────────────────────────────────────────────────────────────

/** Сортировка: истекающие — вверх, потом по created_at desc. */
function sortDrafts(drafts: DraftOut[]): DraftOut[] {
  const now = Date.now();
  return [...drafts].sort((a, b) => {
    const aExp = draftExpiresAt(a.created_at);
    const bExp = draftExpiresAt(b.created_at);
    const aSoon = isExpiringSoon(aExp, now) && !isDraftExpired(aExp, now);
    const bSoon = isExpiringSoon(bExp, now) && !isDraftExpired(bExp, now);

    // Истекающие — первыми
    if (aSoon && !bSoon) return -1;
    if (!aSoon && bSoon) return 1;

    // Среди однородных — по убыванию created_at (новые ближе к концу срока)
    const aTs = a.created_at ? new Date(a.created_at).getTime() : 0;
    const bTs = b.created_at ? new Date(b.created_at).getTime() : 0;
    return bTs - aTs;
  });
}

/** Определяет batchCallCount по mutation_kind (create_campaign = 4 batch calls). */
function getBatchCallCount(kind: string): number | null {
  if (kind === "create_campaign") return 4;
  if (kind === "bulk_status_change") return 3;
  return null;
}

// ─── Основной компонент ───────────────────────────────────────────────────────

function DraftsPage() {
  const { data: drafts, isLoading, isError, error, refetch } = useMetaDrafts();
  const confirmMutation = useConfirmDraft();
  const rejectMutation = useRejectDraft();

  // Фильтр по mutation_kind
  const [filter, setFilter] = useState<FilterKind>("all");
  // Черновик, для которого открыт ConfirmDialog (approve или cancel)
  const [pendingApprove, setPendingApprove] = useState<DraftOut | null>(null);
  const [pendingCancel, setPendingCancel] = useState<DraftOut | null>(null);

  // ── Skeleton ──
  if (isLoading) {
    return (
      <div className="space-y-6">
        <DraftsHeader count={null} />
        <div className="flex gap-2 pb-5 border-b border-bg-5 mb-8">
          {["all", "pause_ad", "bulk_status_change"].map((k) => (
            <Skeleton key={k} height={28} width={80} />
          ))}
        </div>
        {[1, 2].map((i) => (
          <Skeleton key={i} variant="block" height={220} />
        ))}
      </div>
    );
  }

  // ── Error ──
  if (isError) {
    return (
      <div className="space-y-6">
        <DraftsHeader count={null} />
        <ErrorState error={error} onRetry={() => void refetch()} />
      </div>
    );
  }

  const allDrafts = drafts ?? [];

  // Фильтруем по mutation_kind
  const filtered =
    filter === "all" ? allDrafts : allDrafts.filter((d) => d.mutation_kind === filter);

  // Сортируем: expiring-first
  const sorted = sortDrafts(filtered);

  // Считаем для pills
  const countByKind = new Map<string, number>();
  for (const d of allDrafts) {
    countByKind.set(d.mutation_kind, (countByKind.get(d.mutation_kind) ?? 0) + 1);
  }

  // Уникальные kinds, которые реально есть в данных
  const presentKinds = MUTATION_KINDS.filter((k) => (countByKind.get(k) ?? 0) > 0);

  return (
    <>
      <DraftsHeader count={allDrafts.length} />

      {/* ── Filter pills ── */}
      <div
        role="toolbar"
        aria-label="Фильтр по типу мутации"
        className="flex items-center gap-2 mb-6 flex-wrap"
      >
        <FilterPill active={filter === "all"} onClick={() => setFilter("all")}>
          Все
          <span className="text-[10px] opacity-70 ml-0.5">{allDrafts.length}</span>
        </FilterPill>
        {presentKinds.map((kind) => (
          <FilterPill
            key={kind}
            active={filter === kind}
            onClick={() => setFilter(filter === kind ? "all" : kind)}
          >
            {mutationKindLabel(kind)}
            <span className="text-[10px] opacity-70 ml-0.5">{countByKind.get(kind)}</span>
          </FilterPill>
        ))}
      </div>

      {/* ── Empty state ── */}
      {sorted.length === 0 && (
        <EmptyState
          icon={<FileEdit size={32} />}
          title="Черновиков нет"
          description={
            filter === "all"
              ? "AI ничего не предлагает — все операции выполнены или ожидание команды."
              : `Нет черновиков типа «${mutationKindLabel(filter)}».`
          }
        />
      )}

      {/* ── Список DraftCard — max-width 760px стопкой ── */}
      {sorted.length > 0 && (
        <div className="flex flex-col gap-4 max-w-[760px]" role="list" aria-label="Список черновиков">
          {sorted.map((draft) => {
            const reason =
              typeof draft.payload?.["reason"] === "string"
                ? (draft.payload["reason"] as string)
                : null;
            const reasonSource =
              typeof draft.payload?.["model"] === "string"
                ? (draft.payload["model"] as string)
                : null;
            const batchCallCount = getBatchCallCount(draft.mutation_kind);

            return (
              <div key={draft.id} role="listitem">
                <DraftCard
                  draft={draft}
                  // currentState=null → DiffTable показывает "—" в Current (валидный fallback)
                  currentState={null}
                  reason={reason}
                  reasonSource={reasonSource}
                  batchCallCount={batchCallCount}
                  busy={
                    (confirmMutation.isPending &&
                      confirmMutation.variables === String(draft.id)) ||
                    (rejectMutation.isPending &&
                      rejectMutation.variables === String(draft.id))
                  }
                  onApprove={() => setPendingApprove(draft)}
                  onCancel={() => setPendingCancel(draft)}
                />
              </div>
            );
          })}
        </div>
      )}

      {/* ── ConfirmDialog: Approve ── */}
      <ConfirmDialog
        open={pendingApprove !== null}
        onOpenChange={(open) => { if (!open) setPendingApprove(null); }}
        title="Подтвердить и выполнить?"
        description={
          pendingApprove
            ? `Мутация «${mutationKindLabel(pendingApprove.mutation_kind)}» будет передана в очередь исполнения через Marketing API. Действие необратимо.`
            : ""
        }
        confirmLabel="Approve & execute"
        confirmVariant="primary"
        onConfirm={async () => {
          if (!pendingApprove) return;
          await confirmMutation.mutateAsync(String(pendingApprove.id));
        }}
      />

      {/* ── ConfirmDialog: Cancel ── */}
      <ConfirmDialog
        open={pendingCancel !== null}
        onOpenChange={(open) => { if (!open) setPendingCancel(null); }}
        title="Отменить черновик?"
        description={
          pendingCancel
            ? `Черновик «${mutationKindLabel(pendingCancel.mutation_kind)}» будет удалён. Это действие необратимо.`
            : ""
        }
        confirmLabel="Отменить черновик"
        confirmVariant="danger"
        onConfirm={async () => {
          if (!pendingCancel) return;
          await rejectMutation.mutateAsync(String(pendingCancel.id));
        }}
      />
    </>
  );
}

// ─── PageHeader ────────────────────────────────────────────────────────────────

function DraftsHeader({ count }: { count: number | null }) {
  return (
    <PageHeader
      eyebrowNum="04"
      eyebrow="OPERATE · ОДОБРЕНИЕ"
      title="Черновики"
      displayNumber="04"
      subtitle={
        count !== null ? (
          <>
            <span className="text-bg-11 font-medium">{count}</span>
            <HeaderSep />
            ожидают подтверждения
          </>
        ) : undefined
      }
    />
  );
}
