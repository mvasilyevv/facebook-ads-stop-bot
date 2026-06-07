/**
 * DraftsPage — список черновиков мутаций Meta API.
 * Маршрут: /drafts/ (TanStack Router, file-based).
 *
 * API: useTmaDrafts + useTmaConfirmDraft + useTmaRejectDraft из @/lib/api.
 * BackButton включается автоматически (BACK_BUTTON_PATTERNS /^\/drafts\// в TelegramBackButton.tsx).
 * TabBar скрывается (HIDDEN_ON /^\/drafts\//).
 */
import { createFileRoute } from "@tanstack/react-router";
import { useTmaDrafts, useTmaConfirmDraft, useTmaRejectDraft } from "@/lib/api";
import { EmptyState, ErrorState } from "@/components/ui";
import { DraftCard } from "@/components/domain/DraftCard";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Skeleton } from "@/components/ui";

export const Route = createFileRoute("/drafts/")({
  component: DraftsPage,
});

function DraftsPage() {
  const { data: drafts = [], isLoading, isError, error, refetch } = useTmaDrafts();
  const confirmDraft = useTmaConfirmDraft();
  const rejectDraft = useTmaRejectDraft();

  async function handleConfirm(id: number) {
    await confirmDraft.mutateAsync({ taskId: id });
  }

  async function handleReject(id: number) {
    await rejectDraft.mutateAsync({ taskId: id });
  }

  return (
    <div>
      <MiniHeader
        eyebrow="Черновики"
        title={isLoading ? "Черновики" : `Черновики (${drafts.length})`}
        right={
          !isLoading && !isError && drafts.length > 0 ? (
            <button
              type="button"
              onClick={() => void refetch()}
              className="text-[11px] font-mono text-[var(--color-bg-9)] hover:text-[var(--color-bg-11)] transition-colors"
              aria-label="Обновить"
            >
              Обновить
            </button>
          ) : undefined
        }
      />

      <div className="px-4 pt-4">
        {/* Загрузка — скелетоны */}
        {isLoading && (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
          </div>
        )}

        {/* Ошибка */}
        {isError && !isLoading && (
          <ErrorState
            message={(error as Error | null)?.message ?? "Не удалось загрузить черновики"}
            onRetry={() => void refetch()}
          />
        )}

        {/* Список черновиков */}
        {!isLoading && !isError && drafts.length > 0 && (
          <>
            <p className="text-[12px] text-[var(--color-bg-9)] mb-4 leading-relaxed">
              Подтвердите задачу — она пойдёт в очередь на исполнение через Meta API.
              Отмена переводит задачу в статус CANCELLED.
            </p>
            {drafts.map((draft) => (
              <DraftCard
                key={draft.id}
                draft={draft}
                onConfirm={handleConfirm}
                onReject={handleReject}
              />
            ))}
          </>
        )}

        {/* Пусто */}
        {!isLoading && !isError && drafts.length === 0 && (
          <EmptyState
            title="Черновиков нет"
            description="Черновики создаются AI-ботом или через /clone /budget /pause_offer в Telegram"
            action={{
              label: "Обновить",
              onClick: () => void refetch(),
            }}
          />
        )}
      </div>
    </div>
  );
}
