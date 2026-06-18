/**
 * Tooltip — поверх Radix Tooltip.
 * bg-3 background, radius-1, 12px. Delay 400ms open / 100ms close.
 */
import * as RadixTooltip from "@radix-ui/react-tooltip";
import { type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  delayDuration?: number;
  className?: string;
}

export function Tooltip({
  content,
  children,
  side = "top",
  delayDuration = 400,
  className,
}: TooltipProps) {
  return (
    <RadixTooltip.Provider delayDuration={delayDuration} skipDelayDuration={100}>
      <RadixTooltip.Root>
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            side={side}
            sideOffset={6}
            className={cn(
              "z-[80] bg-bg-3 border border-[var(--hairline)] px-2.5 py-1.5",
              "text-[12px] text-bg-11 font-body",
              "rounded-[var(--radius-2)]",
              "animate-in fade-in-0 zoom-in-95",
              "data-[side=bottom]:slide-in-from-top-1",
              "data-[side=top]:slide-in-from-bottom-1",
              className,
            )}
          >
            {content}
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  );
}

/** Провайдер для страниц с несколькими тултипами. */
export const TooltipProvider = RadixTooltip.Provider;
