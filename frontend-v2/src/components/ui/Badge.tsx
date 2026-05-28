/**
 * Badge — FSM-state pills + severity-цвета.
 * Default: pill (radius-full), 8px dot + uppercase text.
 */

import { type HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils/cn";

const badgeStyles = cva(
  [
    "inline-flex items-center gap-1.5",
    "rounded-full border font-display font-medium uppercase",
    "tracking-[0.08em]",
  ],
  {
    variants: {
      variant: {
        normal: "bg-bg-2 border-bg-6 text-bg-10",
        warning: "bg-warning-bg border-[rgba(212,168,88,0.3)] text-warning",
        stop: "bg-danger-bg border-[rgba(199,98,92,0.3)] text-danger",
        claimed: "bg-info-bg border-[rgba(122,160,180,0.3)] text-info",
        disabled: "bg-bg-2 border-bg-5 text-bg-8",
        success: "bg-success-bg border-[rgba(126,180,122,0.3)] text-success",
        info: "bg-info-bg border-[rgba(122,160,180,0.3)] text-info",
        neutral: "bg-bg-3 border-bg-6 text-bg-10",
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

const dotStyles = cva("size-1.5 rounded-full shrink-0", {
  variants: {
    variant: {
      normal: "bg-bg-9",
      warning: "bg-warning",
      stop: "bg-danger",
      claimed: "bg-info",
      disabled: "bg-bg-7",
      success: "bg-success",
      info: "bg-info",
      neutral: "bg-bg-7",
    },
  },
  defaultVariants: {
    variant: "neutral",
  },
});

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeStyles> {
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
      {withDot ? <span aria-hidden="true" className={dotStyles({ variant })} /> : null}
      {children}
    </span>
  );
}

/** Соответствие FSM alert_state → Badge variant. */
export function alertStateToBadge(
  state: string,
): "normal" | "warning" | "stop" | "claimed" | "disabled" {
  switch (state) {
    case "warning_sent":
      return "warning";
    case "stop_sent":
      return "stop";
    case "claimed":
      return "claimed";
    case "disabled":
      return "disabled";
    default:
      return "normal";
  }
}
