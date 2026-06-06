/**
 * Константы mutation_kind для meta-mutations.
 *
 * Источник правды — core/meta_api/mutations/ (имена хендлеров) и
 * TmaDraftOut.mutation_kind в generated.ts.
 * Используется Drafts UI: заголовки, глагол действия, описания.
 */

export const MUTATION_KINDS = [
  "pause_ad",
  "activate_ad",
  "pause_campaign",
  "activate_campaign",
  "set_adset_budget",
  "duplicate_campaign",
  "bulk_status_change",
  "create_campaign",
  "custom_audience",
  "set_ad_creative",
] as const;

export type MutationKind = (typeof MUTATION_KINDS)[number];

/**
 * Краткий глагол действия — для заголовков карточки черновика.
 * В верхнем регистре по стилю Drafts UI.
 */
export const MUTATION_KIND_VERBS: Record<MutationKind, string> = {
  pause_ad: "ПАУЗА",
  activate_ad: "ВОЗОБНОВИТЬ",
  pause_campaign: "ПАУЗА КАМПАНИИ",
  activate_campaign: "ВОЗОБНОВИТЬ КАМПАНИЮ",
  set_adset_budget: "ИЗМЕНИТЬ БЮДЖЕТ",
  duplicate_campaign: "ДУБЛИРОВАТЬ КАМПАНИЮ",
  bulk_status_change: "МАССОВОЕ ДЕЙСТВИЕ",
  create_campaign: "СОЗДАТЬ КАМПАНИЮ",
  custom_audience: "АУДИТОРИЯ",
  set_ad_creative: "СМЕНИТЬ КРЕАТИВ",
};

/**
 * Полное описание действия — для описания черновика.
 */
export const MUTATION_KIND_LABELS: Record<MutationKind, string> = {
  pause_ad: "Поставить объявление на паузу",
  activate_ad: "Возобновить показ объявления",
  pause_campaign: "Поставить кампанию на паузу",
  activate_campaign: "Возобновить кампанию",
  set_adset_budget: "Изменить бюджет адсета",
  duplicate_campaign: "Дублировать кампанию",
  bulk_status_change: "Массовое изменение статуса",
  create_campaign: "Создать новую кампанию",
  custom_audience: "Создать/обновить аудиторию",
  set_ad_creative: "Сменить креатив объявления",
};

/** Краткий глагол (PAUSE, REACTIVATE...) для mutation_kind. Fallback — сам код. */
export function mutationKindVerb(kind: string): string {
  return MUTATION_KIND_VERBS[kind as MutationKind] ?? kind.toUpperCase();
}

/** Полное описание действия. Fallback — сам код. */
export function mutationKindLabel(kind: string): string {
  return MUTATION_KIND_LABELS[kind as MutationKind] ?? kind;
}

/**
 * Возвращает true, если mutation_kind — это массовая операция
 * (bulk_status_change затрагивает N объектов, не один ad).
 */
export function isBulkMutation(kind: string): boolean {
  return kind === "bulk_status_change" || kind === "create_campaign" || kind === "custom_audience";
}
