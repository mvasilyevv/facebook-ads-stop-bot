/**
 * HealthTab — вкладка мониторинга воркеров:
 *   - Список воркеров с ONLINE/OFFLINE badge и временем последнего heartbeat.
 *   - Overall-статус HEALTHY / DEGRADED / CRITICAL.
 *   - Restart observer и disable-worker через ConfirmDialog.
 */

import { useState } from "react";
import { RefreshCcw } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";
import { formatRelativeTime } from "@/lib/utils/format";
import type { HealthWorker } from "@/lib/types/api";

import {
  useHealthDetails,
  useRestartObserver,
  useRestartDisableWorker,
} from "@/lib/api/settings";

type ConfirmTarget = "observer" | "disable_worker" | null;

export function HealthTab() {
  const [confirmTarget, setConfirmTarget] = useState<ConfirmTarget>(null);

  const healthQuery = useHealthDetails();
  const restartObserver = useRestartObserver();
  const restartDisable = useRestartDisableWorker();

  const health = healthQuery.data;

  /** Цвет/вариант для overall. */
  function overallVariant(overall: string): "success" | "warning" | "stop" {
    if (overall === "HEALTHY") return "success";
    if (overall === "DEGRADED") return "warning";
    return "stop";
  }

  /** Запустить действие перезапуска по цели. */
  async function handleConfirmRestart() {
    if (confirmTarget === "observer") {
      await new Promise<void>((resolve, reject) => {
        restartObserver.mutate(undefined, {
          onSuccess: () => {
            toast.success("Observer перезапускается");
            resolve();
          },
          onError: (err) => {
            toast.error("Ошибка", err instanceof Error ? err.message : String(err));
            reject(err);
          },
        });
      });
    } else if (confirmTarget === "disable_worker") {
      await new Promise<void>((resolve, reject) => {
        restartDisable.mutate(undefined, {
          onSuccess: () => {
            toast.success("Disable worker перезапускается");
            resolve();
          },
          onError: (err) => {
            toast.error("Ошибка", err instanceof Error ? err.message : String(err));
            reject(err);
          },
        });
      });
    }
  }

  if (healthQuery.isError) {
    return (
      <ErrorState
        title="Не удалось загрузить статус воркеров."
        error={healthQuery.error}
        onRetry={() => healthQuery.refetch()}
      />
    );
  }

  return (
    <>
      <ConfirmDialog
        open={!!confirmTarget}
        onOpenChange={(o) => { if (!o) setConfirmTarget(null); }}
        title={
          confirmTarget === "observer"
            ? "Перезапустить Observer?"
            : "Перезапустить Disable Worker?"
        }
        description={
          confirmTarget === "observer"
            ? "Observer завершит текущий цикл и перезапустится. Сканирование прервётся на ~10–30 секунд."
            : "Disable worker перестанет обрабатывать задачи на время рестарта (~5–10 секунд)."
        }
        confirmWord="RESTART"
        confirmLabel="Перезапустить"
        cancelLabel="Отмена"
        onConfirm={handleConfirmRestart}
      />

      <div className="grid grid-cols-[1fr_320px] gap-8">
        {/* Левая колонка: список воркеров. */}
        <div className="space-y-6">
          <section>
            <div className="flex items-center gap-3 mb-4">
              <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9">
                Воркеры
              </h3>
              {healthQuery.isLoading ? (
                <Skeleton width={80} height={18} />
              ) : health?.overall ? (
                <Badge variant={overallVariant(health.overall)}>
                  {health.overall}
                </Badge>
              ) : null}
            </div>

            {healthQuery.isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 7 }).map((_, i) => (
                  <Skeleton key={i} height={44} />
                ))}
              </div>
            ) : (
              <div className="border border-bg-5 divide-y divide-bg-5">
                {health?.workers.map((w) => (
                  <WorkerRow key={w.name} worker={w} />
                ))}
                {(health?.workers.length ?? 0) === 0 && (
                  <div className="px-4 py-6 text-[13px] text-bg-9">
                    Нет данных о воркерах.
                  </div>
                )}
              </div>
            )}
          </section>
        </div>

        {/* Правая колонка: действия + легенда. */}
        <div className="space-y-6">
          <section className="border border-bg-5 bg-bg-1 p-5 space-y-3">
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-3">
              Действия
            </h3>
            <Button
              variant="secondary"
              size="sm"
              fullWidth
              leftIcon={<RefreshCcw size={13} aria-hidden="true" />}
              onClick={() => setConfirmTarget("observer")}
              loading={restartObserver.isPending}
            >
              Restart Observer
            </Button>
            <Button
              variant="secondary"
              size="sm"
              fullWidth
              leftIcon={<RefreshCcw size={13} aria-hidden="true" />}
              onClick={() => setConfirmTarget("disable_worker")}
              loading={restartDisable.isPending}
            >
              Restart Disable Worker
            </Button>
          </section>

          <section className="border border-bg-5 bg-bg-1 p-5">
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-3">
              Легенда
            </h3>
            <ul className="text-[12px] text-bg-9 space-y-2">
              <li>
                <Badge variant="success" size="sm">ONLINE</Badge>{" "}
                — heartbeat получен &lt;60 сек назад.
              </li>
              <li>
                <Badge variant="stop" size="sm">OFFLINE</Badge>{" "}
                — heartbeat не получен &gt;60 сек.
              </li>
              <li>
                <span className="font-display uppercase text-[10px] tracking-wider text-success">
                  HEALTHY
                </span>{" "}
                — все воркеры ONLINE.
              </li>
              <li>
                <span className="font-display uppercase text-[10px] tracking-wider text-warning">
                  DEGRADED
                </span>{" "}
                — часть воркеров OFFLINE.
              </li>
              <li>
                <span className="font-display uppercase text-[10px] tracking-wider text-danger">
                  CRITICAL
                </span>{" "}
                — большинство воркеров OFFLINE.
              </li>
            </ul>
          </section>
        </div>
      </div>
    </>
  );
}

/** Строка воркера с badge ONLINE/OFFLINE и временем last heartbeat. */
function WorkerRow({ worker }: { worker: HealthWorker }) {
  const isOnline = worker.status === "ONLINE";

  return (
    <div className="flex items-center justify-between px-4 py-3 hover:bg-bg-2 transition-colors">
      <div className="flex items-center gap-3">
        <span
          aria-hidden="true"
          className={[
            "size-1.5 rounded-full",
            isOnline ? "bg-success" : "bg-danger",
          ].join(" ")}
        />
        <span className="font-numeric text-[13px] text-bg-11">{worker.name}</span>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-[11px] text-bg-9">
          {worker.last_heartbeat_at
            ? formatRelativeTime(worker.last_heartbeat_at)
            : "никогда"}
        </span>
        <Badge variant={isOnline ? "success" : "stop"} size="sm">
          {worker.status}
        </Badge>
      </div>
    </div>
  );
}
