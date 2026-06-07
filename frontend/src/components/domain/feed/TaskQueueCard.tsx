/**
 * TaskQueueCard — колонка очереди задач (Disable / Enable) на Dashboard.
 *
 * Header: "N pending · M retrying" + eyebrow title.
 * Body: список TaskRow. Состояния: loading, error, empty, data.
 */

import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { TaskRow } from "./TaskRow";
import type { TaskQueueRow as TaskQueueRowData } from "@fb/shared";

// Статусы в состоянии ожидания/выполнения
const PENDING_STATUSES = new Set(["PENDING", "DRAFT"]);
const RETRYING_STATUSES = new Set(["RETRYING"]);
const RUNNING_STATUSES = new Set(["RUNNING"]);

interface TaskQueueCardProps {
  title: string;
  tasks: TaskQueueRowData[];
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
  emptyLabel?: string;
}

export function TaskQueueCard({
  title,
  tasks,
  isLoading,
  isError,
  error,
  onRetry,
  emptyLabel = "Очередь пуста",
}: TaskQueueCardProps) {
  const pendingCount = tasks.filter((t) => PENDING_STATUSES.has(t.status)).length;
  const retryingCount = tasks.filter((t) => RETRYING_STATUSES.has(t.status)).length;
  const runningCount = tasks.filter((t) => RUNNING_STATUSES.has(t.status)).length;

  // Мета-строка в заголовке: "12 pending · 2 retrying"
  const metaParts: string[] = [];
  if (pendingCount > 0) metaParts.push(`${pendingCount} pending`);
  if (runningCount > 0) metaParts.push(`${runningCount} running`);
  if (retryingCount > 0) metaParts.push(`${retryingCount} retrying`);

  const metaText = isLoading
    ? "—"
    : metaParts.length > 0
      ? metaParts.join(" · ")
      : "all done";

  return (
    <Card
      title={title}
      meta={<span className="tabular-nums">{metaText}</span>}
      padded={false}
    >
      <div className="px-6 pb-2">
        {isError ? (
          <ErrorState
            title="Не удалось загрузить очередь."
            error={error}
            onRetry={onRetry}
          />
        ) : isLoading ? (
          // Skeleton-строки
          <div className="flex flex-col">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="grid items-center gap-3.5 py-3 border-b border-bg-3 last:border-b-0"
                style={{ gridTemplateColumns: "24px 1fr auto auto" }}
              >
                <Skeleton width={24} height={24} />
                <Skeleton height={13} className="w-full" />
                <Skeleton width={56} height={22} />
                <Skeleton width={32} height={11} />
              </div>
            ))}
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState title={emptyLabel} description="Нет задач за последнее время." />
        ) : (
          <div>
            {tasks.map((task) => (
              <TaskRow key={task.id} task={task} />
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
