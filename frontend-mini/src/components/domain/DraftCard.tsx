/**
 * DraftCard — карточка черновика мутации Meta API под канон mini-dashboard.
 *
 * Канон: острые углы, mono числа/коды, var(--bg-N)/text-bg-N,
 * Eyebrow, ribbon «СКОРО ИСТЕКАЕТ», touch ≥44px.
 *
 * Props: onConfirm/onReject — внешние хендлеры, busy — spinner.
 */
import { useEffect, useState } from "react";
import { AlertTriangle, Clock } from "lucide-react";
import type { DraftOut } from "@fb/shared";
import {
  buildDraftDiff,
  draftExpiresAt,
  isExpiringSoon,
  isDraftExpired,
  isBulkMutation,
  mutationKindLabel,
  formatRelativeTime,
} from "@fb/shared";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Eyebrow } from "@/components/data/Eyebrow";
import { haptic, tgConfirm } from "@/lib/tg";
import { cn } from "@/lib/cn";

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Форматирует оставшееся время до истечения: "47 мин" / "23ч 47м". */
function formatTimeLeft(expiresAt: Date, now: number): string {
  const ms = expiresAt.getTime() - now;
  if (ms <= 0) return "истёк";
  const totalSec = Math.ceil(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.ceil((totalSec % 3600) / 60);
  if (h === 0) return `${m} мин`;
  return `${h}ч ${m}м`;
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface DraftCardProps {
  draft: DraftOut;
  /** Текущее состояние объекта для diff-таблицы. */
  currentState?: Record<string, unknown> | null;
  onConfirm: (id: number) => Promise<void>;
  onReject: (id: number) => Promise<void>;
  busy?: boolean;
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function DraftCard({ draft, currentState, onConfirm, onReject, busy = false }: DraftCardProps) {
  // Реактивный таймер — обновляем каждую минуту
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  const expiresAt = draftExpiresAt(draft.created_at);
  const expiring = isExpiringSoon(expiresAt, now);
  const expired = isDraftExpired(expiresAt, now);
  const isBulk = isBulkMutation(draft.mutation_kind);

  // Полное русское описание мутации
  const label = mutationKindLabel(draft.mutation_kind);

  // Строки diff
  const diffRows = buildDraftDiff(
    draft.mutation_kind,
    (draft.payload ?? {}) as Record<string, unknown>,
    currentState ?? null,
  );

  // Число объектов батча
  const payload = (draft.payload ?? {}) as Record<string, unknown>;
  const batchCount = isBulk && Array.isArray(payload["object_ids"])
    ? (payload["object_ids"] as unknown[]).length
    : null;

  return (
    <article
      className={cn(
        "border bg-bg-1 relative",
        expired
          ? "border-bg-5 opacity-60"
          : expiring
            ? "border-[rgba(212,168,88,0.35)]"
            : "border-bg-5",
      )}
      data-testid="draft-card"
    >
      {/* ── Ribbon СКОРО ИСТЕКАЕТ ── */}
      {expiring && !expired && (
        <div
          aria-label="Истекает скоро"
          data-testid="expiring-ribbon"
          className={cn(
            "absolute top-0 right-4",
            "bg-warning text-bg-0",
            "font-display font-semibold text-[9px] tracking-[0.14em] uppercase",
            "px-2 py-[3px]",
            "z-10",
          )}
        >
          СКОРО ИСТЕКАЕТ
        </div>
      )}

      {/* ── Header ── */}
      <header className="px-4 pt-4 pb-3 border-b border-bg-5">
        {/* Meta-line: eyebrow + возраст */}
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <Eyebrow>ЧЕРНОВИК</Eyebrow>
          <span className="font-display tabular-nums text-[10px] text-bg-8">
            {formatRelativeTime(draft.created_at)}
          </span>
        </div>

        {/* Заголовок: полное описание мутации */}
        <h3 className="font-display text-[17px] font-medium tracking-[-0.01em] text-bg-11 m-0 leading-[1.2]">
          {label}
          {/* Батч: N объектов */}
          {isBulk && batchCount != null && (
            <>
              {" · "}
              <span className="text-accent font-display tabular-nums">{batchCount}</span>
              {" объектов"}
            </>
          )}
        </h3>

        {/* Запросил */}
        <p className="mt-1 font-display text-[11px] text-bg-9">
          Запросил{" "}
          <span className="text-bg-10 font-medium">@{draft.requested_by}</span>
        </p>
      </header>

      {/* ── Diff-таблица ── */}
      {diffRows.length > 0 && (
        <div className="border-b border-bg-5">
          {diffRows.map((row, i) => (
            <div
              key={i}
              className={cn(
                "grid grid-cols-3 gap-2 px-4 py-2.5 text-[12px]",
                i > 0 && "border-t border-bg-5",
              )}
            >
              <span className="font-display text-bg-8 truncate">{row.field}</span>
              <span
                className={cn(
                  "font-display tabular-nums truncate text-right",
                  row.changed ? "text-bg-7 line-through" : "text-bg-10",
                )}
              >
                {row.current}
              </span>
              <span
                className={cn(
                  "font-display tabular-nums truncate text-right",
                  row.changed ? "text-accent" : "text-bg-10",
                )}
              >
                {row.target}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* ── Callout «Пакетная операция» ── */}
      {isBulk && batchCount != null && batchCount > 0 && (
        <div className="border-b border-bg-5 px-4 py-3 flex items-start gap-2">
          <AlertTriangle size={13} className="text-warning shrink-0 mt-[2px]" aria-hidden />
          <p className="font-display text-[11px] text-warning leading-[1.5]">
            Пакетная операция · {batchCount} graph-вызовов
          </p>
        </div>
      )}

      {/* ── Footer: таймер + кнопки ── */}
      <footer className="px-4 py-3 flex items-center justify-between gap-3 bg-bg-0">
        {/* Таймер */}
        <div
          className={cn(
            "flex items-center gap-1.5 font-display text-[11px]",
            expired ? "text-danger" : expiring ? "text-warning" : "text-bg-9",
          )}
        >
          <Clock size={11} aria-hidden />
          {expired ? (
            <span>Черновик истёк</span>
          ) : expiring ? (
            <span>
              СКОРО ИСТЕКАЕТ ·{" "}
              <span className="font-medium tabular-nums">{formatTimeLeft(expiresAt, now)}</span>
            </span>
          ) : (
            <span>
              Истекает через{" "}
              <span className="text-bg-10 tabular-nums">{formatTimeLeft(expiresAt, now)}</span>
            </span>
          )}
        </div>

        {/* Кнопки */}
        {!expired && (
          <div className="flex gap-2 shrink-0">
            <Button
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={() => {
                void (async () => {
                  const ok = await tgConfirm("Отклонить этот черновик?");
                  if (!ok) return;
                  haptic.impact("light");
                  await onReject(draft.id);
                })();
              }}
              aria-label="Отклонить черновик"
              className="min-h-[44px] px-3"
            >
              Отклонить
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={busy}
              disabled={busy}
              onClick={() => {
                void (async () => {
                  const ok = await tgConfirm(`Одобрить «${label}»?`);
                  if (!ok) return;
                  haptic.impact("medium");
                  await onConfirm(draft.id);
                })();
              }}
              aria-label="Одобрить и выполнить"
              className="min-h-[44px] px-3"
            >
              Одобрить и выполнить
            </Button>
          </div>
        )}
        {expired && (
          <Badge variant="cancelled" size="sm">истёк</Badge>
        )}
      </footer>
    </article>
  );
}
