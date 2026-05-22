// Чистая функция: по фактам о DOM Ads Manager решает причину пустого скана.
// Факты собирает caller через page.evaluate — есть ли хедер таблицы и видны ли чипы фильтра.

export type EmptyReason = 'table_not_found' | 'filter_excludes_all' | 'no_active_ads';

export interface EmptyReasonInput {
  hasTableHeader: boolean;
  hasFilterChips: boolean;
  rowCount: number;
}

export function detectEmptyReason(input: EmptyReasonInput): EmptyReason | null {
  if (input.rowCount > 0) {
    return null;
  }
  if (!input.hasTableHeader) {
    return 'table_not_found';
  }
  if (input.hasFilterChips) {
    return 'filter_excludes_all';
  }
  return 'no_active_ads';
}
