/**
 * ScanModeBar — индикатор текущего режима адаптивного скана (НЕ обратный отсчёт).
 *
 * Лёгкая горизонтальная шкала с градиентом зелёный→жёлтый→красный (спокойно → критично).
 * Маркер скользит по шкале в позицию текущего режима observer'а:
 *   IDLE «Простой» (нечего сканировать) → CALM «Спокойно» → ELEVATED «Повышенный» →
 *   CRITICAL «Критично» (есть стоп-хиты, сканируем максимально часто).
 *
 * Режим приходит из observer:runtime.scan_mode (бэк считает его по итогу цикла:
 * stop-хиты → CRITICAL, warning → ELEVATED, есть офферные ads → CALM, пусто → IDLE).
 * Обратный отсчёт до следующего скана живёт ОТДЕЛЬНО (см. ScanCluster) — эта линия его
 * не показывает, она про «где мы по нагрузке прямо сейчас».
 */

const TRACK_WIDTH_PX = 132;

/** Метаданные режимов: подпись, позиция на шкале [0..1] и цвет маркера. */
const MODE_INFO: Record<string, { label: string; pos: number; color: string }> = {
  IDLE: { label: "Простой", pos: 0.0, color: "var(--bg-8)" },
  CALM: { label: "Спокойно", pos: 0.3, color: "var(--fsm-normal)" },
  ELEVATED: { label: "Повышенный", pos: 0.64, color: "var(--fsm-warning)" },
  CRITICAL: { label: "Критично", pos: 1.0, color: "var(--fsm-stop)" },
};

interface ScanModeBarProps {
  /** Режим из observer:runtime.scan_mode (CRITICAL|ELEVATED|CALM|IDLE). null — пока неизвестен. */
  mode: string | null;
}

export function ScanModeBar({ mode }: ScanModeBarProps) {
  const info = mode ? MODE_INFO[mode] : undefined;
  const pos = info?.pos ?? 0.3;
  const color = info?.color ?? "var(--bg-8)";
  const label = info?.label ?? "—";

  return (
    <div className="flex flex-col gap-1.5" style={{ width: TRACK_WIDTH_PX }}>
      <div className="flex items-baseline justify-between leading-none">
        <span className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
          РЕЖИМ
        </span>
        <span
          className="font-display text-[11px] font-semibold uppercase tracking-[0.04em]"
          style={{ color }}
        >
          {label}
        </span>
      </div>
      {/* Шкала: полный лёгкий градиент всегда виден; маркер указывает позицию режима. */}
      <div
        className="relative h-[5px] w-full rounded-full"
        style={{
          background:
            "linear-gradient(90deg, var(--fsm-normal) 0%, var(--fsm-warning) 55%, var(--fsm-stop) 100%)",
          opacity: 0.32,
        }}
        role="meter"
        aria-valuemin={0}
        aria-valuemax={3}
        aria-valuenow={Math.round(pos * 3)}
        aria-label={`Режим скана: ${label}`}
      >
        {/* Маркер-игла в позиции режима (скользит при смене режима). */}
        <div
          className="absolute top-1/2"
          style={{
            left: `${pos * 100}%`,
            transform: "translate(-50%, -50%)",
            transition: "left 600ms cubic-bezier(0.22, 1, 0.36, 1)",
          }}
        >
          <div
            className="rounded-full"
            style={{
              width: 4,
              height: 11,
              background: color,
              boxShadow: `0 0 0 2px var(--bg-0), 0 0 8px ${color}`,
            }}
          />
        </div>
      </div>
    </div>
  );
}
