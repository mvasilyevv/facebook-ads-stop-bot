/**
 * Управление сканированием прямо из шапки главной.
 *
 * Тумблер читает и меняет авторитетное состояние /api/settings/observer — тот
 * же контракт, что и раздел сканирования в Настройках. Время следующего цикла
 * берётся только из подтверждённой system-секции снапшота: без свежего снимка
 * показывается «не подтверждено», а не оптимистичная оценка.
 */
import { useEffect, useState } from "react";
import { Radar } from "lucide-react";

import { safeApiProblemMessage } from "@fb/operator-api";
import type { OperatorSnapshot } from "@fb/shared/operator/contracts";

import { Switch } from "@/components/ui/Switch";
import { toast } from "@/components/ui/toastStore";
import { useObserverSettings, useToggleScanning } from "@/lib/api/settings";

const ETA_TICK_MS = 30_000;

/** «цикл через N мин» до следующего скана; null — показать нечего. */
export function nextScanEtaLabel(
  nextScanAt: string | null | undefined,
  nowMs: number,
): string | null {
  if (!nextScanAt) return null;
  const at = Date.parse(nextScanAt);
  if (Number.isNaN(at)) return null;
  const deltaSeconds = Math.round((at - nowMs) / 1000);
  if (deltaSeconds <= 0) return "цикл ожидается";
  if (deltaSeconds < 60) return "цикл через <1 мин";
  return `цикл через ${Math.round(deltaSeconds / 60)} мин`;
}

export function ScanningControl({ system }: { system: OperatorSnapshot["system"] }) {
  const settingsQuery = useObserverSettings();
  const toggleScanning = useToggleScanning();
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), ETA_TICK_MS);
    return () => window.clearInterval(timer);
  }, []);

  const enabled = settingsQuery.data?.is_scanning_enabled ?? null;
  const systemTrusted = system.state === "ready" || system.state === "partial";
  const eta = systemTrusted ? nextScanEtaLabel(system.data?.next_scan_at ?? null, nowMs) : null;

  async function handleToggle() {
    if (enabled === null) return;
    const next = !enabled;
    try {
      await toggleScanning.mutateAsync(next);
      toast.success(next ? "Сканирование включено" : "Сканирование остановлено");
    } catch (error) {
      toast.error(
        "Состояние сканирования не изменено",
        safeApiProblemMessage(error, "Проверьте готовность Observer"),
      );
    }
  }

  return (
    <div className="ledger-proof-stamp" role="group" aria-label="Периодическое сканирование">
      <Radar size={16} aria-hidden="true" />
      <span>Сканирование</span>
      {enabled === null ? (
        <span className="text-warning" role="status">
          {settingsQuery.isPending ? "Проверяю…" : "Не подтверждено"}
        </span>
      ) : (
        <>
          <span role="status" className={enabled ? undefined : "text-warning"}>
            {enabled ? "Включено" : "Остановлено"}
          </span>
          {enabled ? (
            <span className="ledger-proof-stamp__time">{eta ?? "цикл не подтверждён"}</span>
          ) : null}
          <Switch
            checked={enabled}
            disabled={toggleScanning.isPending}
            onChange={() => void handleToggle()}
            label={
              enabled
                ? "Остановить периодическое сканирование"
                : "Включить периодическое сканирование"
            }
          />
        </>
      )}
    </div>
  );
}
