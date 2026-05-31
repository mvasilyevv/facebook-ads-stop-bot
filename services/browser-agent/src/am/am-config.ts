// Параметры запроса am_tabular для active replication. Зеркало боевого запроса UI
// (docs/am_tabular_scanner_plan.md §1), но column_fields — НАШ полный набор, независимо от UI.

// column_fields, которые запрашиваем у am_tabular (level=ad). Покрывает все поля сервиса.
export const AM_COLUMN_FIELDS: readonly string[] = [
  'results',
  'cost_per_result',
  'objective',
  'reach',
  'impressions',
  'spend',
  'clicks',
  'cpc',
  'actions',
  'cost_per_action_type',
  'ctr',
  'outbound_clicks',
  'outbound_clicks_ctr',
  'cpm',
  'frequency',
  'attribution_setting',
  'conversion_count_setting',
  'ad_id',
];

// action_type, которые нас интересуют (лиды/реги/LPV) — кладём в filtering action_type IN [...].
export const AM_ACTION_TYPES: readonly string[] = [
  'lead',
  'omni_complete_registration',
  'omni_landing_page_view',
  'landing_page_view',
];

// Статусы доставки ад'ов, которые сканируем (как боевой filtering ad.delivery_info).
export const AM_AD_DELIVERY_STATUSES: readonly string[] = [
  'active',
  'archived',
  'completed',
  'inactive',
  'limited',
  'not_delivering',
  'not_published',
  'pending_review',
  'recently_completed',
  'recently_rejected',
  'rejected',
  'scheduled',
];

// Окна атрибуции (как UI: default+inline; парсер выбирает default).
export const AM_ATTRIBUTION_WINDOWS: readonly string[] = ['default', 'inline'];

// Лимит строк на запрос (>> числа ад'ов; пагинация курсором при превышении).
export const AM_PAGE_LIMIT = 5000;

export interface AmScanConfig {
  // allowlist кампаний (#3): фильтр am_tabular по campaign.id IN [...]; пусто → без фильтра.
  campaignIds: string[];
  // owner_tag: если campaignIds пуст, am резолвит campaign.id по имени → тянет только свой скоуп.
  ownerTag?: string;
  // окно дат am_tabular: 'today', 'yesterday', ... (как UI date_preset).
  datePreset: string;
}

export function defaultAmConfig(campaignIds: string[] = [], ownerTag = ''): AmScanConfig {
  return {
    campaignIds: campaignIds.filter(Boolean),
    ownerTag: ownerTag || undefined,
    datePreset: 'today',
  };
}
