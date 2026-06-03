/**
 * Drafts (`/drafts`) — очередь действий, ожидающих ручного подтверждения.
 *
 * Два источника:
 *   1. PENDING disable/enable задачи (task_queue) — через /dashboard/{disable,enable}-tasks.
 *   2. DRAFT meta_api_mutation (AI-предложения через Marketing API) —
 *      через admin-роутер /dashboard/draft-tasks.
 *
 * Десктоп — доверенная admin-зона (X-API-Key, без Telegram-личности): per-user ACL
 * не применяется, его держит бэк. meta-черновики, созданные в Telegram, бэк вернёт
 * как неподтверждаемые с десктопа (confirm → 409) — обрабатываем как ошибку с тостом.
 */

import { useState, useMemo } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { FileEdit } from "lucide-react";

import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { DraftCard } from "@/components/domain/DraftCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Pill } from "@/components/ui/Pill";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";
import {
  useDrafts,
  useApproveDraft,
  useCancelDraft,
  useMetaDrafts,
  useConfirmMetaDraft,
  useRejectMetaDraft,
  type MetaDraft,
} from "@/lib/api/drafts";
import { cn } from "@/lib/utils/cn";
import type { TaskQueueRow } from "@/lib/types/api";

export const Route = createFileRoute("/drafts/")({
  component: DraftsPage,
});

/** Доступные типы мутаций для фильтра. */
const TYPE_OPTIONS = [
  { value: "", label: "Все" },
  { value: "disable", label: "Отключение" },
  { value: "enable", label: "Включение" },
  { value: "meta_api_mutation", label: "Действие через API" },
];

/** Человекочитаемые названия meta-мутаций. */
const MUTATION_KIND_LABELS: Record<string, string> = {
  pause_ad: "Пауза объявления",
  activate_ad: "Включение объявления",
  pause_campaign: "Пауза кампании",
  activate_campaign: "Включение кампании",
  set_adset_budget: "Изменение бюджета",
  duplicate_campaign: "Дублирование кампании",
  create_campaign: "Создание кампании",
  bulk_status_change: "Массовое вкл/выкл",
  custom_audience: "Custom Audience",
  set_ad_creative: "Замена креатива",
};

function mutationKindLabel(kind: string): string {
  return MUTATION_KIND_LABELS[kind] ?? kind;
}

/** Строка diff-таблицы карточки (совместима с DraftCard). */
interface DiffRow {
  key: string;
  current?: string | null;
  target?: string | null;
  highlight?: boolean;
}

/** Генерирует читаемый заголовок карточки по task_type + payload. */
function buildSummary(row: TaskQueueRow): string {
  switch (row.task_type) {
    case "disable":
      return `Отключить «${row.ad_name ?? row.fb_ad_id ?? row.id}»`;
    case "enable":
      return `Включить «${row.ad_name ?? row.fb_ad_id ?? row.id}»`;
    case "meta_api_mutation":
      return `Действие с API · задача ${row.id.slice(0, 8)}`;
    default:
      return `${row.task_type} · задача ${row.id.slice(0, 8)}`;
  }
}

/** Diff-таблица для disable/enable задачи. */
function buildDiff(row: TaskQueueRow): DiffRow[] {
  const rows: DiffRow[] = [];

  if (row.fb_ad_id) {
    rows.push({ key: "ID объявления", target: row.fb_ad_id });
  }
  if (row.ad_name) {
    rows.push({ key: "Название", target: row.ad_name });
  }

  switch (row.task_type) {
    case "disable":
      rows.push({ key: "Статус", current: "ACTIVE", target: "PAUSED", highlight: true });
      break;
    case "enable":
      rows.push({ key: "Статус", current: "PAUSED", target: "ACTIVE", highlight: true });
      break;
    default:
      rows.push({ key: "Действие", target: row.task_type, highlight: true });
  }

  if (row.attempt_count > 0) {
    rows.push({ key: "Попыток", target: String(row.attempt_count) });
  }

  return rows;
}

/** Заголовок карточки meta-мутации. */
function buildMetaSummary(m: MetaDraft): string {
  const base = mutationKindLabel(m.mutation_kind);
  return m.target_id ? `${base} · ${m.target_id}` : base;
}

