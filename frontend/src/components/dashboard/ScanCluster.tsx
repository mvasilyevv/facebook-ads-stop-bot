/**
 * ScanCluster — правый кластер page-header Dashboard (управление сканом).
 *
 * Канон design_handoff/web-dashboard.jsx (ScanHeaderControl):
 *   - Observer ON: индикатор РЕЖИМА (ScanModeBar — лёгкий градиент зелёный→красный, маркер
 *     в позиции CRITICAL/ELEVATED/CALM/IDLE) + ОТДЕЛЬНЫЙ блок обратного отсчёта «СЛЕД. СКАН Nс»
 *     + «ПОСЛЕДНИЙ СКАН Nс назад» + primary «Сканировать» (на время скана — спиннер + «Сканирую»).
 *   - Observer OFF (paused): dashed pause-ring + «СКАН ВЫКЛЮЧЕН»; единый CTA находится
 *     в полноширинном PausedBanner, чтобы не дублировать действие.
 *
 * Режим (scanMode) и обратный отсчёт — РАЗНЫЕ сущности: линия показывает нагрузку, число —
 * сколько секунд до скана. Данные: observer on/off + last_scan_at + next_scan_at + scan_mode
 * (через useScanCountdown + observer:runtime). onScan — реальный POST scan-now; onEnable — включение.
 */

import { useEffect, useState } from "react";
import { CircleHelp, RefreshCw, Power, ServerOff } from "lucide-react";
import { formatRelativeTime } from "@fb/shared";
import { PausedRing } from "@/components/data/CountdownRing";
import { ScanModeBar } from "@/components/data/ScanModeBar";
import { Button } from "@/components/ui/Button";
import { useScanCountdown } from "@/lib/hooks/useScanCountdown";
import { cn } from "@/lib/utils/cn";
import type { MonitoringState } from "./monitoringState";

/** Мульти-кабинет: прогресс обхода кабинетов в текущем цикле (observer:runtime). */
export interface ScanProgress {
  /** Кабинет, сканируемый прямо сейчас (числовой ID без act_). */
  current?: string | null;
  /** Сколько кабинетов уже отсканировано в этом цикле. */
  done?: number | null;
  /** Всего кабинетов в scan set. */
  total?: number | null;
}

interface ScanClusterProps {
  /** Включён ли observer. */
  scanOn: boolean;
  /** Подтверждённое состояние контура — не показываем live-controls при offline/unknown. */
  monitoringState: MonitoringState;
  /** ISO последнего скана (stats.last_scan_at). */
  lastScanAt?: string | null;
  /** ISO следующего скана (observer:runtime.next_scan_at) — реальный адаптивный отсчёт. */
  nextScanAt?: string | null;
  /** Режим адаптивного скана (observer:runtime.scan_mode): CRITICAL|ELEVATED|CALM|IDLE. */
  scanMode?: string | null;
  /** Интервал авто-скана в секундах. */
  intervalSeconds?: number;
  /** Мульти-кабинет: прогресс цикла (показывается только при total > 1). */
  scanProgress?: ScanProgress | null;
  /** Реальный запуск скана (POST scan-now). */
  onScan: () => void;
  /** Выключить observer (on → paused). */
  onDisable?: () => void;
}

