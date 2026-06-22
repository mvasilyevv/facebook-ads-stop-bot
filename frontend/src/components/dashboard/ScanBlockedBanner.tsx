/**
 * ScanBlockedBanner — full-width warning-баннер под page-header, когда скан ВКЛЮЧЁН,
 * но фактически ничего не отслеживает (money-критичное «тихое» состояние).
 *
 * Главный кейс: пустой список кампаний (allowlist) при одном кабинете — observer коротит
 * скан, авто-стоп не работает, но всё выглядит «зелёным» (observer running, heartbeats ОК).
 * Причину (текст) даёт бэкенд (DashboardStatsOut.scan_blocked_reason) — совпадает с реальной
 * логикой observer (core.observer.accounts.allowlist_blocks_scan). CTA ведёт на «Кампании».
 */

import { ListChecks, ArrowRight } from "lucide-react";

interface ScanBlockedBannerProps {
  /** Человекочитаемая причина блокировки от бэкенда. */
  reason: string;
  /** Переход на страницу заполнения списка кампаний. */
  onNavigate?: () => void;
}

export function ScanBlockedBanner({ reason, onNavigate }: ScanBlockedBannerProps) {
  return (
    <div
      role="alert"
      className="flex items-center gap-3 rounded-[var(--radius-3)] border border-l-2 border-[color-mix(in_srgb,var(--warning)_34%,transparent)] border-l-warning bg-warning-bg px-4 py-3"
    >
      <span className="flex shrink-0 text-warning" aria-hidden="true">
        <ListChecks size={18} strokeWidth={2} />
      </span>
      <div className="flex-1 text-[13px] leading-[1.45] text-bg-11">
        <b className="text-warning">Скан включён, но ничего не отслеживает</b> — {reason}
      </div>
      {onNavigate ? (
        <button
          type="button"
          onClick={onNavigate}
          className="inline-flex shrink-0 items-center gap-2 rounded-[var(--radius-2)] bg-warning px-4 py-2 font-display text-[13px] font-semibold text-bg-0 transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Открыть «Кампании»
          <ArrowRight size={14} aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}
