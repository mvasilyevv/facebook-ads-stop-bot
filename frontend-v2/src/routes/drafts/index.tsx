/**
 * Drafts (`/drafts`) — список AI-черновиков, ожидающих ручного подтверждения.
 *
 * Блоки:
 *   1. PageHeader — eyebrow 03, счётчик pending + expiring.
 *   2. Фильтр по task_type — Pill-чипы.
 *   3. Список DraftCard — diff, AI-reasoning, кнопки Approve/Cancel + ACL.
 *   4. Состояния: loading (Skeleton), error (ErrorState+retry), empty.
 *
 * ACL: draft создан другим chat_id → Approve недоступен (ACL-blocked card).
 * Approve → useApproveDraft (retry endpoint) + ConfirmDialog + Toast.
 * Cancel → useCancelDraft (delete endpoint) + Toast.
 */

import { useState, useMemo } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { FileEdit, Lock } from "lucide-react";

import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { DraftCard } from "@/components/domain/DraftCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Pill } from "@/components/ui/Pill";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";
import { useDrafts, useApproveDraft, useCancelDraft } from "@/lib/api/drafts";
import { cn } from "@/lib/utils/cn";
import type { TaskQueueRow } from "@/lib/types/api";

export const Route = createFileRoute("/drafts/")({
  component: DraftsPage,
});

/** Доступные типы мутаций для фильтра. */
const TYPE_OPTIONS = [
  { value: "", label: "All" },
  { value: "disable", label: "pause_ad" },
  { value: "enable", label: "activate_ad" },
  { value: "meta_api_mutation", label: "meta_api" },
];

/** Генерирует читаемый заголовок карточки по task_type + payload. */
function buildSummary(row: TaskQueueRow): string {
  switch (row.task_type) {
    case "disable":
      return `Pause ad ${row.ad_name ?? row.fb_ad_id ?? row.id}`;
    case "enable":
      return `Activate ad ${row.ad_name ?? row.fb_ad_id ?? row.id}`;
    case "meta_api_mutation":
      return `Meta API mutation · task ${row.id.slice(0, 8)}`;
    default:
      return `${row.task_type} · task ${row.id.slice(0, 8)}`;
  }
}

/** Строит diff-таблицу для карточки из TaskQueueRow. */
function buildDiff(row: TaskQueueRow) {
  const rows = [];

  if (row.fb_ad_id) {
    rows.push({ key: "ad_id", target: row.fb_ad_id });
  }
  if (row.ad_name) {
    rows.push({ key: "ad_name", target: row.ad_name });
  }

  switch (row.task_type) {
    case "disable":
      rows.push({
        key: "effective_status",
        current: "ACTIVE",
        target: "PAUSED",
        highlight: true,
      });
      break;
    case "enable":
      rows.push({
        key: "effective_status",
        current: "PAUSED",
        target: "ACTIVE",
        highlight: true,
      });
      break;
    default:
      rows.push({
        key: "mutation",
        target: row.task_type,
        highlight: true,
      });
  }

  if (row.attempt_count > 0) {
    rows.push({ key: "attempt_count", target: String(row.attempt_count) });
  }

  return rows;
}

/** Вычисляет дату протухания черновика (24h от created_at). */
function getExpiresAt(createdAt: string | null): string | null {
  if (!createdAt) return null;
  const d = new Date(createdAt);
  d.setHours(d.getHours() + 24);
  return d.toISOString();
}

/** true, если черновик истекает в течение 1 часа. */
function isExpiringSoon(createdAt: string | null): boolean {
  if (!createdAt) return false;
  const expires = new Date(createdAt);
  expires.setHours(expires.getHours() + 24);
  return expires.getTime() - Date.now() < 60 * 60 * 1000;
}

/** Метка типа мутации для заголовка карточки. */
function mutationLabel(task_type: string): string {
  switch (task_type) {
    case "disable":
      return "meta_api / pause_ad";
    case "enable":
      return "meta_api / activate_ad";
    case "meta_api_mutation":
      return "meta_api / mutation";
    default:
      return task_type;
  }
}

// Текущий chat_id из localStorage (если хранится при авторизации).
// Fallback — null (нет ACL-проверки, Approve доступен).
function getCurrentChatId(): number | null {
  try {
    const raw = localStorage.getItem("chat_id");
    return raw ? Number(raw) : null;
  } catch {
    return null;
  }
}

