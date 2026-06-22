/**
 * CountdownBar — горизонтальная линия обратного отсчёта до следующего скана.
 *
 * Вместо кольца: тонкая линия с градиентом зелёный→жёлтый→красный (слева направо).
 * Заливка растёт по мере приближения скана (пусто = только что сканировали, полная =
 * скан вот-вот) — чем ближе скан, тем «краснее». Сверху тонким шрифтом — обратный отсчёт
 * и полный интервал текущего цикла (адаптивный: 18/45/90/135с по нагрузке).
 *
 * Градиент привязан к ШИРИНЕ ТРЕКА (background-size), а не к ширине заливки — поэтому
 * заполнение проявляет зелёный край первым, красный последним. transition width 1s —
 * плавный ход (значение тикает раз в секунду в useScanCountdown).
 */

const TRACK_WIDTH_PX = 132;

interface CountdownBarProps {
  /** Секунды до следующего скана (next из useScanCountdown). */
  remaining: number;
  /** Полный интервал текущего цикла (знаменатель). */
  interval: number;
  /** Идёт ли скан прямо сейчас. */
  scanning: boolean;
}

export function CountdownBar({ remaining, interval, scanning }: CountdownBarProps) {
  const safeInterval = Math.max(interval, 1);
  const elapsed = Math.max(0, safeInterval - remaining);
  // Прогресс цикла: 0 = только что сканировали, 1 = скан вот-вот (или идёт).
  const progress = scanning ? 1 : Math.min(1, elapsed / safeInterval);

  return (
    <div className="flex flex-col gap-1.5" style={{ width: TRACK_WIDTH_PX }}>
      <div className="flex items-baseline justify-between leading-none">
        <span
          className="font-display text-[9px] font-semibold uppercase tracking-[0.12em]"
          style={{ color: scanning ? "var(--accent)" : "var(--bg-9)" }}
        >
          {scanning ? "ИДЁТ СКАН" : "СЛЕД. СКАН"}
        </span>
        <span className="font-display tabular-nums" style={{ fontWeight: 300 }}>
          {scanning ? (
            <span className="text-[12px] text-bg-9">сканирую…</span>
          ) : (
            <>
              <span className="text-[13px] text-bg-11">{remaining}</span>
              <span className="text-[11px] text-bg-8">/{safeInterval}с</span>
            </>
          )}
        </span>
      </div>
      <div
        className="relative h-[5px] w-full overflow-hidden rounded-full"
        style={{ background: "var(--bg-5)" }}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={safeInterval}
        aria-valuenow={scanning ? safeInterval : elapsed}
        aria-label="Обратный отсчёт до следующего скана"
      >
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{
            width: `${progress * 100}%`,
            background:
              "linear-gradient(90deg, var(--fsm-normal) 0%, var(--fsm-warning) 55%, var(--fsm-stop) 100%)",
            backgroundSize: `${TRACK_WIDTH_PX}px 100%`,
            backgroundRepeat: "no-repeat",
            transition: "width 1s linear",
            opacity: scanning ? 0.85 : 1,
          }}
        />
      </div>
    </div>
  );
}
