/**
 * DraftCard — карточка AI-mutation draft.
 * См. docs/frontend_v2_design.md §4.6.
 *
 * Header: eyebrow (DRAFT · 12 min ago · meta_api / pause_ad) + meta (Requested by @user).
 * Body: summary (one bold sentence) + diff (key→value monospace) + AI reasoning.
 * Footer: expiration counter + actions.
 */

import { type ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { Eyebrow } from "../layout/Eyebrow";
import { formatRelativeTime } from "@/lib/utils/format";
import { cn } from "@/lib/utils/cn";

interface DiffRow {
  key: string;
  current?: string | null;
  target?: string | null;
  /** Если true — highlight'ить как изменённое поле (accent border слева). */
  highlight?: boolean;
}

interface DraftCardProps {
  taskType: string;
  createdAt: string | null;
  requestedBy?: string | null;
  summary: ReactNode;
  diff: DiffRow[];
  reason?: string | null;
  /** Когда draft истекает (ISO). По умолчанию через 24h. */
  expiresAt?: string | null;
  /** Доступна ли approve кнопка для current user. */
  canApprove?: boolean;
  /** Подсказка, почему approve disabled (показывается как tooltip). */
  approveDisabledReason?: string;
  onApprove?: () => void;
  onCancel?: () => void;
  busy?: boolean;
}

export function DraftCard({
  taskType,
  createdAt,
  requestedBy,
  summary,
  diff,
  reason,
  expiresAt,
  canApprove = true,
  approveDisabledReason,
  onApprove,
  onCancel,
  busy,
}: DraftCardProps) {
  return (
    <article className="border border-bg-5 bg-bg-1">
      {/* Header */}
      <header className="px-6 pt-5 pb-3">
        <Eyebrow>
          DRAFT
          <span className="text-bg-7 mx-2">·</span>
          <span>{formatRelativeTime(createdAt)}</span>
          <span className="text-bg-7 mx-2">·</span>
          <span>{taskType}</span>
        </Eyebrow>
        {requestedBy ? (
          <div className="mt-2 text-[12px] text-bg-9">
            Requested by{" "}
            <span className="text-bg-11 font-display">@{requestedBy}</span>
          </div>
        ) : null}
      </header>

      <hr className="border-bg-5" />

      {/* Body */}
      <div className="px-6 py-5">
        <div className="font-display text-[14.5px] text-bg-11 font-medium mb-4 tracking-tight">
          {summary}
        </div>

        <dl className="grid grid-cols-[120px_1fr] gap-y-1.5 gap-x-4 font-numeric text-[12.5px]">
          {diff.map((row) => (
            <div
              key={row.key}
              className={cn(
                "contents",
                row.highlight && "[&>dt]:border-l-2 [&>dt]:border-accent [&>dt]:pl-2 [&>dt]:-ml-2.5",
              )}
            >
              <dt className="text-bg-9 tracking-wider">{row.key}</dt>
              <dd className="text-bg-11">
                {row.current != null && row.target != null ? (
                  <>
                    <span className="text-bg-9">{row.current}</span>
                    <span aria-hidden="true" className="text-bg-7 mx-2">
                      →
                    </span>
                    <span className="text-accent">{row.target}</span>
                  </>
                ) : (
                  row.target ?? row.current ?? "—"
                )}
              </dd>
            </div>
          ))}
        </dl>

        {reason ? (
          <div className="mt-5 pt-4 border-t border-bg-5">
            <div className="text-[10px] text-bg-9 uppercase tracking-wider font-display mb-1.5">
              Reasoning
            </div>
            <p className="text-[13px] text-bg-10 leading-relaxed m-0">{reason}</p>
          </div>
        ) : null}
      </div>

      <hr className="border-bg-5" />

      {/* Footer */}
      <footer className="px-6 py-4 flex items-center justify-between">
        <div className="text-[12px] text-bg-9 font-display tracking-wider">
          {expiresAt ? (
            <>
              Expires{" "}
              <span className="text-bg-10">{formatRelativeTime(expiresAt)}</span>
            </>
          ) : (
            "No expiration"
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={onApprove}
            disabled={!canApprove}
            loading={busy}
            title={!canApprove ? approveDisabledReason : undefined}
          >
            Approve &amp; execute
          </Button>
        </div>
      </footer>
    </article>
  );
}
