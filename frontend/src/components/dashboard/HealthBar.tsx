/**
 * HealthBar — сегментированный 8px-бар долей портфеля Норма/Предупреждение/Стоп.
 *
 * Портировано из design_handoff/dashboard-shared.jsx (HealthBar). Ширина каждого
 * сегмента анимируется от 0 на mount (800ms ease-out). Под баром — легенда с
 * абсолютными счётчиками. При полностью пустом портфеле бар нейтрально-пустой.
 */

import { useEffect, useState } from "react";

interface HealthBarProps {
  /** Кол-во объявлений в Норме. */
  normal: number;
  /** Кол-во в Предупреждении. */
  warning: number;
  /** Кол-во в Стопе. */
  stop: number;
}

interface Seg {
  key: string;
  n: number;
  color: string;
}

export function HealthBar({ normal, warning, stop }: HealthBarProps) {
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
        className="flex h-2 overflow-hidden border border-bg-6 bg-bg-2"
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
      <div className="mt-2.5 flex flex-wrap gap-[18px]">
        {segs.map((s) => (
          <span
            key={s.key}
            className="inline-flex items-center gap-[7px] text-[12px] text-bg-9"
          >
            <span
              aria-hidden="true"
              className="inline-block size-[7px] rounded-full"
              style={{ background: s.color }}
            />
            {s.key}{" "}
            <b className="ml-0.5 font-display tabular-nums text-bg-11">{s.n}</b>
          </span>
        ))}
      </div>
    </div>
  );
}
