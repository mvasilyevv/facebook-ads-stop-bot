/**
 * StepLaunch — шаг 7 визарда: запуск → прогресс → созданные Meta-ID.
 * Запускает POST /tools/campaigns/launch, затем поллит run/{id} каждые 3s.
 * При финальном статусе показывает результат (success / error + cleanup).
 */
import { useEffect, useState } from "react";
import { CheckCircle, XCircle, RefreshCw } from "lucide-react";
import { Button, Badge } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { haptic } from "@/lib/tg";
import { useLaunchCampaign, useCampaignRun, useCleanupRun } from "@/lib/api";
import type { CampaignConfig, CampaignRunDetail } from "@/lib/campaignTypes";
import { RUN_STATUS_LABEL, TERMINAL_STATUSES } from "@/lib/campaignTypes";
import { useWizardStore } from "./-wizardStore";
import { cn } from "@/lib/cn";

function StatusIcon({ status }: { status: string }) {
  if (status === "succeeded")
    return <CheckCircle size={28} strokeWidth={1.6} className="text-[var(--color-success)]" />;
  if (status === "failed" || status === "cancelled")
    return <XCircle size={28} strokeWidth={1.6} className="text-[var(--color-danger)]" />;
  return (
    <RefreshCw size={24} strokeWidth={1.5} className="text-accent animate-spin" />
  );
}

function MetaIdsList({ ids }: { ids: Record<string, unknown> }) {
  const entries = Object.entries(ids);
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-col gap-0 border border-[var(--hairline)] divide-y divide-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden">
      {entries.map(([key, val]) => (
        <div key={key} className="flex items-start justify-between gap-3 px-3.5 py-3 min-h-[44px] bg-bg-1">
          <p className="text-[11px] uppercase tracking-[0.07em] text-bg-8 shrink-0 mt-0.5">{key}</p>
          <p className="font-mono text-[12px] text-bg-10 text-right break-all">
            {typeof val === "string" ? val : JSON.stringify(val)}
          </p>
        </div>
      ))}
    </div>
  );
}

function ProgressSection({ run }: { run: CampaignRunDetail }) {
  const progress = run.progress ?? {};
  const statusLabel = RUN_STATUS_LABEL[run.status] ?? run.status;
  const isTerminal = TERMINAL_STATUSES.has(run.status);
  return (
    <div className="flex flex-col gap-3">
      {/* Статус + иконка */}
      <div className="flex flex-col items-center gap-3 py-4">
        <StatusIcon status={run.status} />
        <div className="text-center">
          <p className="font-display text-[18px] text-bg-11 leading-snug">{statusLabel}</p>
          {!isTerminal && (
            <p className="text-[12px] text-bg-8 mt-1">Обновляется каждые 3 секунды…</p>
          )}
        </div>
      </div>

      {/* Прогресс JSONB */}
      {Object.keys(progress).length > 0 && (
        <div className="border border-[var(--hairline)] bg-bg-1 px-3.5 py-3 rounded-[var(--radius-2)]">
          <Eyebrow className="mb-2">ПРОГРЕСС</Eyebrow>
          <pre className="text-[11px] text-bg-9 whitespace-pre-wrap break-all font-mono leading-relaxed">
            {JSON.stringify(progress, null, 2)}
          </pre>
        </div>
      )}

      {/* Meta IDs */}
      {run.status === "succeeded" && Object.keys(run.created_meta_ids ?? {}).length > 0 && (
        <div className="flex flex-col gap-2">
          <Eyebrow>СОЗДАННЫЕ META-ID</Eyebrow>
          <MetaIdsList ids={run.created_meta_ids} />
        </div>
      )}

      {/* Ошибка */}
      {run.error && (
        <div className="border border-[var(--color-danger)] bg-[var(--color-danger-bg)] p-3 rounded-[var(--radius-2)]">
          <p className="text-[12px] text-[var(--color-danger)] leading-snug">{run.error}</p>
        </div>
      )}
    </div>
  );
}

