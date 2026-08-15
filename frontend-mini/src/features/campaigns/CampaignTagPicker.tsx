import { toggleCampaignTag } from "@fb/features/campaigns";

import { cn } from "@/lib/cn";

export function CampaignTagPicker<T extends string>({
  label,
  values,
  options,
  emptyLabel,
  onChange,
}: {
  label: string;
  values: T[];
  options: ReadonlyArray<{ value: T; label: string }>;
  emptyLabel: string;
  onChange: (values: T[]) => void;
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-[12px] uppercase tracking-[0.08em] text-bg-9">{label}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const selected = values.includes(option.value);
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(toggleCampaignTag(values, option.value))}
              className={cn(
                "min-h-11 rounded-[var(--radius-2)] border px-3 text-[13px]",
                selected
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-[var(--color-hairline-strong)] bg-bg-2 text-bg-10",
              )}
            >
              {option.label}
            </button>
          );
        })}
      </div>
      {values.length === 0 ? <p className="mt-2 text-[12px] text-bg-8">{emptyLabel}</p> : null}
    </fieldset>
  );
}