export function ScanCluster({
  scanOn,
  monitoringState,
  lastScanAt,
  nextScanAt,
  scanMode,
  intervalSeconds = 30,
  scanProgress,
  onScan,
  onDisable,
}: ScanClusterProps) {
  const { scanning, next, interval, doScan } = useScanCountdown({
    lastScanAt,
    nextScanAt,
    intervalSeconds,
    enabled: scanOn,
    onScan,
  });

  // Sticky-режим: во время скана бэк временно не пишет scan_mode (известен только по итогу
  // цикла) — держим последний известный, чтобы линия не моргала в «—».
  const [stickyMode, setStickyMode] = useState<string | null>(scanMode ?? null);
  useEffect(() => {
    if (scanMode) setStickyMode(scanMode);
  }, [scanMode]);
  const mode = scanMode ?? stickyMode;

  if (scanOn && (monitoringState === "offline" || monitoringState === "unknown")) {
    const unavailable = monitoringState === "offline";
    const StatusIcon = unavailable ? ServerOff : CircleHelp;
    return (
      <div className="flex flex-wrap items-center justify-end gap-3 sm:gap-4">
        <div className="flex items-center gap-2.5">
          <span
            className={cn(
              "flex size-8 items-center justify-center rounded-[var(--radius-2)]",
              unavailable ? "bg-danger-bg text-danger" : "bg-accent-bg text-accent",
            )}
          >
            <StatusIcon size={16} strokeWidth={1.7} aria-hidden="true" />
          </span>
          <span
            className={cn(
              "font-display text-[9px] font-semibold uppercase tracking-[0.12em]",
              unavailable ? "text-danger" : "text-accent",
            )}
          >
            {unavailable ? "СКАН НЕДОСТУПЕН" : "ОЖИДАЕМ ПЕРВЫЙ СКАН"}
          </span>
        </div>
        <div className="hidden h-7 w-px bg-[var(--hairline-strong)] sm:block" aria-hidden="true" />
        <div className="leading-[1.3]">
          <div className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
            ПОСЛЕДНИЙ СКАН
          </div>
          <div className="whitespace-nowrap font-display text-[13px] tabular-nums text-bg-10">
            {scanAgo(lastScanAt)}
          </div>
        </div>
      </div>
    );
  }

  // ── Paused: observer выключен ───────────────────────────────────────────────
  if (!scanOn) {
    return (
      <div className="flex flex-wrap items-center justify-end gap-3 sm:gap-4">
        <div className="flex items-center gap-2.5">
          <PausedRing />
          <span className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-warning">
            СКАН ВЫКЛЮЧЕН
          </span>
        </div>
        <div className="hidden h-7 w-px bg-[var(--hairline-strong)] sm:block" aria-hidden="true" />
        <div className="leading-[1.3]">
          <div className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
            ПОСЛЕДНИЙ СКАН
          </div>
          <div className="whitespace-nowrap font-display text-[13px] tabular-nums text-bg-10">
            {scanAgo(lastScanAt)} <span className="text-bg-8">·</span>{" "}
            <span className="text-warning">стоп</span>
          </div>
        </div>
      </div>
    );
  }

  // ── Active: observer работает ───────────────────────────────────────────────
  return (
    <div className="flex flex-wrap items-center justify-end gap-3 sm:gap-4">
      {/* Индикатор режима — лёгкий градиент + маркер (НЕ отсчёт). */}
      <ScanModeBar mode={mode} />
      <div className="hidden h-7 w-px bg-[var(--hairline-strong)] sm:block" aria-hidden="true" />
      {/* Обратный отсчёт — отдельная сущность: сколько секунд до следующего скана. */}
      <div className="leading-[1.3]">
        <div className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
          СЛЕД. СКАН
        </div>
        {scanning ? (
          <div className="whitespace-nowrap font-display text-[13px] text-accent">сканирую…</div>
        ) : (
          <div className="whitespace-nowrap font-display tabular-nums">
            <span className="text-[16px] text-bg-11" style={{ fontWeight: 300 }}>
              {next}
            </span>
            <span className="text-[11px] text-bg-8" style={{ fontWeight: 300 }}>
              /{interval}с
            </span>
          </div>
        )}
      </div>
      <div className="hidden h-7 w-px bg-[var(--hairline-strong)] sm:block" aria-hidden="true" />
      <div className="leading-[1.3]">
        <div className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
          ПОСЛЕДНИЙ СКАН
        </div>
        <div className="whitespace-nowrap font-display text-[13px] tabular-nums text-bg-10">
          {scanning ? formatScanningLabel(scanProgress) : scanAgo(lastScanAt)}
        </div>
      </div>
      {/* Мульти-кабинет: текущий кабинет цикла (только когда кабинетов > 1) */}
      {scanning && (scanProgress?.total ?? 0) > 1 && scanProgress?.current ? (
        <div className="leading-[1.3]">
          <div className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-bg-9">
            КАБИНЕТ
          </div>
          <div
            className="whitespace-nowrap font-display text-[13px] tabular-nums text-bg-10"
            title={`Кабинет ${scanProgress.current}`}
          >
            …{scanProgress.current.slice(-4)}
          </div>
        </div>
      ) : null}
      {onDisable && (
        <Button
          variant="secondary"
          size="md"
          className="ml-1"
          leftIcon={<Power size={14} aria-hidden="true" />}
          onClick={onDisable}
        >
          Выключить
        </Button>
      )}
      <Button
        variant="primary"
        size="md"
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

/**
 * Возраст последнего скана крупными единицами (мин/ч/дн), не до секунды:
 * «только что» / «39 мин назад» / «5 ч назад» / «3 дн назад». «—» если нет метки.
 */
function scanAgo(iso: string | null | undefined): string {
  const rel = formatRelativeTime(iso); // "сейчас" | "5 мин" | "2 ч" | "3 дн" | "—"
  if (rel === "—") return "—";
  if (rel === "сейчас") return "только что";
  return `${rel} назад`;
}

/** «сканирую…» или «кабинет 2/3» — когда в цикле несколько кабинетов. */
function formatScanningLabel(p: ScanProgress | null | undefined): string {
  const total = p?.total ?? 0;
  if (total > 1) {
    // accounts_done = сколько уже завершено → текущий = done + 1 (cap по total).
    const current = Math.min((p?.done ?? 0) + 1, total);
    return `кабинет ${current}/${total}`;
  }
  return "сканирую…";
}