/** Diff-таблица meta-мутации из payload-параметров. */
function buildMetaDiff(m: MetaDraft): DiffRow[] {
  const rows: DiffRow[] = [];
  rows.push({ key: "Действие", target: mutationKindLabel(m.mutation_kind), highlight: true });
  if (m.target_id) rows.push({ key: "Объект", target: m.target_id });
  if (m.ad_account_id) rows.push({ key: "Кабинет", target: m.ad_account_id });
  for (const [k, v] of Object.entries(m.payload ?? {})) {
    rows.push({
      key: k,
      target: v != null && typeof v === "object" ? JSON.stringify(v) : String(v),
    });
  }
  return rows;
}

/** Дата протухания черновика (24h от created_at). */
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

/** Метка типа мутации для заголовка карточки disable/enable. */
function mutationLabel(task_type: string): string {
  switch (task_type) {
    case "disable":
      return "Отключить объявление";
    case "enable":
      return "Включить объявление";
    case "meta_api_mutation":
      return "Действие с API";
    default:
      return task_type;
  }
}

/** Нормализованная карточка из любого источника — единый вход в DraftCard. */
interface DraftCardModel {
  key: string;
  filterType: string;
  taskTypeLabel: string;
  createdAt: string | null;
  requestedBy: string | null;
  summary: string;
  diff: DiffRow[];
  approve: () => Promise<void>;
  cancel: () => Promise<void>;
}