function DraftsPage() {
  // ─── Фильтр по типу ─────────────────────────────────────────────────────
  const [selectedType, setSelectedType] = useState("");

  // ─── Состояние ConfirmDialog ─────────────────────────────────────────────
  const [confirmApprove, setConfirmApprove] = useState<TaskQueueRow | null>(null);
  const [confirmCancel, setConfirmCancel] = useState<TaskQueueRow | null>(null);

  // ─── API ─────────────────────────────────────────────────────────────────
  const draftsQuery = useDrafts(selectedType || undefined);
  const approveMutation = useApproveDraft();
  const cancelMutation = useCancelDraft();

  const currentChatId = getCurrentChatId();

  // ─── Подсчёт статистики ──────────────────────────────────────────────────
  const { filteredDrafts, expiringCount } = useMemo(() => {
    const all = draftsQuery.data ?? [];
    const filtered = selectedType ? all.filter((d) => d.task_type === selectedType) : all;
    const expiring = filtered.filter((d) => isExpiringSoon(d.created_at)).length;
    return { filteredDrafts: filtered, expiringCount: expiring };
  }, [draftsQuery.data, selectedType]);

  // ─── Counts для Pill-фильтров ────────────────────────────────────────────
  const typeCounts = useMemo(() => {
    const all = draftsQuery.data ?? [];
    const counts: Record<string, number> = { "": all.length };
    for (const d of all) {
      counts[d.task_type] = (counts[d.task_type] ?? 0) + 1;
    }
    return counts;
  }, [draftsQuery.data]);

  // ─── Обработчики ─────────────────────────────────────────────────────────
  async function handleApprove() {
    if (!confirmApprove) return;
    try {
      await approveMutation.mutateAsync({
        id: confirmApprove.id,
        task_type: confirmApprove.task_type,
      });
      toast.success(
        "Черновик подтверждён",
        `Задача ${confirmApprove.id.slice(0, 8)} передана в очередь на исполнение.`,
      );
    } catch (err) {
      toast.error(
        "Ошибка подтверждения",
        err instanceof Error ? err.message : "Неизвестная ошибка",
      );
      throw err;
    }
  }

  async function handleCancel() {
    if (!confirmCancel) return;
    try {
      await cancelMutation.mutateAsync({
        id: confirmCancel.id,
        task_type: confirmCancel.task_type,
      });
      toast.success(
        "Черновик отменён",
        `Задача ${confirmCancel.id.slice(0, 8)} отменена.`,
      );
    } catch (err) {
      toast.error(
        "Ошибка отмены",
        err instanceof Error ? err.message : "Неизвестная ошибка",
      );
      throw err;
    }
  }

  // ─── Subtitle header ─────────────────────────────────────────────────────
  const pendingCount = draftsQuery.data?.length ?? 0;

  const subtitle = draftsQuery.isLoading ? null : (
    <>
      <span className={cn("font-display", expiringCount > 0 ? "text-warning" : "text-accent")}>
        {pendingCount}
      </span>{" "}
      pending
      {expiringCount > 0 ? (
        <>
          <HeaderSep />
          <span className="text-warning font-display">{expiringCount}</span> expiring within 1h
        </>
      ) : null}
      <HeaderSep />
      AI proposals require human approval — owner-only
    </>
  );

  // ─── Рендер ──────────────────────────────────────────────────────────────
  return (
    <>
      <PageHeader
        eyebrowNum="03"
        eyebrow="HUMAN-IN-THE-LOOP · APPROVE · EXECUTE"
        title="Drafts."
        displayNumber="03"
        subtitle={subtitle}
      />

      {/* Фильтр по типу мутации */}
      <div className="flex items-center gap-2 py-2 pb-5 border-b border-bg-5 mb-8 flex-wrap">
        <span className="font-display text-[10px] uppercase tracking-widest text-bg-8 mr-2">
          Type
        </span>
        {TYPE_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => setSelectedType(opt.value)}
            className="p-0 border-0 bg-transparent focus:outline-none"
            aria-label={`Фильтр: ${opt.label}`}
          >
            <Pill active={selectedType === opt.value}>
              {opt.label}
              {(typeCounts[opt.value] ?? 0) > 0 ? (
                <span className="text-[10px] opacity-70 ml-0.5">
                  {typeCounts[opt.value]}
                </span>
              ) : null}
            </Pill>
          </button>
        ))}
      </div>

      {/* Loading */}
      {draftsQuery.isLoading ? (
        <div className="flex flex-col gap-4">
          {[1, 2, 3].map((n) => (
            <div key={n} className="border border-bg-5 bg-bg-1 p-6">
              <Skeleton height={12} width="30%" className="mb-3" />
              <Skeleton height={20} width="55%" className="mb-4" />
              <Skeleton height={12} width="40%" className="mb-6" />
              <Skeleton height={80} />
            </div>
          ))}
        </div>
      ) : draftsQuery.isError ? (
        <ErrorState
          title="Не удалось загрузить черновики."
          error={draftsQuery.error}
          onRetry={() => draftsQuery.refetch()}
        />
      ) : filteredDrafts.length === 0 ? (
        <EmptyState
          icon={<FileEdit size={40} strokeWidth={1.25} aria-hidden="true" />}
          title="Нет черновиков на подтверждение"
          description="ИИ сегодня молчит. Когда появится черновик — он будет здесь со всеми деталями и контролями."
        />
      ) : (
        <div className="flex flex-col gap-4">
          {filteredDrafts.map((draft) => {
            // ACL: draft доступен для Approve только если создан текущим chat_id
            // или chat_id не известен (нет авторизации на фронте).
            const isBlocked =
              currentChatId !== null &&
              draft.requested_by_chat_id !== null &&
              draft.requested_by_chat_id !== currentChatId;

            const expiringSoon = isExpiringSoon(draft.created_at);
            const expiresAt = getExpiresAt(draft.created_at);

            const approveBlocked = approveMutation.isPending || cancelMutation.isPending;

            return (
              <div
                key={draft.id}
                className={cn(
                  "relative",
                  expiringSoon && "draft-expiring-soon",
                )}
              >
                {/* Лейбл "EXPIRING SOON" */}
                {expiringSoon ? (
                  <div
                    aria-label="Истекает скоро"
                    className={cn(
                      "absolute top-0 right-4 z-10",
                      "font-display text-[9px] tracking-widest uppercase font-semibold",
                      "bg-warning text-bg-0 px-2 py-0.5",
                      "-translate-y-px",
                    )}
                  >
                    EXPIRING SOON
                  </div>
                ) : null}

                <DraftCard
                  taskType={mutationLabel(draft.task_type)}
                  createdAt={draft.created_at}
                  requestedBy={draft.requested_by}
                  summary={buildSummary(draft)}
                  diff={buildDiff(draft)}
                  reason={null}
                  expiresAt={expiresAt}
                  canApprove={!isBlocked}
                  approveDisabledReason={
                    isBlocked
                      ? `Only the owner (@${draft.requested_by ?? "?"} · chat ${draft.requested_by_chat_id}) can approve this draft.`
                      : undefined
                  }
                  onApprove={() => setConfirmApprove(draft)}
                  onCancel={() => setConfirmCancel(draft)}
                  busy={approveBlocked && confirmApprove?.id === draft.id}
                />

                {/* Блок ACL-note для заблокированных карточек */}
                {isBlocked ? (
                  <div className="px-6 py-2 border-t border-bg-5 bg-bg-0 flex items-center gap-2 text-warning font-display text-[10.5px] tracking-wide">
                    <Lock size={12} aria-hidden="true" />
                    Owner-only — created by @{draft.requested_by ?? "?"} · chat{" "}
                    {draft.requested_by_chat_id}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      {/* ConfirmDialog — Approve */}
      <ConfirmDialog
        open={confirmApprove !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmApprove(null);
        }}
        title="Подтвердить выполнение черновика?"
        description={
          confirmApprove
            ? `Задача ${confirmApprove.id.slice(0, 8)} будет передана воркеру на немедленное исполнение. Это необратимо.`
            : ""
        }
        confirmLabel="Approve & execute"
        cancelLabel="Отмена"
        onConfirm={handleApprove}
      />

      {/* ConfirmDialog — Cancel */}
      <ConfirmDialog
        open={confirmCancel !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmCancel(null);
        }}
        title="Отменить черновик?"
        description={
          confirmCancel
            ? `Задача ${confirmCancel.id.slice(0, 8)} будет отменена. Действие необратимо.`
            : ""
        }
        confirmLabel="Отменить задачу"
        cancelLabel="Назад"
        onConfirm={handleCancel}
      />
    </>
  );
}

/** Экспорт форматтера для тестов. */
export { buildSummary, buildDiff, isExpiringSoon, mutationLabel };
