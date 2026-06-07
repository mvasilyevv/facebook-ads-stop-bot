/**
 * Badge — FSM-state pills.
 * Спека: .badge height 22px, padding 0 10px, dot 6px, border-radius 9999px.
 * Использовать с alertStateToBadgeVariant / taskStatusToBadgeVariant из @fb/shared.
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
        normal:   "bg-bg-2 border-bg-6 text-bg-10",
        warning:  "bg-warning-bg border-[rgba(212,168,88,0.3)] text-warning",
        stop:     "bg-danger-bg border-[rgba(199,98,92,0.3)] text-danger",
        claimed:  "bg-info-bg border-[rgba(122,160,180,0.3)] text-info",
        disabled: "bg-bg-2 border-bg-5 text-bg-8",
        // Дополнительные
        success:  "bg-success-bg border-[rgba(126,180,122,0.3)] text-success",
        info:     "bg-info-bg border-[rgba(122,160,180,0.3)] text-info",
        neutral:  "bg-bg-3 border-bg-6 text-bg-10",
        // Task statuses
        pending:   "bg-bg-3 border-bg-6 text-bg-9",
        running:   "bg-info-bg border-[rgba(122,160,180,0.3)] text-info",
        done:      "bg-success-bg border-[rgba(126,180,122,0.3)] text-success",
        failed:    "bg-danger-bg border-[rgba(199,98,92,0.3)] text-danger",
        retrying:  "bg-warning-bg border-[rgba(212,168,88,0.3)] text-warning",
        cancelled: "bg-bg-2 border-bg-5 text-bg-8",
        draft:     "bg-accent-bg border-[rgba(245,241,232,0.2)] text-accent",
      },
      size: {
        sm: "h-[18px] px-2 text-[9px]",
        md: "h-[22px] px-2.5 text-[10px]",
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
      normal:    "bg-bg-9",
      warning:   "bg-warning",
      stop:      "bg-danger",
      claimed:   "bg-info",
      disabled:  "bg-bg-7",
      success:   "bg-success",
      info:      "bg-info",
      neutral:   "bg-bg-7",
      pending:   "bg-bg-8",
      running:   "bg-info",
      done:      "bg-success",
      failed:    "bg-danger",
      retrying:  "bg-warning",
      cancelled: "bg-bg-7",
      draft:     "bg-accent",
    },
  },
  defaultVariants: { variant: "neutral" },
});

export type BadgeVariant = NonNullable<VariantProps<typeof badgeStyles>["variant"]>;

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeStyles> {
  /** Показывать точку-статус (default true). */
  withDot?: boolean;
}

export function Badge({
  children,
  variant,
  size,
  withDot = true,
  className,
  ...rest
}: BadgeProps) {
  return (
    <span className={cn(badgeStyles({ variant, size }), className)} {...rest}>
      {withDot ? (
        // dot 6×6px согласно спеке
        <span
          aria-hidden="true"
          className={cn(dotStyles({ variant }), "size-1.5")}
        />
      ) : null}
      {children}
    </span>
  );
}
