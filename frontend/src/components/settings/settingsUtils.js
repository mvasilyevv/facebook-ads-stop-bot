export const STOP_RANGE_MARKS = [40, 60, 80, 100];
export const WARNING_RANGE_MARKS = [50, 65, 80, 100];
export const OBSERVER_STEP_CONFIGS = [
  {
    id: 'cpc',
    ordinal: 'Шаг 1',
    title: 'Клик',
    code: 'CPC',
    description: 'Ранняя ступень: сам CPC и расход без кликов.',
    stopKey: 'cpc_stop_percent_of_base',
    warningKey: 'cpc_warning_percent_of_stop',
  },
  {
    id: 'cpl',
    ordinal: 'Шаг 2',
    title: 'Лид',
    code: 'CPL',
    description: 'Средняя ступень: CPL и расход до первого лида.',
    stopKey: 'cpl_stop_percent_of_base',
    warningKey: 'cpl_warning_percent_of_stop',
  },
  {
    id: 'cpr',
    ordinal: 'Шаг 3',
    title: 'Регистрация',
    code: 'CPR',
    description: 'Поздняя ступень: CPR, диапазон расхода и реги без депозита.',
    stopKey: 'cpr_stop_percent_of_base',
    warningKey: 'cpr_warning_percent_of_stop',
  },
];

export function clampStepValue(value, min, max, step) {
  const number = Number(value);
  if (!Number.isFinite(number)) return min;
  const clamped = Math.min(max, Math.max(min, number));
  return Math.round(clamped / step) * step;
}

export function getObserverStepThresholds(observer, stepConfig) {
  const stopPercent = clampStepValue(
    observer?.[stepConfig.stopKey] ?? observer?.stop_percent_of_base ?? 100,
    5,
    100,
    5,
  );
  const warningPercent = clampStepValue(
    observer?.[stepConfig.warningKey] ?? observer?.warning_percent_of_stop ?? 80,
    50,
    100,
    5,
  );
  return {
    stopPercent,
    warningPercent,
    stopShiftPercent: 100 - stopPercent,
  };
}

export function roundMoney(val) {
  const num = Number(val);
  if (!Number.isFinite(num)) return 0;
  return Math.round((num + Number.EPSILON) * 100) / 100;
}

export function fmtMoney(val) {
  if (val == null || Number.isNaN(Number(val))) return '—';
  return `$${Number(val).toFixed(2)}`;
}

export function normalizeBotUsername(value) {
  return String(value || '')
    .trim()
    .replace(/^@+/, '');
}

export function makeTelegramDeepLink(botUsername, code) {
  const username = normalizeBotUsername(botUsername);
  const token = String(code || '').trim();
  if (!username || !token) return '';
  return `https://t.me/${username}?start=${encodeURIComponent(token)}`;
}

export function formatDateTimeRu(value) {
  if (!value) return 'Не передан backend-ом';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('ru-RU', {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

export async function copyTextToClipboard(text) {
  const value = String(text || '').trim();
  if (!value) {
    throw new Error('Нечего копировать');
  }

  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }

  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  const ok = document.execCommand('copy');
  document.body.removeChild(textarea);
  if (!ok) {
    throw new Error('Не удалось скопировать текст');
  }
}

export function getTelegramPollerStatusMeta(status) {
  const normalized = String(status || 'OFFLINE').toUpperCase();
  if (normalized === 'ONLINE') {
    return { label: 'Онлайн', color: '#10B981' };
  }
  if (normalized === 'WAITING_AUTHORIZATION') {
    return { label: 'Ждёт авторизацию', color: '#F59E0B' };
  }
  if (normalized === 'WAITING_BOT_TOKEN') {
    return { label: 'Ждёт токен бота', color: '#F59E0B' };
  }
  return { label: 'Оффлайн', color: '#EF4444' };
}