function DraftsPage() {
  const [selectedType, setSelectedType] = useState("");

  // Универсальные диалоги: хранят сводку + действие (источник-агностично).
  const [confirmApprove, setConfirmApprove] = useState<{
    summary: string;
    run: () => Promise<void>;
  } | null>(null);
  const [confirmCancel, setConfirmCancel] = useState<{
    summary: string;
    run: () => Promise<void>;
  } | null>(null);

  const draftsQuery = useDrafts();
  const metaQuery = useMetaDrafts();
  const approveMutation = useApproveDraft();
  const cancelMutation = useCancelDraft();
  const confirmMeta = useConfirmMetaDraft();
  const rejectMeta = useRejectMetaDraft();

  // Нормализуем оба источника в единый список карточек.
  const cards = useMemo<DraftCardModel[]>(() => {
    const out: DraftCardModel[] = [];
    for (const d of draftsQuery.data ?? []) {
      out.push({
        key: `task-${d.id}`,
        filterType: d.task_type,
        taskTypeLabel: mutationLabel(d.task_type),
        createdAt: d.created_at,
        requestedBy: d.requested_by,
        summary: buildSummary(d),
        diff: buildDiff(d),
        approve: () =>
          approveMutation.mutateAsync({ id: d.id, task_type: d.task_type }).then(() => undefined),
        cancel: () =>
          cancelMutation.mutateAsync({ id: d.id, task_type: d.task_type }).then(() => undefined),
      });
    }
    for (const m of metaQuery.data ?? []) {
      out.push({
        key: `meta-${m.id}`,
        filterType: "meta_api_mutation",
        taskTypeLabel: mutationKindLabel(m.mutation_kind),
        createdAt: m.created_at,
        requestedBy: m.requested_by,
        summary: buildMetaSummary(m),
        diff: buildMetaDiff(m),
        approve: () => confirmMeta.mutateAsync(m.id).then(() => undefined),
        cancel: () => rejectMeta.mutateAsync(m.id).then(() => undefined),
      });
    }
    // Новые сверху по дате создания.
    out.sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""));
    return out;
  }, [draftsQuery.data, metaQuery.data, approveMutation, cancelMutation, confirmMeta, rejectMeta]);

  const filteredCards = selectedType
    ? cards.filter((c) => c.filterType === selectedType)
    : cards;
  const expiringCount = filteredCards.filter((c) => isExpiringSoon(c.createdAt)).length;

  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = { "": cards.length };
    for (const c of cards) counts[c.filterType] = (counts[c.filterType] ?? 0) + 1;
    return counts;
  }, [cards]);

  async function handleApprove() {
    if (!confirmApprove) return;
    try {
      await confirmApprove.run();
      toast.success("Подтверждено", "Задача передана в очередь на исполнение.");
    } catch (err) {
      toast.error("Ошибка подтверждения", err instanceof Error ? err.message : "Неизвестная ошибка");
      throw err;
    }
  }

  async function handleCancel() {
    if (!confirmCancel) return;
    try {
      await confirmCancel.run();
      toast.success("Отменено", "Черновик отклонён.");
    } catch (err) {
      toast.error("Ошибка отмены", err instanceof Error ? err.message : "Неизвестная ошибка");
      throw err;
    }
  }

  const isLoading = draftsQuery.isLoading || metaQuery.isLoading;
  const isError = draftsQuery.isError || metaQuery.isError;

  const subtitle = isLoading ? null : (
    <>
      <span className={cn("font-display", expiringCount > 0 ? "text-warning" : "text-accent")}>
        {cards.length}
      </span>{" "}
      на подтверждение
      {expiringCount > 0 ? (
        <>
          <HeaderSep />
          <span className="text-warning font-display">{expiringCount}</span> истекает в течение 1ч
        </>
      ) : null}
      <HeaderSep />
      Требуют ручного подтверждения
    </>
  );

  return (
    <>
      <PageHeader
        eyebrowNum="02"
        eyebrow="РУЧНОЙ КОНТРОЛЬ · ПОДТВЕРДИТЬ · ВЫПОЛНИТЬ"
        title="Черновики"
        displayNumber="02"
        subtitle={subtitle}
      />

      {/* Фильтр по типу */}
      <div className="flex items-center gap-2 py-2 pb-5 border-b border-bg-5 mb-8 flex-wrap">
        <span className="font-display text-[10px] uppercase tracking-widest text-bg-8 mr-2">
          Тип
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
                <span className="text-[10px] opacity-70 ml-0.5">{typeCounts[opt.value]}</span>
              ) : null}
            </Pill>
          </button>
        ))}
      </div>

      {isLoading ? (
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
      ) : isError ? (
        <ErrorState
          title="Не удалось загрузить черновики."
          error={draftsQuery.error ?? metaQuery.error}
          onRetry={() => {
            draftsQuery.refetch();
            metaQuery.refetch();
          }}
        />
      ) : filteredCards.length === 0 ? (
        <EmptyState
          icon={<FileEdit size={40} strokeWidth={1.25} aria-hidden="true" />}
          title="Нет черновиков на подтверждение"
          description="Когда появится действие на подтверждение — оно будет здесь со всеми деталями."
        />
      ) : (
        <div className="flex flex-col gap-4">
          {filteredCards.map((card) => {
            const expiringSoon = isExpiringSoon(card.createdAt);
            return (
              <div key={card.key} className={cn("relative", expiringSoon && "border border-warning/40")}>
                {expiringSoon ? (
                  <div
                    aria-label="Истекает скоро"
                    className={cn(
                      "absolute top-0 right-4 z-10",
                      "font-display text-[9px] tracking-widest uppercase font-semibold",
                      "bg-warning text-bg-0 px-2 py-0.5 -translate-y-px",
                    )}
                  >
                    ИСТЕКАЕТ СКОРО
                  </div>
                ) : null}

                <DraftCard
                  taskType={card.taskTypeLabel}
                  createdAt={card.createdAt}
                  requestedBy={card.requestedBy}
                  summary={card.summary}
                  diff={card.diff}
                  reason={null}
                  expiresAt={getExpiresAt(card.createdAt)}
                  onApprove={() => setConfirmApprove({ summary: card.summary, run: card.approve })}
                  onCancel={() => setConfirmCancel({ summary: card.summary, run: card.cancel })}
                />
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
        title="Подтвердить выполнение?"
        description={
          confirmApprove
            ? `${confirmApprove.summary}. Задача будет передана воркеру на немедленное исполнение — отменить нельзя.`
            : ""
        }
        confirmLabel="Подтвердить и выполнить"
        cancelLabel="Отмена"
        confirmVariant="primary"
        onConfirm={handleApprove}
      />

      {/* ConfirmDialog — Cancel */}
      <ConfirmDialog
        open={confirmCancel !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmCancel(null);
        }}
        title="Отклонить черновик?"
        description={
          confirmCancel ? `${confirmCancel.summary}. Черновик будет отменён.` : ""
        }
        confirmLabel="Отклонить"
        cancelLabel="Назад"
        onConfirm={handleCancel}
      />
    </>
  );
}

/** Экспорт форматтеров для тестов. */
export { buildSummary, buildDiff, isExpiringSoon, mutationLabel };
