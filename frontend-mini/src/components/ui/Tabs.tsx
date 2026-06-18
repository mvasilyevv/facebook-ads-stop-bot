/**
 * Tabs — горизонтальный переключатель вкладок.
 * Острые углы, нижняя линия на активной.
 * Тач-цель ≥ 44px. Скролл по горизонтали при переполнении.
 */
import { cn } from "@/lib/cn";

export interface TabItem {
  key: string;
  label: string;
}

interface TabsProps {
  items: TabItem[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}

export function Tabs({ items, active, onChange, className }: TabsProps) {
  return (
    <div
      className={cn(
        "flex gap-0 overflow-x-auto scrollbar-none",
        "border-b border-[var(--hairline)]",
        className,
      )}
    >
      {items.map((tab) => {
        const isActive = tab.key === active;
        return (
          <button
            key={tab.key}
            type="button"
            onClick={() => onChange(tab.key)}
            className={cn(
              "shrink-0 min-h-[44px] px-4",
              "text-[13px] font-body font-medium",
              "transition-colors duration-[var(--dur-base)]",
              "border-b-2 -mb-px",
              isActive
                ? "border-[var(--color-accent)] text-[var(--color-bg-11)]"
                : "border-transparent text-[var(--color-bg-9)] hover:text-[var(--color-bg-11)]",
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
