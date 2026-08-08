/**
 * Switch — toggle (role="switch").
 * Геометрия задаётся inline-стилями для детерминированного layout.
 * Цвета: checked=accent (warm off-white), unchecked=bg-5. Ползунок: checked=bg-0, off=bg-9.
 */

interface SwitchProps {
  checked: boolean;
  onChange: () => void;
  /** aria-label обязателен для доступности. */
  label: string;
  disabled?: boolean;
  /** Видимая подпись слева от тогла. */
  visualLabel?: string;
  /** Описание под visualLabel. */
  description?: string;
}

export function Switch({
  checked,
  onChange,
  label,
  disabled = false,
  visualLabel,
  description,
}: SwitchProps) {
  const toggle = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={onChange}
      disabled={disabled}
      className={[
        "relative inline-flex size-11 shrink-0 items-center justify-center rounded-full align-middle",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        "disabled:opacity-40 disabled:cursor-not-allowed",
      ].join(" ")}
    >
      <span
        aria-hidden="true"
        className="relative block h-[22px] w-[38px] rounded-full transition-colors duration-[120ms]"
        style={{
          background: checked ? "var(--color-accent)" : "var(--color-bg-5)",
        }}
      >
        <span
          className="absolute top-0.5 size-[18px] rounded-full transition-[left] duration-[120ms]"
          style={{
            left: checked ? 18 : 2,
            background: checked ? "var(--color-bg-0)" : "var(--color-bg-9)",
          }}
        />
      </span>
    </button>
  );

  // Без visualLabel — голый тогл
  if (!visualLabel) return toggle;

  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-[13px] text-bg-11 font-medium">{visualLabel}</div>
        {description ? <div className="text-[12px] text-bg-9 mt-0.5">{description}</div> : null}
      </div>
      {toggle}
    </div>
  );
}
