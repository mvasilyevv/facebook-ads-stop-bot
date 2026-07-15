/**
 * AdTaskHistory — секция «ИСТОРИЯ ЗАДАЧ» в AdDrawer: алерты + задачи, DESC по времени.
 * Выделено из AdDrawer.tsx (было >600 строк в одном файле — god-component).
 */
import { useMemo } from "react";
import { formatRelativeTime } from "@fb/shared";
import type { components } from "@fb/shared/api/generated";

import { Skeleton } from "@/components/ui/Skeleton";
import { Eyebrow } from "@/components/data/Eyebrow";
import { RulePills } from "./RulePill";

type AlertRow = components["schemas"]["AlertRow"];
type TaskRow = components["schemas"]["TaskRow"];

interface HistoryEntry {
  id: string;
  ts: string;
  kind: "warning" | "stop" | "task";
  title: string;
  rules?: string[];
  meta?: string;
}

const KIND_DOT: Record<HistoryEntry["kind"], string> = {
  warning: "var(--fsm-warning)",
  stop: "var(--fsm-stop)",
  task: "var(--accent)",
};

interface AdTaskHistoryProps {
  alerts: AlertRow[];
  tasks: TaskRow[];
  isLoading: boolean;
}

export function AdTaskHistory({ alerts, tasks, isLoading }: AdTaskHistoryProps) {
  const historyItems = useMemo<HistoryEntry[]>(() => {
    const out: HistoryEntry[] = [];
    for (const a of alerts) {
      out.push({
        id: `al-${a.id}`,
        ts: a.created_at,
        kind: a.stage === "stop" ? "stop" : "warning",
        title: a.stage === "stop" ? "Сработал STOP" : "Сработал WARNING",
        rules: a.matched_rule_codes,
      });
    }
    for (const t of tasks) {
      out.push({
        id: `tk-${t.id}`,
        ts: t.created_at,
        kind: "task",
        title: taskTitle(t.task_type),
        meta: `${t.status} · ${t.requested_by}`,
      });
    }
    out.sort((x, y) => new Date(y.ts).getTime() - new Date(x.ts).getTime());
    return out;
  }, [alerts, tasks]);

  return (
    <section>
      <Eyebrow className="mb-3">ИСТОРИЯ ЗАДАЧ</Eyebrow>
      {isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} height={36} className="w-full" />
          ))}
        </div>
      ) : historyItems.length === 0 ? (
        <div className="text-[13px] text-bg-9">Задач по объявлению нет.</div>
      ) : (
        <div className="flex flex-col">
          {historyItems.map((h) => (
            <HistoryRow key={h.id} entry={h} />
          ))}
        </div>
      )}
    </section>
  );
}

function HistoryRow({ entry }: { entry: HistoryEntry }) {
  return (
    <div className="flex items-start gap-3 py-2.5 border-b border-[var(--hairline)] last:border-b-0">
      <span
        aria-hidden="true"
        className="size-[7px] rounded-full mt-1.5 shrink-0"
        style={{ background: KIND_DOT[entry.kind] }}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[13px] text-bg-11">{entry.title}</span>
          <span className="font-display text-[11px] text-bg-8 tabular-nums shrink-0">
            {formatRelativeTime(entry.ts)}
          </span>
        </div>
        {entry.rules && entry.rules.length > 0 ? (
          <div className="mt-1.5">
            <RulePills codes={entry.rules} max={4} />
          </div>
        ) : null}
        {entry.meta ? <div className="font-display text-[11px] text-bg-9 mt-1">{entry.meta}</div> : null}
      </div>
    </div>
  );
}

/** Заголовок задачи по типу. */
function taskTitle(taskType: string): string {
  if (taskType === "disable") return "Задача на отключение";
  if (taskType === "enable") return "Enable-задача";
  if (taskType === "meta_api_mutation") return "Действие через API";
  return `Задача: ${taskType}`;
}
