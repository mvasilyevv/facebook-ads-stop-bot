/**
 * Switch — нативный toggle-переключатель (on/off).
 * Тач-цель: label ≥ 44px через padding. Без JS-анимации — CSS transition.
 */
import { useId, type ChangeEventHandler } from "react";
import { cn } from "@/lib/cn";

interface SwitchProps {
  checked: boolean;
  onChange: ChangeEventHandler<HTMLInputElement>;
  label?: string;
  disabled?: boolean;
  id?: string;
}

export function Switch({ checked, onChange, label, disabled, id }: SwitchProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  return (
    <label
      htmlFor={inputId}
      className={cn(
        "flex items-center justify-between gap-3 min-h-[44px] cursor-pointer",
        disabled && "opacity-40 cursor-not-allowed",
      )}
    >
      {label && (
        <span className="text-[14px] text-[var(--color-bg-11)] select-none">{label}</span>
      )}
      <div className="relative">
        <input
          id={inputId}
          type="checkbox"
          role="switch"
          aria-checked={checked}
          checked={checked}
          onChange={onChange}
          disabled={disabled}
          className="sr-only"
        />
        {/* Track */}
        <div
          className={cn(
            "w-11 h-6 transition-colors duration-[var(--dur-base)]",
            checked
              ? "bg-[var(--color-accent)]"
              : "bg-[var(--color-bg-5)]",
          )}
        />
        {/* Thumb */}
        <div
          className={cn(
            "absolute top-1 left-1 w-4 h-4 bg-[#0a0a0b] transition-transform duration-[var(--dur-base)]",
            checked && "translate-x-5",
          )}
        />
      </div>
    </label>
  );
}
