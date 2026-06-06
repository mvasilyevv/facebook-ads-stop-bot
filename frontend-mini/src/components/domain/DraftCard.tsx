/**
 * DraftCard — карточка черновика для DraftsPage.
 * Компактный: тип-бейдж, verb, diff-таблица, таймер, Confirm/Reject.
 * Локальный компонент (не трогает ui/).
 */
import { useState } from "react";
import type { DraftOut } from "@fb/shared";
import {
  MUTATION_KIND_VERBS,
  MUTATION_KIND_LABELS,
  buildDraftDiff,
  draftExpiresAt,
  isExpiringSoon,
  isDraftExpired,
  formatDateTime,
  formatRelativeTime,
} from "@fb/shared";
import { Badge, Button, Card } from "@/components/ui";
import { haptic, tgConfirm } from "@/lib/tg";
import { cn } from "@/lib/cn";

interface DraftCardProps {
  draft: DraftOut;
  /** currentState для buildDraftDiff — может прийти из useTmaDraftDetail */
  currentState?: Record<string, unknown> | null;
  onConfirm: (id: number) => Promise<void>;
  onReject: (id: number) => Promise<void>;
}

export function DraftCard({ draft, currentState, onConfirm, onReject }: DraftCardProps) {
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const expiresAt = draftExpiresAt(draft.created_at);
  const expiring = isExpiringSoon(expiresAt);
  const expired = isDraftExpired(expiresAt);

  // Строки diff через shared buildDraftDiff
  const diffRows = buildDraftDiff(
    draft.mutation_kind,
    (draft.payload ?? {}) as Record<string, unknown>,
    currentState ?? null,
  );

  const verb = MUTATION_KIND_VERBS[draft.mutation_kind as keyof typeof MUTATION_KIND_VERBS]
    ?? draft.mutation_kind.toUpperCase();
  const label = MUTATION_KIND_LABELS[draft.mutation_kind as keyof typeof MUTATION_KIND_LABELS]
    ?? draft.mutation_kind;

  async function handleConfirm() {
    const confirmed = await tgConfirm(`Подтвердить «${label}»?`);
    if (!confirmed) return;
    setBusy(true);
    setLocalError(null);
    haptic.impact("medium");
    try {
      await onConfirm(draft.id);
      haptic.notify("success");
    } catch (err) {
      haptic.notify("error");
      setLocalError((err as Error).message ?? "Ошибка");
      setBusy(false);
    }
  }

  async function handleReject() {
    const confirmed = await tgConfirm("Отменить этот черновик?");
    if (!confirmed) return;
    setBusy(true);
    setLocalError(null);
    haptic.impact("light");
    try {
      await onReject(draft.id);
      haptic.notify("success");
    } catch (err) {
      haptic.notify("error");
      setLocalError((err as Error).message ?? "Ошибка");
      setBusy(false);
    }
  }

  return (
    <Card className="mb-3">
      {/* Шапка: verb-бейдж + дата */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={expired ? "cancelled" : expiring ? "warning" : "pending"}>
            {verb}
          </Badge>
          {draft.target_id && (
            <span className="font-mono text-[10px] text-[var(--color-bg-9)]">
              {draft.target_id}
            </span>
          )}
        </div>
        <p className="text-[11px] text-[var(--color-bg-9)] font-mono shrink-0">
          {formatRelativeTime(draft.created_at)}
        </p>
      </div>

      {/* Описание */}
      <p className="text-[13px] text-[var(--color-bg-11)] mb-3 leading-snug">{label}</p>

      {/* Diff-таблица */}
      {diffRows.length > 0 && (
        <div className="mb-3 border border-[var(--color-bg-5)]">
          {diffRows.map((row, i) => (
            <div
              key={i}
              className={cn(
                "grid grid-cols-3 gap-2 px-3 py-2 text-[12px]",
                i > 0 && "border-t border-[var(--color-bg-5)]",
              )}
            >
              <span className="text-[var(--color-bg-9)] font-mono col-span-1 truncate">
                {row.field}
              </span>
              <span
                className={cn(
                  "font-mono col-span-1 truncate text-right",
                  row.changed ? "text-[var(--color-bg-8)] line-through" : "text-[var(--color-bg-10)]",
                )}
              >
                {row.current}
              </span>
              <span
                className={cn(
                  "font-mono col-span-1 truncate text-right",
                  row.changed ? "text-[var(--color-accent)]" : "text-[var(--color-bg-10)]",
                )}
              >
                {row.target}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Таймер истечения */}
      <div className="flex items-center gap-2 mb-3">
        <p
          className={cn(
            "text-[11px] font-mono",
            expired
              ? "text-[var(--color-danger)]"
              : expiring
              ? "text-[var(--color-warning)]"
              : "text-[var(--color-bg-9)]",
          )}
        >
          {expired
            ? "Истёк"
            : expiring
            ? `Истекает: ${formatDateTime(expiresAt.toISOString())}`
            : `До: ${formatDateTime(expiresAt.toISOString())}`}
        </p>
      </div>

      {/* Ошибка */}
      {localError && (
        <p className="text-[12px] text-[var(--color-danger)] mb-2">{localError}</p>
      )}

      {/* Кнопки */}
      {!expired && (
        <div className="flex gap-2">
          <Button
            variant="primary"
            size="md"
            fullWidth
            loading={busy}
            onClick={handleConfirm}
          >
            Подтвердить
          </Button>
          <Button
            variant="secondary"
            size="md"
            fullWidth
            disabled={busy}
            onClick={handleReject}
          >
            Отменить
          </Button>
        </div>
      )}
      {expired && (
        <Button variant="ghost" size="sm" disabled>
          Истёк — больше недоступен
        </Button>
      )}
    </Card>
  );
}
