// Набор колонок Ads Manager для вкладок кабинетов (уровень КАМПАНИЙ).
//
// Вкладки кабинетов открываются на manage/campaigns с этим query, чтобы пользователь
// сразу видел нужные метрики (name/delivery/budget/results/spend/cpc/lead/reg/ctr/cpm/
// frequency и пр.). На скан (am_tabular) это НЕ влияет — скан снифит токен из любых
// graph-запросов страницы и сам дёргает level=ad через fetch (см. am-fetch.ts).
//
// Дефолт — пресет пользователя (его column_preset + явные columns). Переопределяется
// env BROWSER_AGENT_AM_COLUMNS_QS (строка query без ведущего '?'), чтобы менять набор
// колонок/пресет без пересборки образа.

const DEFAULT_COLUMNS_QS =
  'columns=name%2Cdelivery%2Cbudget%2Cresults%2Creach%2Cimpressions%2Ccost_per_result' +
  '%2Cspend%2Cclicks%2Ccpc%2Cactions%3Alead%2Ccost_per_action_type%3Alead' +
  '%2Cactions%3Aomni_complete_registration%2Ccost_per_action_type%3Aomni_complete_registration' +
  '%2Cctr%2Ccampaign_group_name%2Ccampaign_name%2Coutbound_clicks%3Aoutbound_click' +
  '%2Coutbound_clicks_ctr%3Aoutbound_click%2Cactions%3Aomni_landing_page_view' +
  '%2Ccost_per_action_type%3Alanding_page_view%2Ccpm%2Cfrequency' +
  '&attribution_windows=default&column_preset=1030561339462971';

/** Query-строка колонок Ads Manager (без ведущего '?'). Env переопределяет дефолт. */
export function adsManagerColumnsQs(): string {
  const env = process.env.BROWSER_AGENT_AM_COLUMNS_QS;
  return env && env.trim() ? env.trim() : DEFAULT_COLUMNS_QS;
}
