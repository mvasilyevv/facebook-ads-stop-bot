/**
 * DraftCard — карточка AI-mutation черновика для Drafts-страницы.
 *
 * Спека (docs/frontend_mockups/drafts.html):
 *   - meta-line: badge DRAFT + age + tool-name (mutation_kind) + requested-by
 *   - title: verb из MUTATION_KIND_VERBS акцентом + task-id badge справа
 *   - body: DiffTable (3 колонки) ИЛИ PreviewBlock + ReasonBlock (2 колонки)
 *   - footer: expiration timer + [Cancel] / [Approve & execute]
 *
 * Состояния:
 *   expiring-soon — warning-border + "EXPIRING SOON" ribbon
 *   blocked       — disabled approve + lock-note
 *   batch-warning — callout с числом graph-вызовов
 */

import { useEffect, useState } from "react";
import { Clock, Lock, AlertTriangle, Check } from "lucide-react";
import {
  buildDraftDiff,
  draftExpiresAt,
  isExpiringSoon,
  isDraftExpired,
  mutationKindLabel,
  isBulkMutation,
} from "@fb/shared";
import type { DraftOut } from "@fb/shared";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { formatRelativeTime } from "@fb/shared";
import { cn } from "@/lib/utils/cn";
import { DiffTable } from "./DiffTable";
import {
  PreviewBlock,
  buildCreateCampaignPreview,
  buildBulkPausePreview,
} from "./PreviewBlock";

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Форматирует оставшееся время до истечения черновика: "47 мин" / "23ч 47м" / "истёк". */
function formatTimeLeft(expiresAt: Date, now: number): string {
  const ms = expiresAt.getTime() - now;
  if (ms <= 0) return "истёк";
  const totalSec = Math.ceil(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.ceil((totalSec % 3600) / 60);
  if (h === 0) return `${m} мин`;
  return `${h}ч ${m}м`;
}

/** Сокращённый task-id: последние 12 символов hex-части. */
function shortTaskId(id: number | string): string {
  const str = String(id);
  return str.length > 12 ? str.slice(-12) : str;
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface DraftCardProps {
  draft: DraftOut;
  /**
   * Текущее состояние объекта (AdSnapshot / адсет-snapshot) для 3-колоночного diff.
   * Контракт: { status?, daily_budget_cents?, lifetime_budget_cents?, ... }
   */
  currentState?: Record<string, unknown> | null;
  /**
   * Текст AI-reasoning для ReasonBlock. Берётся из payload.reason или отдельного поля.
   */
  reason?: string | null;
  /** Источник (модель): "claude-opus-4-7". */
  reasonSource?: string | null;
  /** Количество graph-вызовов для batch-warning (create_campaign → 4). */
  batchCallCount?: number | null;
  /** Может ли текущий пользователь подтвердить черновик (owner-check). */
  canApprove?: boolean;
  /** Причина блокировки approve (показывается как lock-note). */
  approveBlockedReason?: string | null;
  onApprove?: () => void;
  onCancel?: () => void;
  /** Идёт ли запрос (spinner на кнопке). */
  busy?: boolean;
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function DraftCard({
  draft,
  currentState,
  reason,
  reasonSource,
  batchCallCount,
  canApprove = true,
  approveBlockedReason,
  onApprove,
  onCancel,
  busy,
}: DraftCardProps) {
  // Реактивный таймер — обновляем каждую минуту для expiration-счётчика
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  const expiresAt = draftExpiresAt(draft.created_at);
  const expiring = isExpiringSoon(expiresAt, now);
  const expired = isDraftExpired(expiresAt, now);
  const isBlocked = !canApprove;

  // Тип мутации → полное русское описание и формат body
  const label = mutationKindLabel(draft.mutation_kind);
  const isBulk = isBulkMutation(draft.mutation_kind);
  const isCreateCampaign = draft.mutation_kind === "create_campaign";
  const usePreview = isBulk || isCreateCampaign;

  // Строим DiffRow[] через buildDraftDiff из @fb/shared
  const payload = draft.payload ?? {};
  const diffRows = usePreview ? [] : buildDraftDiff(draft.mutation_kind, payload, currentState);

  // Preview-данные
  const previewProps = isCreateCampaign
    ? buildCreateCampaignPreview(payload)
    : isBulk
      ? buildBulkPausePreview(payload)
      : null;

  return (
    <article
      className={cn(
        "border bg-bg-1 relative transition-colors duration-[200ms]",
        // Состояния границы
        expired
          ? "border-bg-5 opacity-60"
          : expiring
            ? "border-[rgba(212,168,88,0.3)] hover:border-[rgba(212,168,88,0.5)]"
            : "border-bg-5 hover:border-bg-6",
        isBlocked && "border-bg-5",
      )}
      data-expiring={expiring}
      data-blocked={isBlocked}
    >
      {/* ── Ribbon EXPIRING SOON ── */}
      {expiring && !expired && (
        <div
          aria-label="Истекает скоро"
          className={cn(
            "absolute top-[-1px] right-4",
            "bg-warning text-bg-0",
            "font-display font-semibold text-[9px] tracking-[0.14em] uppercase",
            "px-2 py-[3px]",
            // Ribbon не перекрывает border карточки
            "z-10",
          )}
        >
          СКОРО ИСТЕКАЕТ
        </div>
      )}

      {/* ── Header ── */}
      <header className="flex items-start justify-between gap-4 px-6 pt-5 pb-4 border-b border-bg-3">
        <div className="flex-1 min-w-0">
          {/* Meta-line: badge + age + tool-name */}
          <div className="flex items-center gap-2.5 mb-2 font-display text-[10px] tracking-[0.14em] uppercase">
            <Badge variant="draft" size="sm">draft</Badge>
            <span className="text-bg-10">{formatRelativeTime(draft.created_at)}</span>
            <span className="text-bg-6">·</span>
            <span className="text-accent-muted">meta_api / {draft.mutation_kind}</span>
          </div>

          {/* Заголовок: полное русское описание действия */}
          <h3 className="font-display text-[20px] font-medium tracking-[-0.01em] text-bg-11 m-0 leading-[1.2]">
            {label}
            {/* Для bulk — добавляем счётчик объектов */}
            {isBulk && payload["object_ids"] != null && (
              <> · <span className="text-accent">
                {Array.isArray(payload["object_ids"])
                  ? payload["object_ids"].length
                  : "N"}
              </span> объектов</>
            )}
          </h3>

          {/* Запросил */}
          <div className="mt-1.5 font-display text-[11px] text-bg-9 tracking-[0.02em]">
            Запросил{" "}
            <span className="text-bg-10">@{draft.requested_by}</span>
          </div>
        </div>

        {/* Task-id badge */}
        <div className="shrink-0 font-display text-[10px] tracking-[0.04em] text-bg-8 bg-bg-2 border border-bg-5 px-2 py-1">
          <span className="text-bg-7 mr-1">TASK</span>
          {shortTaskId(draft.id)}
        </div>
      </header>

      {/* ── Diff section ── */}
      <div className="px-3 py-2 border-b border-bg-5">
        {usePreview && previewProps ? (
          <PreviewBlock {...previewProps} />
        ) : (
          <DiffTable rows={diffRows} />
        )}
      </div>

      {/* ── AI rationale block ── */}
      {(reason || (batchCallCount != null && batchCallCount > 1)) && (
        <div className="px-5 py-4 border-b border-bg-5">
          {reason && (
            <>
              <span
                className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-9 mb-1.5 inline-block"
              >
                AI · ОБОСНОВАНИЕ
              </span>
              <div className="text-[13px] text-bg-10 leading-[1.5]">
                {reason}
              </div>
              {reasonSource && (
                <div className="mt-1 font-display text-[10px] text-bg-7 tracking-[0.04em]">
                  {reasonSource}
                </div>
              )}
            </>
          )}

          {/* Batch-warning callout */}
          {batchCallCount != null && batchCallCount > 1 && (
            <div
              className={cn(
                "bg-warning-bg border border-[rgba(212,168,88,0.3)]",
                "px-[14px] py-3 flex items-start gap-2.5",
                reason ? "mt-3" : "",
              )}
            >
              <AlertTriangle
                size={14}
                className="text-warning shrink-0 mt-[2px]"
                aria-hidden="true"
              />
              <div className="font-display text-[11px] text-warning leading-[1.5] tracking-[0.02em]">
                Пакетная операция · {batchCallCount} graph-вызовов
                <br />
                При сбое любого шага весь батч откатывается.
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Footer ── */}
      <footer className="flex items-center justify-between gap-4 px-6 py-4 border-t border-bg-3 bg-bg-0">
        {/* Expiration counter */}
        <div
          className={cn(
            "flex items-center gap-2 font-display text-[11px] tracking-[0.02em]",
            expiring ? "text-warning" : "text-bg-9",
          )}
        >
          <Clock
            size={12}
            className={expiring ? "text-warning" : "text-bg-7"}
            aria-hidden="true"
          />
          {expired ? (
            "Черновик истёк"
          ) : (
            <>
              Истекает через{" "}
              <span className={expiring ? "text-warning font-medium" : "text-bg-10"}>
                {formatTimeLeft(expiresAt, now)}
              </span>
            </>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2">
          {/* ACL lock-note если blocked */}
          {isBlocked && approveBlockedReason ? (
            <span className="flex items-center gap-1.5 font-display text-[10.5px] text-warning tracking-[0.02em] mr-3">
              <Lock size={12} aria-hidden="true" />
              {approveBlockedReason}
            </span>
          ) : null}

          <Button
            variant="ghost"
            size="sm"
            onClick={onCancel}
            disabled={busy || expired}
          >
            {isBlocked ? "Отклонить как админ" : "Отклонить"}
          </Button>

          {/* Approve — disabled при blocked или expired */}
          {isBlocked || expired ? (
            <span
              title={approveBlockedReason ?? "Недоступно"}
              className="inline-flex cursor-not-allowed"
            >
              <Button
                variant="primary"
                size="sm"
                disabled
                leftIcon={<Check size={14} aria-hidden="true" />}
              >
                Одобрить и выполнить
              </Button>
            </span>
          ) : (
            <Button
              variant="primary"
              size="sm"
              onClick={onApprove}
              loading={busy}
              disabled={busy}
              leftIcon={!busy ? <Check size={14} aria-hidden="true" /> : undefined}
            >
              Одобрить и выполнить
            </Button>
          )}
        </div>
      </footer>
    </article>
  );
}
