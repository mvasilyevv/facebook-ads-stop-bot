/**
 * Tabs — underline (page-level) и segmented/pill (filter).
 * Поверх Radix Tabs. Keyboard-navigation встроен в Radix.
 */
import * as RadixTabs from "@radix-ui/react-tabs";
import { type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

export interface TabItem {
  value: string;
  label: ReactNode;
  /** Счётчик, отображается рядом с лейблом. */
  count?: number;
  disabled?: boolean;
}

interface TabsProps {
  value: string;
  onValueChange: (value: string) => void;
  variant?: "underline" | "segmented";
  className?: string;
  children: ReactNode;
}

export function Tabs({
  value,
  onValueChange,
  variant = "underline",
  className,
  children,
}: TabsProps) {
  return (
    <RadixTabs.Root
      value={value}
      onValueChange={onValueChange}
      data-variant={variant}
      className={className}
    >
      {children}
    </RadixTabs.Root>
  );
}

export function TabsList({
  items,
  variant = "underline",
  className,
}: {
  items: TabItem[];
  variant?: "underline" | "segmented";
  className?: string;
}) {
  return (
    <RadixTabs.List
      className={cn(
        "inline-flex items-center",
        variant === "underline"
          ? "gap-1 border-b border-[var(--color-hairline)] w-full"
          : "gap-1 bg-bg-2 border border-[var(--color-hairline)] p-1 rounded-[var(--radius-2)]",
        className,
      )}
    >
      {items.map((it) => (
        <RadixTabs.Trigger
          key={it.value}
          value={it.value}
          disabled={it.disabled}
          className={cn(
            "inline-flex min-h-11 items-center gap-2 font-display transition-colors duration-[120ms]",
            "disabled:opacity-40 disabled:cursor-not-allowed",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
            variant === "underline"
              ? [
                  "text-[13px] px-4 py-2.5 -mb-px border-b-2 border-transparent font-normal",
                  "text-bg-9 hover:text-bg-11",
                  "data-[state=active]:border-accent data-[state=active]:text-bg-11 data-[state=active]:font-semibold",
                ]
              : [
                  "h-11 px-3 text-[12px] uppercase tracking-wider rounded-[var(--radius-1)]",
                  "text-bg-9 hover:text-bg-11",
                  "data-[state=active]:bg-bg-4 data-[state=active]:text-accent",
                ],
          )}
        >
          {it.label}
          {it.count != null ? (
            <span className="text-[12px] font-display tabular-nums border border-[var(--color-hairline)] px-1 leading-tight rounded-[var(--radius-1)]">
              {it.count}
            </span>
          ) : null}
        </RadixTabs.Trigger>
      ))}
    </RadixTabs.List>
  );
}

/** Контент таба. Обёртка над Radix TabsContent. */
export const TabsContent = RadixTabs.Content;
