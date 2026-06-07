/**
 * TaskQueues — секция «03 / ОЧЕРЕДЬ ЗАДАЧ» на Dashboard.
 *
 * Канон design_handoff/web-dashboard.jsx: grid 1fr 1fr из двух карточек
 * DISABLE QUEUE / ENABLE QUEUE. Строка задачи (components.jsx TaskRow):
 *   status-dot + ad-name (mono) + статус-лейбл + «×attempts · age».
 *
 * Данные — реальные TaskQueueRow (useDisableTasks / useEnableTasks).
 */

import { Eyebrow } from "@/components/data/Eyebrow";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import {
  normalizeTaskStatus,
  formatRelativeTime,
  type TaskStatus,
} from "@fb/shared";
import type { TaskQueueRow } from "@fb/shared";

// Статус задачи → {лейбл, цвет} (канон TASK_STATUS из components.jsx).
const STATUS_VIEW: Record<TaskStatus, { label: string; color: string }> = {
  PENDING: { label: "в очереди", color: "var(--bg-10)" },
  RUNNING: { label: "в работе", color: "var(--info)" },
  RETRYING: { label: "повтор", color: "var(--warning)" },
  FAILED: { label: "ошибка", color: "var(--danger)" },
  SUCCEEDED: { label: "готово", color: "var(--success)" },
  CANCELLED: { label: "отменено", color: "var(--bg-8)" },
};

function TaskRowItem({ task }: { task: TaskQueueRow }) {
  const status = normalizeTaskStatus(task.status);
  const view = STATUS_VIEW[status];

  return (
    <div
      className="grid items-center gap-3 border-b border-bg-5 px-3 last:border-b-0"
      style={{ gridTemplateColumns: "1fr auto auto", height: "var(--row-h)" }}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span
          aria-hidden="true"
          className="size-1.5 shrink-0 rounded-full"
          style={{ background: view.color }}
        />
        <span
          className="truncate font-display text-bg-11"
          style={{ fontSize: "var(--row-fs)" }}
        >
          {task.ad_name ?? task.fb_ad_id ?? "—"}
        </span>
      </div>
      <span
        className="min-w-[56px] text-right text-[11px]"
        style={{ color: view.color }}
      >
        {view.label}
      </span>
      <span className="min-w-[64px] text-right font-display text-[11px] tabular-nums text-bg-8">
        {task.attempt_count > 0 ? `×${task.attempt_count} · ` : ""}
        {formatRelativeTime(task.created_at)}
      </span>
    </div>
  );
}

interface QueueColProps {
  title: string;
  tasks: TaskQueueRow[];
  countColor: string;
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
}

function QueueCol({ title, tasks, countColor, isLoading, isError, error, onRetry }: QueueColProps) {
  return (
    <div className="border border-bg-5 bg-bg-1">
      <div className="flex items-baseline justify-between p-6 pb-3">
        <Eyebrow>{title}</Eyebrow>
        <span
          className="font-display text-[13px] tabular-nums"
          style={{ color: tasks.length ? countColor : "var(--bg-8)" }}
        >
          {tasks.length}
        </span>
      </div>
      <div className="px-6 pb-2">
        {isError ? (
          <ErrorState title="Не удалось загрузить очередь." error={error} onRetry={onRetry} />
        ) : isLoading ? (
          <div className="flex flex-col">
            {Array.from({ length: 3 }).map((_, i) => (
              <div
                key={i}
                className="grid items-center gap-3 py-3"
                style={{ gridTemplateColumns: "1fr auto auto" }}
              >
                <Skeleton height={13} className="w-full" />
                <Skeleton height={11} width={48} />
                <Skeleton height={11} width={48} />
              </div>
            ))}
          </div>
        ) : tasks.length === 0 ? (
          <div className="flex flex-col items-center gap-1.5 px-6 py-8 text-center">
            <div className="text-[13px] text-bg-10">Очередь пуста</div>
            <div className="text-[12px] text-bg-9">Нет задач в работе</div>
          </div>
        ) : (
          <div>
            {tasks.map((t) => (
              <TaskRowItem key={t.id} task={t} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

interface TaskQueuesProps {
  disableTasks: TaskQueueRow[];
  enableTasks: TaskQueueRow[];
  disableLoading: boolean;
  enableLoading: boolean;
  disableError: boolean;
  enableError: boolean;
  onRetryDisable?: () => void;
  onRetryEnable?: () => void;
}

export function TaskQueues({
  disableTasks,
  enableTasks,
  disableLoading,
  enableLoading,
  disableError,
  enableError,
  onRetryDisable,
  onRetryEnable,
}: TaskQueuesProps) {
  return (
    <div>
      <Eyebrow num="03" className="mb-4 flex">
        ОЧЕРЕДЬ ЗАДАЧ
      </Eyebrow>
      <div className="grid grid-cols-2 gap-4">
        <QueueCol
          title="DISABLE QUEUE"
          tasks={disableTasks}
          countColor="var(--danger)"
          isLoading={disableLoading}
          isError={disableError}
          onRetry={onRetryDisable}
        />
        <QueueCol
          title="ENABLE QUEUE"
          tasks={enableTasks}
          countColor="var(--success)"
          isLoading={enableLoading}
          isError={enableError}
          onRetry={onRetryEnable}
        />
      </div>
    </div>
  );
}
