/**
 * useScanCountdown — состояние scan-кластера на Dashboard (countdown + age + scanning).
 *
 * Два режима отсчёта:
 *   1. РЕАЛЬНЫЙ (предпочтительный): задан `nextScanAt` — ISO-метка следующего скана из
 *      observer:runtime.next_scan_at. Отсчёт идёт к ней от абсолютного времени, поэтому
 *      отражает АДАПТИВНЫЙ интервал бэка (CRITICAL 18с / ELEVATED 45с / CALM 90с / IDLE 135с)
 *      и jitter — ровно то, что воркер реально запланировал. Сервер сам водит скан, поэтому
 *      авто-триггер на нуле выключен (UI только показывает время, не инициирует скан).
 *   2. MOCK (фолбэк): `nextScanAt` нет — крутим локальный отсчёт от статичного `intervalSeconds`
 *      (interval − age % interval), как раньше. Используется, пока бэк не отдал next_scan_at.
 *
 * Контракт:
 *   - `age` — секунды с последнего скана (из lastScanAt; при scanning → "сканирую").
 *   - `next` — секунды до следующего скана (реальные или mock).
 *   - `interval` — знаменатель кольца (полный интервал текущего цикла для пропорции дуги).
 *   - `scanning` — идёт ли скан прямо сейчас (на время вызова onScan).
 *   - `doScan()` — ручной запуск: ставит scanning, дёргает onScan, держит ~1.4s.
 *   - в MOCK-режиме на `next===0` (enabled) — авто-запуск doScan (как в прототипе).
 *
 * Возраст и отсчёт обновляются раз в секунду; реальный `next` пересчитывается от абсолютной
 * метки nextScanAt, поэтому переживает рефетчи и не «убегает» при обновлении данных с сервера.
 */

import { useCallback, useEffect, useRef, useState } from "react";

/** Сколько визуально держать scanning-состояние (мс) — спиннер + sweep. */
const SCANNING_HOLD_MS = 1400;

interface UseScanCountdownArgs {
  /** ISO-метка последнего скана (из stats.last_scan_at). */
  lastScanAt?: string | null;
  /** Интервал авто-скана в секундах (из observer-настроек, дефолт 30) — для MOCK-режима и max кольца. */
  intervalSeconds?: number;
  /** ISO-метка следующего скана (observer:runtime.next_scan_at). Задано → РЕАЛЬНЫЙ режим. */
  nextScanAt?: string | null;
  /** Включён ли observer. При false отсчёт заморожен. */
  enabled?: boolean;
  /** Реальный запуск скана (POST scan-now). Вызывается при ручном и (в MOCK) авто-триггере. */
  onScan?: () => void;
}

interface ScanCountdownState {
  scanning: boolean;
  /** Возраст последнего скана в секундах (для "Nс назад"). */
  age: number;
  /** Секунды до следующего скана. */
  next: number;
  /** Знаменатель кольца (полный интервал текущего цикла). */
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

/** Секунды до будущей ISO-метки (clamp ≥0). null — если метки нет/не распарсилась. */
function secondsUntilIso(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.round((t - Date.now()) / 1000));
}

export function useScanCountdown({
  lastScanAt,
  intervalSeconds = 30,
  nextScanAt,
  enabled = true,
  onScan,
}: UseScanCountdownArgs): ScanCountdownState {
  const baseInterval = Math.max(1, intervalSeconds);
  const [scanning, setScanning] = useState(false);
  const [age, setAge] = useState(() => ageFromIso(lastScanAt));
  // Реальный отсчёт: тикает раз в секунду от абсолютной nextScanAt.
  const [realNext, setRealNext] = useState<number | null>(() => secondsUntilIso(nextScanAt));
  // ref на scanning для tick-замыкания без пересоздания интервала
  const scanningRef = useRef(false);
  // ручной «локальный» момент скана: когда нет свежего lastScanAt от API,
  // считаем возраст от него, чтобы UI отозвался мгновенно.
  const localScanAtRef = useRef<number | null>(null);
  // Полный интервал текущего реального цикла (знаменатель кольца). Фиксируется при смене
  // nextScanAt — это свежее «полное» время до скана сразу после планирования воркером.
  const [cycleMax, setCycleMax] = useState<number>(baseInterval);

  // Реакция на обновление lastScanAt с сервера — сбрасываем локальный override.
  useEffect(() => {
    localScanAtRef.current = null;
    setAge(ageFromIso(lastScanAt));
  }, [lastScanAt]);

  // Реакция на новую метку nextScanAt: фиксируем полный интервал цикла + ресинк отсчёта.
  useEffect(() => {
    const secs = secondsUntilIso(nextScanAt);
    setRealNext(secs);
    if (secs !== null && secs > 0) {
      setCycleMax(secs);
    }
  }, [nextScanAt]);

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

  // Тикалка раз в секунду: возраст + реальный отсчёт (от абсолютной метки → без дрейфа).
  useEffect(() => {
    const iv = window.setInterval(() => {
      if (scanningRef.current) return;
      const base = localScanAtRef.current;
      if (base !== null) {
        setAge(Math.max(0, Math.round((Date.now() - base) / 1000)));
      } else {
        setAge(ageFromIso(lastScanAt));
      }
      setRealNext(secondsUntilIso(nextScanAt));
    }, 1000);
    return () => window.clearInterval(iv);
  }, [lastScanAt, nextScanAt]);

  const realMode = realNext !== null;

  // next: реальный отсчёт (адаптивный) либо MOCK (interval − age % interval).
  const next = !enabled
    ? realMode
      ? cycleMax
      : baseInterval
    : realMode
      ? realNext
      : Math.max(0, baseInterval - (age % baseInterval));

  // Знаменатель кольца: полный интервал реального цикла (≥ next, чтобы дуга не переполнялась).
  const interval = realMode ? Math.max(cycleMax, next, 1) : baseInterval;

  // Авто-запуск при достижении нуля — ТОЛЬКО в MOCK-режиме (в реальном скан водит сервер).
  useEffect(() => {
    if (!realMode && enabled && next === 0 && !scanningRef.current) {
      doScan();
    }
  }, [realMode, next, enabled, doScan]);

  return { scanning, age, next, interval, doScan };
}
