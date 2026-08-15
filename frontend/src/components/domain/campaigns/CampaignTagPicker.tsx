import { toggleCampaignTag } from "@fb/features/campaigns";

import { cn } from "@/lib/utils/cn";

interface CampaignTagPickerProps<T extends string> {
  label: string;
  values: T[];
  options: ReadonlyArray<{ value: T; label: string }>;
  emptyLabel: string;
  onChange: (values: T[]) => void;
}

/** Fixed multi-select rendered as explicit tags, never as a comma-separated input. */
export function CampaignTagPicker<T extends string>({
  label,
  values,
  options,
  emptyLabel,
  onChange,
}: CampaignTagPickerProps<T>) {
  return (
    <fieldset className="min-w-0">
      <legend className="mb-1.5 font-display text-[12px] uppercase tracking-wider text-bg-9">
        {label}
      </legend>
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
                "inline-flex min-h-11 items-center rounded-[var(--radius-2)] border px-3 text-[13px] transition-colors",
                selected
                  ? "border-accent bg-accent-bg text-accent"
                  : "border-[var(--color-hairline-strong)] bg-bg-2 text-bg-10 hover:border-bg-7 hover:text-bg-11",
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
