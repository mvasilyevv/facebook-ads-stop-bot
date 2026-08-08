export const CAMPAIGN_META_GROUPS = [
  { key: "campaigns", label: "Кампании" },
  { key: "adsets", label: "Группы" },
  { key: "ads", label: "Объявления" },
  { key: "creatives", label: "Креативы" },
] as const;

export interface CampaignMetaIdGroup {
  key: (typeof CAMPAIGN_META_GROUPS)[number]["key"];
  label: string;
  ids: string[];
}

interface CampaignRunReviewInput {
  status: string;
  progress?: Record<string, unknown> | null;
  created_meta_ids?: Record<string, unknown> | null;
}

export type CampaignRunTaskState =
  | "queued"
  | "running"
  | "confirmed"
  | "failed"
  | "cancelled"
  | "unknown";

export type CampaignRunControlAction = "abort" | "resume";

export type CampaignRunLifecycleTone =
  | "pending"
  | "running"
  | "confirmed"
  | "failed"
  | "cancelled"
  | "unknown";

export interface CampaignRunLifecyclePresentation {
  label: string;
  description: string;
  tone: CampaignRunLifecycleTone;
}

const TASK_LIFECYCLE: Record<
  CampaignRunTaskState,
  CampaignRunLifecyclePresentation
> = {
  queued: {
    label: "В очереди",
    description: "Задача принята, выполнение ещё не началось.",
    tone: "pending",
  },
  running: {
    label: "Выполняется",
    description: "Задача выполняется. Это ещё не подтверждённый результат.",
    tone: "running",
  },
  confirmed: {
    label: "Подтверждено",
    description: "Финальный результат задачи подтверждён.",
    tone: "confirmed",
  },
  failed: {
    label: "Не выполнено",
    description: "Задача завершилась ошибкой и не была подтверждена.",
    tone: "failed",
  },
  cancelled: {
    label: "Отменено",
    description: "Задача отменена до завершения.",
    tone: "cancelled",
  },
  unknown: {
    label: "Результат неизвестен",
    description:
      "Фактический результат не подтверждён. Не повторяйте запуск до ручной сверки.",
    tone: "unknown",
  },
};

const ABORT_REASON: Record<string, string> = {
  campaign_task_missing: "Остановка недоступна: связанная задача не найдена.",
  abort_already_requested:
    "Остановка уже запрошена. Дождитесь итогового статуса задачи.",
  run_already_cancelled: "Запуск уже остановлен.",
  run_already_succeeded: "Запуск уже успешно завершён.",
  run_already_failed: "Запуск уже завершился ошибкой.",
  run_task_state_inconsistent:
    "Остановка заблокирована: состояние задачи требует повторной сверки.",
};

const RESUME_REASON: Record<string, string> = {
  run_already_succeeded: "Повтор не нужен: запуск уже успешно завершён.",
  run_not_terminal:
    "Повтор доступен только после безопасного завершения текущей задачи.",
  campaign_task_missing: "Повтор заблокирован: исходная задача не найдена.",
  campaign_task_not_terminal:
    "Повтор доступен только после завершения исходной задачи.",
  external_boundary_crossed:
    "Повтор заблокирован: задача могла начать изменения в Meta.",
  created_meta_objects_present:
    "Повтор заблокирован: в Meta уже есть созданные объекты.",
  terminal_outcome_not_rejected:
    "Повтор заблокирован: безопасный отказ до Meta не подтверждён.",
  checkpoint_reason_not_resumable:
    "Повтор заблокирован: ошибка не относится к безопасной точке повтора.",
  invalid_config_checkpoint:
    "Повтор заблокирован: сохранённая конфигурация запуска повреждена.",
  invalid_media_checkpoint:
    "Повтор заблокирован: ссылка на сохранённые креативы некорректна.",
  media_checkpoint_missing:
    "Повтор заблокирован: набор креативов больше недоступен.",
  media_checkpoint_empty:
    "Повтор заблокирован: в сохранённом наборе нет креативов.",
  media_checkpoint_incomplete:
    "Повтор заблокирован: часть сохранённых креативов недоступна.",
};

export function campaignRunTaskLifecycle(
  state: CampaignRunTaskState,
): CampaignRunLifecyclePresentation {
  return TASK_LIFECYCLE[state];
}

export function campaignRunControlReason(
  action: CampaignRunControlAction,
  reason: string,
  available: boolean,
): string {
  if (available) {
    return action === "abort"
      ? "Можно запросить безопасную остановку текущей задачи."
      : "Можно безопасно повторить: изменения в Meta не начинались.";
  }
  const known =
    action === "abort" ? ABORT_REASON[reason] : RESUME_REASON[reason];
  if (known) return known;
  return action === "abort"
    ? "Остановка пока недоступна. Обновите данные запуска."
    : "Безопасный повтор пока недоступен. Обновите данные запуска.";
}

export function campaignRunCommandLifecycle(
  action: CampaignRunControlAction,
  state: CampaignRunTaskState,
): CampaignRunLifecyclePresentation {
  const actionLabel = action === "abort" ? "Остановка" : "Повтор";
  const lifecycle = TASK_LIFECYCLE[state];
  const description =
    state === "queued"
      ? `${actionLabel} поставлена в очередь. Завершение ещё не подтверждено.`
      : state === "running"
        ? `${actionLabel} выполняется. Завершение ещё не подтверждено.`
        : state === "confirmed"
          ? `${actionLabel} подтверждена.`
          : state === "failed"
            ? `${actionLabel} не выполнена.`
            : state === "cancelled"
              ? `${actionLabel} отменена.`
              : `Результат команды «${actionLabel.toLowerCase()}» неизвестен. Не повторяйте её до сверки.`;
  return { ...lifecycle, description };
}

function scalarIds(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.flatMap(scalarIds);
  }
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "bigint"
  ) {
    const id = String(value).trim();
    return id ? [id] : [];
  }
  return [];
}

function isScalarIdCheckpoint(value: unknown): boolean {
  if (Array.isArray(value)) return value.every(isScalarIdCheckpoint);
  return (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "bigint"
  );
}

export function campaignMetaIdGroups(
  createdMetaIds: Record<string, unknown> | null | undefined,
): CampaignMetaIdGroup[] {
  const source = createdMetaIds ?? {};
  return CAMPAIGN_META_GROUPS.map(({ key, label }) => ({
    key,
    label,
    ids: [...new Set(scalarIds(source[key]))],
  }));
}

export function campaignRunRequiresManualReview(
  run: CampaignRunReviewInput,
): boolean {
  if (run.status !== "failed") return false;

  const progress = run.progress ?? {};
  if (String(progress.outcome ?? "").toUpperCase() === "UNKNOWN") return true;

  const created = run.created_meta_ids ?? {};
  const groups = campaignMetaIdGroups(created);
  return (
    groups.some((group) => group.ids.length > 0) ||
    CAMPAIGN_META_GROUPS.some(
      ({ key }) =>
        Object.hasOwn(created, key) && !isScalarIdCheckpoint(created[key]),
    )
  );
}

export function adsManagerCampaignUrl(
  createdMetaIds: Record<string, unknown> | null | undefined,
): string | null {
  const campaigns =
    campaignMetaIdGroups(createdMetaIds).find(
      (group) => group.key === "campaigns",
    )?.ids ?? [];
  const numericIds = campaigns.filter((id) => /^\d+$/.test(id));
  if (numericIds.length === 0) return null;

  return `https://www.facebook.com/adsmanager/manage/campaigns?ids=${encodeURIComponent(
    numericIds.join(","),
  )}`;
}
