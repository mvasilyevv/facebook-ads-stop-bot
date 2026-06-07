/**
 * PausedBanner — full-width warning-баннер под page-header, когда Observer выключен.
 *
 * Канон design_handoff/dashboard-shared.jsx (PausedBanner): иконка паузы +
 * «Observer выключен — объявления не мониторятся с HH:MM…» + кнопка «Включить».
 *
 * `since` — время выключения (HH:MM UTC); если неизвестно — не показываем метку.
 */

import { Pause, Play } from "lucide-react";

interface PausedBannerProps {
  /** Время выключения (HH:MM) — если есть. */
  since?: string | null;
  onEnable?: () => void;
}

export function PausedBanner({ since, onEnable }: PausedBannerProps) {
  return (
    <div
      role="alert"
      className="flex items-center gap-3 border border-l-2 border-[color-mix(in_srgb,var(--warning)_34%,transparent)] border-l-warning bg-warning-bg px-4 py-3"
    >
      <span className="flex shrink-0 text-warning" aria-hidden="true">
        <Pause size={18} strokeWidth={2} />
      </span>
      <div className="flex-1 text-[13px] leading-[1.45] text-bg-11">
        <b className="text-warning">Observer выключен</b> — объявления не
        мониторятся
        {since ? (
          <>
            {" "}
            с <span className="font-display tabular-nums">{since}</span>
          </>
        ) : null}
        . Алерты, авто-disable и live-tail на паузе.
      </div>
      {onEnable ? (
        <button
          type="button"
          onClick={onEnable}
          className="inline-flex shrink-0 items-center gap-2 bg-warning px-4 py-2 font-display text-[13px] font-semibold text-bg-0 transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <Play size={14} aria-hidden="true" />
          Включить
        </button>
      ) : null}
    </div>
  );
}
