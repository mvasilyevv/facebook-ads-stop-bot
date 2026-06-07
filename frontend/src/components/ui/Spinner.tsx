/**
 * Spinner + ProgressBar — индикаторы загрузки.
 *
 * Spinner: ring 2px accent, animate-spin.
 * ProgressBar: 2px горизонтальная полоса accent — используется как top-loader.
 *
 * prefers-reduced-motion: анимация убирается через CSS-токен --dur-base=0ms.
 */
import { type HTMLAttributes } from "react";
import { cn } from "./cn";

// ─── Spinner ───────────────────────────────────────────────────────────────────

interface SpinnerProps extends HTMLAttributes<HTMLSpanElement> {
  /** Размер в px. Default 16. */
  size?: number;
  /** Цвет класс. Default accent. */
  colorClass?: string;
}

export function Spinner({
  size = 16,
  colorClass = "border-accent",
  className,
  ...rest
}: SpinnerProps) {
  return (
    <span
      role="status"
      aria-label="Загрузка"
      style={{ width: size, height: size }}
      className={cn(
        "inline-block rounded-full border-2 border-r-transparent animate-spin",
        colorClass,
        className,
      )}
      {...rest}
    />
  );
}

// ─── ProgressBar ───────────────────────────────────────────────────────────────

interface ProgressBarProps extends HTMLAttributes<HTMLDivElement> {
  /** 0–100. Если undefined — indeterminate (бегающая полоса). */
  value?: number;
  /** Высота в px. Default 2. */
  thickness?: number;
}

export function ProgressBar({
  value,
  thickness = 2,
  className,
  ...rest
}: ProgressBarProps) {
  const indeterminate = value === undefined;

  return (
    <div
      role="progressbar"
      aria-valuenow={indeterminate ? undefined : value}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Прогресс"
      style={{ height: thickness }}
      className={cn("w-full bg-bg-3 overflow-hidden", className)}
      {...rest}
    >
      <div
        style={
          indeterminate
            ? undefined
            : { width: `${Math.min(100, Math.max(0, value ?? 0))}%`, transition: "width 200ms ease" }
        }
        className={cn(
          "h-full bg-accent",
          indeterminate && "animate-[progress-indeterminate_1.5s_ease_infinite]",
        )}
      />
    </div>
  );
}
