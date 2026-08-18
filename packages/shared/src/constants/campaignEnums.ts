/**
 * Валидные enum-опции FB для визарда создания кампаний (шаг «Параметры»).
 *
 * У Meta нет единого «дай валидные опции» API — её собственный UI зашивает эти
 * значения и связку валидности (objective → допустимые optimization_goal). Здесь
 * тот же подход: дропдауны вместо free-text + гемблинг-дефолт по SOP
 * (OUTCOME_SALES / OFFSITE_CONVERSIONS / PURCHASE). Финальную корректность связки
 * гарантирует pre-flight `validate_only` (Фаза 3) — реальная проверка Meta.
 *
 * Опции в формате { value, label } — напрямую в <Select options=...>.
 */

export interface EnumOption {
  value: string;
  label: string;
}

/** 6 ODAX-целей кампании. Дефолт для гемблинга — OUTCOME_SALES. */
export const CAMPAIGN_OBJECTIVES: EnumOption[] = [
  { value: "OUTCOME_SALES", label: "Sales (продажи/конверсии)" },
  { value: "OUTCOME_LEADS", label: "Leads (лиды)" },
  { value: "OUTCOME_TRAFFIC", label: "Traffic (трафик)" },
  { value: "OUTCOME_ENGAGEMENT", label: "Engagement (вовлечение)" },
  { value: "OUTCOME_AWARENESS", label: "Awareness (узнаваемость)" },
  { value: "OUTCOME_APP_PROMOTION", label: "App promotion (приложение)" },
];

/**
 * Допустимые optimization_goal по objective (best-effort матрица; первый в списке —
 * дефолт). Не претендует на 100% полноту enum'а Meta — даёт рабочие связки; точную
 * валидацию делает validate_only. Источник: Graph API Ad Set reference + ODAX-доки.
 */
export const OPTIMIZATION_GOALS_BY_OBJECTIVE: Record<string, EnumOption[]> = {
  OUTCOME_SALES: [
    { value: "OFFSITE_CONVERSIONS", label: "OFFSITE_CONVERSIONS (конверсии пикселя)" },
    { value: "VALUE", label: "VALUE (макс. ценность)" },
    { value: "LANDING_PAGE_VIEWS", label: "LANDING_PAGE_VIEWS" },
    { value: "LINK_CLICKS", label: "LINK_CLICKS" },
    { value: "REACH", label: "REACH" },
    { value: "IMPRESSIONS", label: "IMPRESSIONS" },
  ],
  OUTCOME_LEADS: [
    { value: "OFFSITE_CONVERSIONS", label: "OFFSITE_CONVERSIONS" },
    { value: "LEAD_GENERATION", label: "LEAD_GENERATION (форма)" },
    { value: "QUALITY_LEAD", label: "QUALITY_LEAD" },
    { value: "LINK_CLICKS", label: "LINK_CLICKS" },
    { value: "LANDING_PAGE_VIEWS", label: "LANDING_PAGE_VIEWS" },
  ],
  OUTCOME_TRAFFIC: [
    { value: "LINK_CLICKS", label: "LINK_CLICKS" },
    { value: "LANDING_PAGE_VIEWS", label: "LANDING_PAGE_VIEWS" },
    { value: "REACH", label: "REACH" },
    { value: "IMPRESSIONS", label: "IMPRESSIONS" },
    { value: "QUALITY_CALL", label: "QUALITY_CALL" },
  ],
  OUTCOME_ENGAGEMENT: [
    { value: "POST_ENGAGEMENT", label: "POST_ENGAGEMENT" },
    { value: "PAGE_LIKES", label: "PAGE_LIKES" },
    { value: "EVENT_RESPONSES", label: "EVENT_RESPONSES" },
    { value: "OFFSITE_CONVERSIONS", label: "OFFSITE_CONVERSIONS" },
    { value: "LINK_CLICKS", label: "LINK_CLICKS" },
    { value: "LANDING_PAGE_VIEWS", label: "LANDING_PAGE_VIEWS" },
    { value: "THRUPLAY", label: "THRUPLAY (видео)" },
    { value: "REACH", label: "REACH" },
  ],
  OUTCOME_AWARENESS: [
    { value: "REACH", label: "REACH" },
    { value: "IMPRESSIONS", label: "IMPRESSIONS" },
    { value: "AD_RECALL_LIFT", label: "AD_RECALL_LIFT" },
    { value: "THRUPLAY", label: "THRUPLAY (видео)" },
  ],
  OUTCOME_APP_PROMOTION: [
    { value: "APP_INSTALLS", label: "APP_INSTALLS" },
    { value: "OFFSITE_CONVERSIONS", label: "OFFSITE_CONVERSIONS (app events)" },
    { value: "LINK_CLICKS", label: "LINK_CLICKS" },
    { value: "VALUE", label: "VALUE" },
  ],
};

