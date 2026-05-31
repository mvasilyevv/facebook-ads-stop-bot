/**
 * Switch — переиспользуемый toggle (role="switch").
 * Геометрия задаётся инлайн-стилями (не arbitrary Tailwind-классами) — детерминированно,
 * не зависит от spacing-шкалы конфигурации. Цвета — через дизайн-токены.
 */

interface SwitchProps {
  checked: boolean;
  onChange: () => void;
  /** aria-label — обязателен для доступности. */
  label: string;
  disabled?: boolean;
  /** Видимая подпись слева от тогла. Если задана — Switch сам рисует строку с подписью. */
  visualLabel?: string;
  /** Описание-подсказка под видимой подписью (последствия переключения). */
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
      style={{ width: 44, height: 24 }}
      className={[
        "relative inline-block border align-middle transition-colors shrink-0",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        checked ? "bg-success border-[rgba(126,180,122,0.5)]" : "bg-bg-3 border-bg-6",
        "disabled:opacity-40 disabled:cursor-not-allowed",
      ].join(" ")}
    >
      <span
        aria-hidden="true"
        className="absolute bg-bg-11 transition-all"
        style={{ width: 16, height: 16, top: 3, left: checked ? 24 : 4 }}
      />
    </button>
  );

  // Без visualLabel — обратная совместимость: голый тогл (подпись рисует родитель).
  if (!visualLabel) return toggle;

  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-[13px] text-bg-11 font-medium">{visualLabel}</div>
        {description ? <div className="text-[11px] text-bg-9 mt-0.5">{description}</div> : null}
      </div>
      {toggle}
    </div>
  );
}
