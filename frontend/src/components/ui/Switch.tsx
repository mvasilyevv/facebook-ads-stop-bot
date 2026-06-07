/**
 * Switch — toggle (role="switch").
 * Геометрия inline-стилями — детерминировано (канон templates.jsx Field-switch).
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
      style={{
        width: 38,
        height: 22,
        flexShrink: 0,
        borderRadius: 999,
        background: checked ? "var(--accent)" : "var(--bg-5)",
      }}
      className={[
        "relative inline-block align-middle transition-colors duration-[120ms] shrink-0",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        "disabled:opacity-40 disabled:cursor-not-allowed",
      ].join(" ")}
    >
      {/* Ползунок */}
      <span
        aria-hidden="true"
        className="absolute transition-all duration-[120ms]"
        style={{
          width: 18,
          height: 18,
          top: 2,
          left: checked ? 18 : 2,
          borderRadius: 999,
          background: checked ? "var(--bg-0)" : "var(--bg-9)",
        }}
      />
    </button>
  );

  // Без visualLabel — голый тогл
  if (!visualLabel) return toggle;

  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-[13px] text-bg-11 font-medium">{visualLabel}</div>
        {description ? (
          <div className="text-[11px] text-bg-9 mt-0.5">{description}</div>
        ) : null}
      </div>
      {toggle}
    </div>
  );
}
