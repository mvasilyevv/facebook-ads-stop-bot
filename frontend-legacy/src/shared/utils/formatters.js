/**
 * Общие форматтеры для отображения метрик.
 * Импортировать вместо локальных fmt$, fmtN, fmtRoas.
 */

/** Форматирует денежную сумму: "$12.34" или "---" */
export function fmt$(v) {
  if (v == null || v === '') return '---';
  return '$' + Number(v).toFixed(2);
}

/** Форматирует целое число или "---" */
export function fmtN(v) {
  if (v == null || v === '') return '---';
  return String(Number(v));
}

/** Форматирует ROAS: "1.23x" или "---" */
export function fmtRoas(v) {
  if (v == null || Number(v) <= 0) return '---';
  return Number(v).toFixed(2) + 'x';
}

/** Форматирует Decimal с точностью: "$1.2345" или "---" */
export function fmtDecimal(v, digits = 2) {
  if (v == null || v === '') return '---';
  const num = Number(v);
  if (!Number.isFinite(num)) return '---';
  return num.toFixed(digits);
}

/** Форматирует Decimal с $ и точностью */
export function fmt$precise(v, digits = 2) {
  if (v == null || v === '') return '---';
  const num = Number(v);
  if (!Number.isFinite(num)) return '---';
  return '$' + num.toFixed(digits);
}
