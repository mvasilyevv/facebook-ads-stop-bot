/**
 * TaskQueueCard — одна колонка очереди (Disable или Enable) на Dashboard.
 *
 * Рендерит список TaskQueueRow + счётчик pending/retrying в заголовке.
 * Pending = статусы PENDING/RUNNING/RETRYING (не терминальные).
 *
 * Состояния: Loading (skeleton-строки), Error (ErrorState), Empty ("Очередь пуста").
 */

import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { TaskQueueRow } from "@/components/domain/TaskQueueRow";
import type { TaskQueueRow as TaskQueueRowData } from "@/lib/types/api";

const WAITING_STATUSES = new Set(["PENDING"]);
const RUNNING_STATUSES = new Set(["RUNNING"]);
const RETRYING_STATUSES = new Set(["RETRYING"]);

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
  const waiting = tasks.filter((t) => WAITING_STATUSES.has(t.status)).length;
  const running = tasks.filter((t) => RUNNING_STATUSES.has(t.status)).length;
  const retrying = tasks.filter((t) => RETRYING_STATUSES.has(t.status)).length;

  const metaText = isLoading
    ? "—"
    : [
        `${waiting} в очереди`,
        running > 0 ? `${running} выполняется` : null,
        retrying > 0 ? `${retrying} повтор` : null,
      ]
        .filter(Boolean)
        .join(" · ");

  return (
    <Card
      title={title}
      meta={
        <span className="tabular-nums">{metaText}</span>
      }
    >
      {isError ? (
        <ErrorState title="Не удалось загрузить очередь." error={error} onRetry={onRetry} />
      ) : isLoading ? (
        <div className="flex flex-col">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="grid grid-cols-[24px_1fr_auto_auto] gap-3.5 items-center py-3">
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
            <TaskQueueRow key={task.id} task={task} />
          ))}
        </div>
      )}
    </Card>
  );
}
