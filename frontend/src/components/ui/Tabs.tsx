/**
 * Tabs — underline (page-level) и segmented/pill (filter).
 * Поверх Radix Tabs. Keyboard-navigation встроен в Radix.
 */
import * as RadixTabs from "@radix-ui/react-tabs";
import { type ReactNode } from "react";
import { cn } from "./cn";

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
          ? "gap-6 border-b border-bg-5 w-full"
          : "gap-1 bg-bg-2 border border-bg-5 p-1",
        className,
      )}
    >
      {items.map((it) => (
        <RadixTabs.Trigger
          key={it.value}
          value={it.value}
          disabled={it.disabled}
          className={cn(
            "inline-flex items-center gap-2 font-display transition-colors duration-[120ms]",
            "disabled:opacity-40 disabled:cursor-not-allowed",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
            variant === "underline"
              ? [
                  "text-[13px] uppercase tracking-wider pb-3 -mb-px border-b-2 border-transparent",
                  "text-bg-9 hover:text-bg-11",
                  "data-[state=active]:border-accent data-[state=active]:text-bg-11",
                ]
              : [
                  "h-7 px-3 text-[11.5px] uppercase tracking-wider",
                  "text-bg-9 hover:text-bg-11",
                  "data-[state=active]:bg-bg-4 data-[state=active]:text-accent",
                ],
          )}
        >
          {it.label}
          {it.count != null ? (
            <span className="text-[10px] font-display tabular-nums border border-bg-6 px-1 leading-tight">
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
