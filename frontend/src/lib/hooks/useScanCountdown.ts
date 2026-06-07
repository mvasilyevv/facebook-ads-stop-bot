/**
 * useScanCountdown — состояние scan-кластера на Dashboard (countdown + age + scanning).
 *
 * Портировано из design_handoff/dashboard-shared.jsx (useScan), адаптировано под
 * реальные данные: возраст последнего скана считается от `lastScanAt` (ISO из API),
 * а не от mock-счётчика. Обратный отсчёт идёт к следующему авто-скану (interval).
 *
 * Контракт:
 *   - `age` — секунды с последнего скана (из lastScanAt; при scanning → "сканирую").
 *   - `next` — секунды до следующего авто-скана (interval - age, clamp 0..interval).
 *   - `scanning` — идёт ли скан прямо сейчас (на время вызова onScan).
 *   - `doScan()` — ручной запуск: ставит scanning, дёргает onScan, держит ~1.4s.
 *   - на `next===0` (enabled) — авто-запуск doScan (как в прототипе).
 *
 * Возраст обновляется раз в секунду; пересчитывается от lastScanAt, поэтому
 * переживает рефетчи и не «убегает» при обновлении данных с сервера.
 */

import { useCallback, useEffect, useRef, useState } from "react";

/** Сколько визуально держать scanning-состояние (мс) — спиннер + sweep. */
const SCANNING_HOLD_MS = 1400;

interface UseScanCountdownArgs {
  /** ISO-метка последнего скана (из stats.last_scan_at). */
  lastScanAt?: string | null;
  /** Интервал авто-скана в секундах (из observer-настроек, дефолт 30). */
  intervalSeconds?: number;
  /** Включён ли observer. При false отсчёт заморожен. */
  enabled?: boolean;
  /** Реальный запуск скана (POST scan-now). Вызывается при ручном и авто-триггере. */
  onScan?: () => void;
}

interface ScanCountdownState {
  scanning: boolean;
  /** Возраст последнего скана в секундах (для "Nс назад"). */
  age: number;
  /** Секунды до следующего авто-скана. */
  next: number;
  /** Интервал авто-скана (эхо аргумента, для max в ring). */
  interval: number;
  /** Ручной запуск скана. */
  doScan: () => void;
}

function ageFromIso(iso: string | null | undefined): number {
  if (!iso) return 0;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return 0;
  return Math.max(0, Math.round((Date.now() - t) / 1000));
}

export function useScanCountdown({
  lastScanAt,
  intervalSeconds = 30,
  enabled = true,
  onScan,
}: UseScanCountdownArgs): ScanCountdownState {
  const interval = Math.max(1, intervalSeconds);
  const [scanning, setScanning] = useState(false);
  const [age, setAge] = useState(() => ageFromIso(lastScanAt));
  // ref на scanning для tick-замыкания без пересоздания интервала
  const scanningRef = useRef(false);
  // ручной «локальный» момент скана: когда нет свежего lastScanAt от API,
  // считаем возраст от него, чтобы UI отозвался мгновенно.
  const localScanAtRef = useRef<number | null>(null);

  // Реакция на обновление lastScanAt с сервера — сбрасываем локальный override.
  useEffect(() => {
    localScanAtRef.current = null;
    setAge(ageFromIso(lastScanAt));
  }, [lastScanAt]);

  const doScan = useCallback(() => {
    if (scanningRef.current) return;
    scanningRef.current = true;
    setScanning(true);
    onScan?.();
    window.setTimeout(() => {
      scanningRef.current = false;
      setScanning(false);
      localScanAtRef.current = Date.now();
      setAge(0);
    }, SCANNING_HOLD_MS);
  }, [onScan]);

  // Тикалка возраста раз в секунду.
  useEffect(() => {
    const iv = window.setInterval(() => {
      if (scanningRef.current) return;
      const base = localScanAtRef.current;
      if (base !== null) {
        setAge(Math.max(0, Math.round((Date.now() - base) / 1000)));
      } else {
        setAge(ageFromIso(lastScanAt));
      }
    }, 1000);
    return () => window.clearInterval(iv);
  }, [lastScanAt]);

  const next = enabled ? Math.max(0, interval - (age % interval)) : interval;

  // Авто-запуск при достижении нуля (как в прототипе).
  useEffect(() => {
    if (enabled && next === 0 && !scanningRef.current) {
      doScan();
    }
  }, [next, enabled, doScan]);

  return { scanning, age, next, interval, doScan };
}