export function StepLaunch() {
  const { config, runId, setRunId, reset } = useWizardStore();
  const launch = useLaunchCampaign();
  const cleanup = useCleanupRun();
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [launched, setLaunched] = useState(!!runId);

  const runQuery = useCampaignRun(runId ?? "", !!runId);
  const run = runQuery.data ?? null;

  // Запускаем залив при первом рендере (если не уже запущен)
  useEffect(() => {
    if (launched) return;
    setLaunched(true);
    haptic.impact("medium");

    launch.mutateAsync({
      config: config as CampaignConfig,
    })
      .then((resp) => {
        setRunId(resp.run_id);
      })
      .catch((err) => {
        setLaunchError((err as Error).message);
        haptic.notify("error");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const isSuccess = run?.status === "succeeded";
  const isFailed = run?.status === "failed" || run?.status === "cancelled";
  const hasMetaIds =
    run && Object.keys(run.created_meta_ids ?? {}).length > 0;

  function handleCleanup() {
    if (!runId) return;
    haptic.impact("medium");
    cleanup.mutate({ id: runId });
  }

  function handleStartNew() {
    haptic.selection();
    reset();
  }

  return (
    <div className="flex flex-col gap-4 p-4 pb-8">
      <Eyebrow num="07">ЗАПУСК</Eyebrow>

      {/* Ошибка запуска (до получения run_id) */}
      {launchError && (
        <div className="flex flex-col items-center gap-3 py-6">
          <XCircle size={32} strokeWidth={1.4} className="text-[var(--color-danger)]" />
          <p className="text-[13px] text-[var(--color-danger)] text-center">{launchError}</p>
        </div>
      )}

      {/* Run ID (после launch) */}
      {runId && (
        <div className="border border-[var(--hairline)] bg-bg-1 px-3.5 py-2.5 rounded-[var(--radius-2)]">
          <p className="text-[10px] uppercase tracking-[0.08em] text-bg-7 mb-1">RUN ID</p>
          <p className="font-mono text-[12px] text-bg-9 truncate">{runId}</p>
        </div>
      )}

      {/* Ожидание — ещё нет данных run */}
      {runId && !run && runQuery.isLoading && (
        <div className="flex flex-col items-center gap-3 py-6">
          <RefreshCw size={24} strokeWidth={1.5} className="text-accent animate-spin" />
          <p className="text-[13px] text-bg-8">Получаем статус…</p>
        </div>
      )}

      {/* Прогресс запуска */}
      {run && <ProgressSection run={run} />}

      {/* Действия после завершения */}
      <div className={cn("flex flex-col gap-3 mt-2", (!isSuccess && !isFailed) && "hidden")}>
        {isFailed && hasMetaIds && (
          <Button
            variant="danger"
            fullWidth
            onClick={handleCleanup}
            loading={cleanup.isPending}
          >
            Cleanup Meta-объектов
          </Button>
        )}
        {isSuccess && (
          <div className="border border-[var(--hairline)] bg-bg-1 p-3 rounded-[var(--radius-2)]">
            <p className="text-[12px] text-bg-9 text-center leading-relaxed">
              Кампания создана. Откройте Ads Manager и снимите паузу,
              когда будете готовы к старту.
            </p>
          </div>
        )}
        <Button variant="secondary" fullWidth onClick={handleStartNew}>
          Новая кампания
        </Button>
      </div>

      {/* Badge статуса воркера */}
      {run && (
        <div className="flex justify-center">
          <Badge
            variant={
              run.status === "succeeded"
                ? "done"
                : run.status === "failed" || run.status === "cancelled"
                  ? "failed"
                  : "warning"
            }
          >
            {RUN_STATUS_LABEL[run.status] ?? run.status}
          </Badge>
        </div>
      )}
    </div>
  );
}
