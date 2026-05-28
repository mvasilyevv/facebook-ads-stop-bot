/**
 * Tooltip — поверх Radix.
 * bg-3 background, radius-1, text-caption. Delay 400ms open / 100ms close.
 */

import * as RadixTooltip from "@radix-ui/react-tooltip";
import { type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  delayDuration?: number;
}

export function Tooltip({ content, children, side = "top", delayDuration = 400 }: TooltipProps) {
  return (
    <RadixTooltip.Provider delayDuration={delayDuration} skipDelayDuration={100}>
      <RadixTooltip.Root>
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            side={side}
            sideOffset={6}
            className={cn(
              "z-[80] bg-bg-3 border border-bg-6 px-2.5 py-1.5",
              "text-[12px] text-bg-11 font-body",
              "rounded-[2px]",
            )}
          >
            {content}
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  );
}

export const TooltipProvider = RadixTooltip.Provider;
