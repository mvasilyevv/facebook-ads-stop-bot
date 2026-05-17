import type { LabelMap } from './index.js';

export const AttributionWindow = {
  CLICK_1D: 'CLICK_1D',
  CLICK_7D: 'CLICK_7D',
  CLICK_7D_VIEW_1D: 'CLICK_7D_VIEW_1D',
  CLICK_1D_VIEW_1D: 'CLICK_1D_VIEW_1D',
} as const;
export type AttributionWindow = (typeof AttributionWindow)[keyof typeof AttributionWindow];

export const attributionLabels: LabelMap<AttributionWindow> = {
  CLICK_1D: { ru: ['Клик 1 день', '1 день после клика'], en: ['1-day click'] },
  CLICK_7D: { ru: ['Клик 7 дней', '7 дней после клика'], en: ['7-day click'] },
  CLICK_7D_VIEW_1D: {
    ru: ['Клик 7 дней или просмотр 1 день'],
    en: ['7-day click or 1-day view'],
  },
  CLICK_1D_VIEW_1D: {
    ru: ['Клик 1 день или просмотр 1 день'],
    en: ['1-day click or 1-day view'],
  },
};
