"use strict";
// Параметры запроса am_tabular для active replication. Зеркало боевого запроса UI
// (docs/am_tabular_scanner_plan.md §1), но column_fields — НАШ полный набор, независимо от UI.
Object.defineProperty(exports, "__esModule", { value: true });
exports.AM_PAGE_LIMIT = exports.AM_ATTRIBUTION_WINDOWS = exports.AM_AD_DELIVERY_STATUSES = exports.AM_ACTION_TYPES = exports.AM_COLUMN_FIELDS = void 0;
exports.defaultAmConfig = defaultAmConfig;
// column_fields, которые запрашиваем у am_tabular (level=ad). Покрывает все поля сервиса.
exports.AM_COLUMN_FIELDS = [
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
exports.AM_ACTION_TYPES = [
    'lead',
    'omni_complete_registration',
    'omni_landing_page_view',
    'landing_page_view',
];
// Статусы доставки ад'ов, которые сканируем (как боевой filtering ad.delivery_info).
exports.AM_AD_DELIVERY_STATUSES = [
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
exports.AM_ATTRIBUTION_WINDOWS = ['default', 'inline'];
// Лимит строк на запрос (>> числа ад'ов; пагинация курсором при превышении).
exports.AM_PAGE_LIMIT = 5000;
function defaultAmConfig(campaignIds = [], ownerTag = '') {
    return {
        campaignIds: campaignIds.filter(Boolean),
        ownerTag: ownerTag || undefined,
        datePreset: 'today',
    };
}
//# sourceMappingURL=am-config.js.map