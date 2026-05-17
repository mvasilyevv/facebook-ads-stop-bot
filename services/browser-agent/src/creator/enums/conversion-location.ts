import type { LabelMap } from './index.js';

export const ConversionLocation = {
  WEBSITE: 'WEBSITE',
  WEBSITE_AND_CALLS: 'WEBSITE_AND_CALLS',
  APP: 'APP',
  MESSENGER: 'MESSENGER',
} as const;
export type ConversionLocation = (typeof ConversionLocation)[keyof typeof ConversionLocation];

export const conversionLocationLabels: LabelMap<ConversionLocation> = {
  WEBSITE: { ru: ['Сайт', 'Веб-сайт'], en: ['Website', 'Web site'] },
  WEBSITE_AND_CALLS: { ru: ['Сайт и звонки'], en: ['Website and calls'] },
  APP: { ru: ['Приложение'], en: ['App'] },
  MESSENGER: { ru: ['Messenger'], en: ['Messenger'] },
};
