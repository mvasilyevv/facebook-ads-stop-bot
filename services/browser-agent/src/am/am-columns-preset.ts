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

export const DEFAULT_COLUMNS_QS =
  "columns=name%2Cdelivery%2Cbudget%2Cresults%2Creach%2Cimpressions%2Ccost_per_result" +
  "%2Cspend%2Cclicks%2Ccpc%2Cactions%3Alead%2Ccost_per_action_type%3Alead" +
  "%2Cactions%3Aomni_complete_registration%2Ccost_per_action_type%3Aomni_complete_registration" +
  "%2Cctr%2Ccampaign_group_name%2Ccampaign_name%2Coutbound_clicks%3Aoutbound_click" +
  "%2Coutbound_clicks_ctr%3Aoutbound_click%2Cactions%3Aomni_landing_page_view" +
  "%2Ccost_per_action_type%3Alanding_page_view%2Ccpm%2Cfrequency" +
  "&attribution_windows=default&column_preset=1030561339462971";

const ALLOWED_COLUMNS_QUERY_KEYS = [
  "columns",
  "attribution_windows",
  "column_preset",
] as const;

export function sanitizeColumnsQs(raw: string): string {
  if (raw.length > 16_384) return DEFAULT_COLUMNS_QS;
  const parsed = new URLSearchParams(raw);
  const safe = new URLSearchParams();
  for (const key of ALLOWED_COLUMNS_QUERY_KEYS) {
    const values = parsed.getAll(key);
    if (values.length === 1 && values[0] && values[0].length <= 8_192) {
      safe.set(key, values[0]);
    }
  }
  return safe.size > 0 ? safe.toString() : DEFAULT_COLUMNS_QS;
}

/** Query-строка колонок Ads Manager (без ведущего '?'). DB → env → default. */
export function adsManagerColumnsQs(
  databaseOverride?: string | null,
): string {
  const env = process.env.BROWSER_AGENT_AM_COLUMNS_QS;
  return sanitizeColumnsQs(
    databaseOverride && databaseOverride.trim()
      ? databaseOverride.trim()
      : env && env.trim()
        ? env.trim()
        : DEFAULT_COLUMNS_QS,
  );
}

/** Проверить только presentation-параметры живой вкладки, игнорируя прочий URL. */
export function adsManagerUrlUsesColumnsQs(
  url: string,
  databaseOverride?: string | null,
): boolean {
  try {
    const parsed = new URL(url);
    const expected = new URLSearchParams(
      adsManagerColumnsQs(databaseOverride),
    );
    for (const key of ALLOWED_COLUMNS_QUERY_KEYS) {
      const currentValues = parsed.searchParams.getAll(key);
      const expectedValues = expected.getAll(key);
      if (
        currentValues.length !== expectedValues.length ||
        currentValues.some(
          (value, index) =>
            !value || value.length > 8_192 || value !== expectedValues[index],
        )
      ) {
        return false;
      }
    }
    return true;
  } catch {
    return false;
  }
}
