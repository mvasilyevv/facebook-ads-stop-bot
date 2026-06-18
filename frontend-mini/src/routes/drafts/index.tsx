/**
 * DraftsPage — список черновиков AI-мутаций Meta API.
 * Маршрут: /drafts/ (TanStack Router, file-based).
 *
 * Черновики — ОСНОВНОЙ таб, TabBar виден. TG back-button не нужен.
 * Сортировка: истекающие первыми (isExpiringSoon).
 *
 * Шапка: MiniHeader eyebrow="ОДОБРЕНИЕ" title="Черновики"
 * right=<счётчик ожидающих>.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { draftExpiresAt, isExpiringSoon } from "@fb/shared";
import type { DraftOut } from "@fb/shared";
import { useTmaDrafts, useTmaConfirmDraft, useTmaRejectDraft } from "@/lib/api";
import { haptic } from "@/lib/tg";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { DraftCard } from "@/components/domain/DraftCard";

export const Route = createFileRoute("/drafts/")({
  component: DraftsPage,
});

// ─── Счётчик ожидающих (pending badge) ──────────────────────────────────────

function PendingCount({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <span
      className="inline-flex items-center justify-center font-display font-semibold text-[11px] tabular-nums bg-warning text-bg-0 min-w-[22px] h-[22px] px-1.5 rounded-full"
      aria-label={`${count} ожидают`}
    >
      {count}
    </span>
  );
}

// ─── Сортировка: истекающие первыми ─────────────────────────────────────────

function sortDrafts(drafts: DraftOut[]): DraftOut[] {
  const now = Date.now();
  return [...drafts].sort((a, b) => {
    const aExp = a.created_at ? isExpiringSoon(draftExpiresAt(a.created_at), now) : false;
    const bExp = b.created_at ? isExpiringSoon(draftExpiresAt(b.created_at), now) : false;
    if (aExp && !bExp) return -1;
    if (!aExp && bExp) return 1;
    // внутри группы: старее — первые (истекут раньше)
    const aTs = a.created_at ? new Date(a.created_at).getTime() : 0;
    const bTs = b.created_at ? new Date(b.created_at).getTime() : 0;
    return aTs - bTs;
  });
}

// ─── Страница ────────────────────────────────────────────────────────────────

function DraftsPage() {
  const { data: rawDrafts = [], isLoading, isError, error, refetch } = useTmaDrafts();
  const confirmDraft = useTmaConfirmDraft();
  const rejectDraft = useTmaRejectDraft();

  // Busy-состояние per-card (id → boolean)
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set());
  const setBusy = (id: number, val: boolean) =>
    setBusyIds((prev) => {
      const next = new Set(prev);
      if (val) { next.add(id); } else { next.delete(id); }
      return next;
    });

  // Авто-рефреш раз в 30с
  useEffect(() => {
    const t = setInterval(() => { void refetch(); }, 30_000);
    return () => clearInterval(t);
  }, [refetch]);

  const drafts = sortDrafts(rawDrafts);
  const pendingCount = rawDrafts.length;

  async function handleConfirm(id: number) {
    setBusy(id, true);
    try {
      await confirmDraft.mutateAsync({ taskId: id });
      haptic.notify("success");
      void refetch();
    } catch {
      haptic.notify("error");
    } finally {
      setBusy(id, false);
    }
  }

  async function handleReject(id: number) {
    setBusy(id, true);
    try {
      await rejectDraft.mutateAsync({ taskId: id });
      haptic.notify("success");
      void refetch();
    } catch {
      haptic.notify("error");
    } finally {
      setBusy(id, false);
    }
  }

  return (
    <div className="flex flex-col">
      {/* ── Шапка ── */}
      <MiniHeader
        eyebrow="ОДОБРЕНИЕ"
        title="Черновики"
        right={<PendingCount count={pendingCount} />}
      />

      <div className="flex flex-col gap-3 p-4">
        {/* Загрузка */}
        {isLoading && (
          <>
            <Skeleton className="h-[120px] w-full" />
            <Skeleton className="h-[120px] w-full" />
            <Skeleton className="h-[120px] w-full" />
          </>
        )}

        {/* Ошибка */}
        {isError && !isLoading && (
          <EmptyState
            title="Ошибка загрузки"
            description={(error as Error | null)?.message ?? "Не удалось загрузить черновики"}
            action={{ label: "Повторить", onClick: () => void refetch() }}
          />
        )}

        {/* Пусто */}
        {!isLoading && !isError && drafts.length === 0 && (
          <EmptyState
            title="Черновиков нет"
            description="Черновики создаются AI-ботом или командами /clone /budget /pause_offer в Telegram"
            action={{ label: "Обновить", onClick: () => void refetch() }}
          />
        )}

        {/* Список */}
        {!isLoading && !isError && drafts.length > 0 &&
          drafts.map((draft) => (
            <DraftCard
              key={draft.id}
              draft={draft}
              onConfirm={handleConfirm}
              onReject={handleReject}
              busy={busyIds.has(draft.id)}
            />
          ))
        }
      </div>
    </div>
  );
}
