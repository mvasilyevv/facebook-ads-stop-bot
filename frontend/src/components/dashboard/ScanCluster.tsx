/**
 * ScanCluster — правый кластер page-header Dashboard (управление сканом).
 *
 * Канон design_handoff/web-dashboard.jsx (ScanHeaderControl):
 *   - Observer ON: countdown-ring + «СЛЕД. СКАН» + «ПОСЛЕДНИЙ СКАН Nс назад» +
 *     primary «Сканировать» (на время скана — спиннер + «Сканирую»).
 *   - Observer OFF (paused): dashed pause-ring + «СКАН ВЫКЛЮЧЕН» +
 *     primary «▶ Включить».
 *
 * Данные: observer on/off + last_scan_at + интервал (через useScanCountdown).
 * onScan — реальный POST scan-now; onEnable — включение observer.
 */

import { RefreshCw, Play } from "lucide-react";
import { CountdownRing, PausedRing } from "@/components/data/CountdownRing";
import { Button } from "@/components/ui/Button";
import { useScanCountdown } from "@/lib/hooks/useScanCountdown";

interface ScanClusterProps {
  /** Включён ли observer. */
  scanOn: boolean;
  /** ISO последнего скана (stats.last_scan_at). */
  lastScanAt?: string | null;
  /** Интервал авто-скана в секундах. */
  intervalSeconds?: number;
  /** Реальный запуск скана (POST scan-now). */
  onScan: () => void;
  /** Включить observer (paused → on). */
  onEnable?: () => void;
}

export function ScanCluster({
  scanOn,
  lastScanAt,
  intervalSeconds = 30,
  onScan,
  onEnable,
}: ScanClusterProps) {
  const { scanning, age, next, interval, doScan } = useScanCountdown({
    lastScanAt,
    intervalSeconds,
    enabled: scanOn,
    onScan,
  });

  // ── Paused: observer выключен ───────────────────────────────────────────────
  if (!scanOn) {
    return (
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2.5">
          <PausedRing />
          <span className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-warning">
            СКАН ВЫКЛЮЧЕН
          </span>
        </div>
        <div className="h-7 w-px bg-bg-5" aria-hidden="true" />
        <div className="leading-[1.3]">
          <div className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
            ПОСЛЕДНИЙ СКАН
          </div>
          <div className="whitespace-nowrap font-display text-[13px] tabular-nums text-bg-10">
            {age}с назад <span className="text-bg-7">·</span>{" "}
            <span className="text-warning">стоп</span>
          </div>
        </div>
        <Button
          variant="primary"
          size="md"
          className="ml-1"
          leftIcon={<Play size={14} aria-hidden="true" />}
          onClick={onEnable}
        >
          Включить
        </Button>
      </div>
    );
  }

  // ── Active: observer работает ───────────────────────────────────────────────
  return (
    <div className="flex items-center gap-4">
      <div className="flex items-center gap-2.5">
        <CountdownRing value={scanning ? interval : next} max={interval} active={scanning} />
        <span
          className="font-display text-[9px] font-semibold uppercase tracking-[0.12em]"
          style={{ color: scanning ? "var(--accent)" : "var(--bg-9)" }}
        >
          {scanning ? "ИДЁТ СКАН" : "СЛЕД. СКАН"}
        </span>
      </div>
      <div className="h-7 w-px bg-bg-5" aria-hidden="true" />
      <div className="leading-[1.3]">
        <div className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
          ПОСЛЕДНИЙ СКАН
        </div>
        <div className="whitespace-nowrap font-display text-[13px] tabular-nums text-bg-10">
          {scanning ? "сканирую…" : `${age}с назад`}
        </div>
      </div>
      <Button
        variant="primary"
        size="md"
        className="ml-1"
        disabled={scanning}
        leftIcon={
          <RefreshCw
            size={16}
            strokeWidth={1.8}
            aria-hidden="true"
            style={scanning ? { animation: "fbSpin 1s linear infinite" } : undefined}
          />
        }
        onClick={() => {
          if (!scanning) doScan();
        }}
      >
        {scanning ? "Сканирую" : "Сканировать"}
      </Button>
    </div>
  );
}
