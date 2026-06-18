/**
 * Checkbox — tri-state: unchecked / checked / indeterminate.
 * Спека: ads.html .checkbox — 16×16px, border 1.5px bg-7, checked=accent.
 * A11y: aria-checked с tri-state поддержкой.
 */
import { type HTMLAttributes } from "react";
import { Check, Minus } from "lucide-react";
import { cn } from "@/lib/utils/cn";

export type CheckboxState = boolean | "indeterminate";

interface CheckboxProps extends Omit<HTMLAttributes<HTMLButtonElement>, "onChange"> {
  checked: CheckboxState;
  onChange?: (next: boolean) => void;
  label?: string;
  disabled?: boolean;
}

export function Checkbox({
  checked,
  onChange,
  label,
  disabled = false,
  className,
  ...rest
}: CheckboxProps) {
  const isIndeterminate = checked === "indeterminate";
  const isChecked = checked === true;

  function handleClick() {
    if (disabled) return;
    // indeterminate → checked; checked → unchecked; unchecked → checked
    const next = isIndeterminate ? true : !isChecked;
    onChange?.(next);
  }

  const button = (
    <button
      type="button"
      role="checkbox"
      aria-checked={isIndeterminate ? "mixed" : isChecked}
      aria-label={label}
      aria-disabled={disabled}
      disabled={disabled}
      onClick={handleClick}
      style={{ width: 16, height: 16 }}
      className={cn(
        "relative inline-flex items-center justify-center shrink-0 rounded-[var(--radius-1)]",
        "border-[1.5px] transition-all duration-[120ms]",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        "disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer",
        isChecked || isIndeterminate
          ? "bg-accent border-accent text-bg-0"
          : "bg-bg-2 border-bg-7 text-transparent",
        className,
      )}
      {...rest}
    >
      {isChecked && (
        <Check
          aria-hidden="true"
          style={{ width: 11, height: 11, strokeWidth: 3 }}
        />
      )}
      {isIndeterminate && (
        // Горизонтальная черта для indeterminate
        <Minus
          aria-hidden="true"
          style={{ width: 9, height: 9, strokeWidth: 2.5 }}
        />
      )}
    </button>
  );

  if (!label) return button;

  return (
    <label className="inline-flex items-center gap-2 cursor-pointer select-none">
      {button}
      <span className="text-[13px] text-bg-11">{label}</span>
    </label>
  );
}
