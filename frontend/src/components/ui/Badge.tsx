/**
 * Badge — presentational status pills.
 * Минимальный служебный текст — 12px; высота сохраняет свободное вертикальное дыхание.
 * Domain states arrive from strict typed operator view-models; Badge never
 * guesses how to normalize an unknown state.
 */
import { type HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils/cn";

const badgeStyles = cva(
  [
    "inline-flex items-center gap-1.5",
    "rounded-full border font-display font-medium uppercase",
    "tracking-[0.08em]",
    "whitespace-nowrap shrink-0",
  ],
  {
    variants: {
      variant: {
        // FSM alert_state
        normal: "bg-bg-2 border-bg-6 text-bg-10",
        warning: "bg-warning-bg border-warning/30 text-warning",
        stop: "bg-danger-bg border-danger/30 text-danger",
        claimed: "bg-info-bg border-info/30 text-info",
        disabled: "bg-bg-2 border-bg-5 text-bg-9",
        // Дополнительные
        success: "bg-success-bg border-success/30 text-success",
        info: "bg-info-bg border-info/30 text-info",
        neutral: "bg-bg-3 border-bg-6 text-bg-10",
        // Task statuses
        pending: "bg-bg-3 border-bg-6 text-bg-9",
        running: "bg-info-bg border-info/30 text-info",
        done: "bg-success-bg border-success/30 text-success",
        failed: "bg-danger-bg border-danger/30 text-danger",
        retrying: "bg-warning-bg border-warning/30 text-warning",
        cancelled: "bg-bg-2 border-bg-5 text-bg-9",
      },
      size: {
        sm: "h-6 px-2 text-[12px]",
        md: "h-7 px-2.5 text-[12px]",
      },
    },
    defaultVariants: {
      variant: "neutral",
      size: "md",
    },
  },
);

const dotStyles = cva("rounded-full shrink-0", {
  variants: {
    variant: {
      normal: "bg-bg-9",
      warning: "bg-warning",
      stop: "bg-danger",
      claimed: "bg-info",
      disabled: "bg-bg-8",
      success: "bg-success",
      info: "bg-info",
      neutral: "bg-bg-8",
      pending: "bg-bg-8",
      running: "bg-info",
      done: "bg-success",
      failed: "bg-danger",
      retrying: "bg-warning",
      cancelled: "bg-bg-8",
    },
  },
  defaultVariants: { variant: "neutral" },
});

export type BadgeVariant = NonNullable<VariantProps<typeof badgeStyles>["variant"]>;

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeStyles> {
  /** Показывать точку-статус (default true). */
  withDot?: boolean;
}

export function Badge({ children, variant, size, withDot = true, className, ...rest }: BadgeProps) {
  return (
    <span className={cn(badgeStyles({ variant, size }), className)} {...rest}>
      {withDot ? (
        // dot 6×6px согласно спеке
        <span aria-hidden="true" className={cn(dotStyles({ variant }), "size-1.5")} />
      ) : null}
      {children}
    </span>
  );
}