/**
 * Стандартные события пикселя для custom_event_type. Актуальны ТОЛЬКО когда
 * optimization_goal = OFFSITE_CONVERSIONS (требуется promoted_object). Дефолт
 * гемблинга — PURCHASE (FTD/депозит).
 */
export const PIXEL_EVENT_TYPES: EnumOption[] = [
  { value: "PURCHASE", label: "PURCHASE (депозит/FTD)" },
  { value: "LEAD", label: "LEAD" },
  { value: "COMPLETE_REGISTRATION", label: "COMPLETE_REGISTRATION (регистрация)" },
  { value: "ADD_TO_CART", label: "ADD_TO_CART" },
  { value: "INITIATE_CHECKOUT", label: "INITIATE_CHECKOUT" },
  { value: "ADD_PAYMENT_INFO", label: "ADD_PAYMENT_INFO" },
  { value: "SUBSCRIBE", label: "SUBSCRIBE" },
  { value: "START_TRIAL", label: "START_TRIAL" },
  { value: "CONTACT", label: "CONTACT" },
  { value: "SEARCH", label: "SEARCH" },
  { value: "VIEW_CONTENT", label: "VIEW_CONTENT" },
  { value: "ADD_TO_WISHLIST", label: "ADD_TO_WISHLIST" },
  { value: "DONATE", label: "DONATE" },
  { value: "SCHEDULE", label: "SCHEDULE" },
  { value: "SUBMIT_APPLICATION", label: "SUBMIT_APPLICATION" },
];

/** Только эта оптимизация требует custom_event_type (promoted_object пикселя). */
export const OPTIMIZATION_GOAL_REQUIRES_EVENT = "OFFSITE_CONVERSIONS";

/**
 * Валидные CTA. Дефолт гемблинга — PLAY_GAME.
 * Лейблы — как кнопки называются в русской версии Ads Manager; в API уходит value.
 */
export const CALL_TO_ACTIONS: EnumOption[] = [
  { value: "PLAY_GAME", label: "Играть" },
  { value: "LEARN_MORE", label: "Подробнее" },
  { value: "SIGN_UP", label: "Регистрация" },
  { value: "DOWNLOAD", label: "Скачать" },
  { value: "GET_OFFER", label: "Получить предложение" },
  { value: "SHOP_NOW", label: "Купить" },
  { value: "SUBSCRIBE", label: "Подписаться" },
  { value: "INSTALL_MOBILE_APP", label: "Установить приложение" },
  { value: "USE_APP", label: "Использовать приложение" },
  { value: "BOOK_TRAVEL", label: "Забронировать" },
  { value: "CONTACT_US", label: "Свяжитесь с нами" },
  { value: "APPLY_NOW", label: "Подать заявку" },
  { value: "ORDER_NOW", label: "Заказать" },
  { value: "GET_QUOTE", label: "Получить расценки" },
  { value: "SEE_MORE", label: "Ещё" },
  { value: "OPEN_LINK", label: "Открыть ссылку" },
];

/** Гемблинг-дефолт по SOP (см. MEMORY: optimize Purchase). */
export const CAMPAIGN_GOAL_DEFAULTS = {
  objective: "OUTCOME_SALES",
  optimization_goal: "OFFSITE_CONVERSIONS",
  custom_event_type: "PURCHASE",
  cta: "PLAY_GAME",
} as const;

/**
 * Дефолтный optimization_goal для objective (первый валидный из матрицы).
 * Нужен при смене objective — чтобы не остаться на невалидной для новой цели опции.
 */
export function defaultOptimizationGoal(objective: string): string {
  const first = OPTIMIZATION_GOALS_BY_OBJECTIVE[objective]?.[0];
  return first ? first.value : "OFFSITE_CONVERSIONS";
}
