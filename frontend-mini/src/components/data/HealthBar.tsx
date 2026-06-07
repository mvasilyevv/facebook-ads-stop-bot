/**
 * HealthBar — сегментированный бар долей портфеля Норма/Предупреждение/Стоп.
 * Ширина сегментов анимируется от 0 на mount. compact → тоньше бар + плотная
 * легенда (для hero mini). Порт из web + проп compact.
 */
import { useEffect, useState } from "react";

interface HealthBarProps {
  /** Кол-во в Норме. */
  normal: number;
  /** Кол-во в Предупреждении. */
  warning: number;
  /** Кол-во в Стопе. */
  stop: number;
  /** Компактный режим (hero mini): тоньше бар, плотная легенда. */
  compact?: boolean;
}

interface Seg {
  key: string;
  n: number;
  color: string;
}

export function HealthBar({ normal, warning, stop, compact = false }: HealthBarProps) {
  const total = normal + warning + stop;
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const r = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(r);
  }, []);

  const segs: Seg[] = [
    { key: "Норма", n: normal, color: "var(--bg-7)" },
    { key: "Предупреждение", n: warning, color: "var(--warning)" },
    { key: "Стоп", n: stop, color: "var(--danger)" },
  ];

  return (
    <div>
      <div
        className={`flex overflow-hidden border border-bg-6 bg-bg-2 ${compact ? "h-1.5" : "h-2"}`}
        role="img"
        aria-label={`Норма ${normal}, Предупреждение ${warning}, Стоп ${stop}`}
      >
        {segs.map((s) => (
          <div
            key={s.key}
            className="border-r border-bg-0 last:border-r-0"
            style={{
              width: mounted && total > 0 ? `${(s.n / total) * 100}%` : "0%",
              background: s.color,
              transition: "width 800ms var(--ease-out)",
            }}
          />
        ))}
      </div>
      <div className={`flex flex-wrap ${compact ? "mt-2 gap-3" : "mt-2.5 gap-[18px]"}`}>
        {segs.map((s) => (
          <span
            key={s.key}
            className={`inline-flex items-center gap-[7px] text-bg-9 ${compact ? "text-[11px]" : "text-[12px]"}`}
          >
            <span
              aria-hidden="true"
              className="inline-block size-[7px] rounded-full"
              style={{ background: s.color }}
            />
            {s.key} <b className="ml-0.5 font-display tabular-nums text-bg-11">{s.n}</b>
          </span>
        ))}
      </div>
    </div>
  );
}
