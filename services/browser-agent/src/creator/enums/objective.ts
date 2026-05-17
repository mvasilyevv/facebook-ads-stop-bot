import type { LabelMap } from './index.js';

export const Objective = {
  SALES: 'SALES',
  LEADS: 'LEADS',
  ENGAGEMENT: 'ENGAGEMENT',
  TRAFFIC: 'TRAFFIC',
  AWARENESS: 'AWARENESS',
  APP_PROMOTION: 'APP_PROMOTION',
} as const;
export type Objective = (typeof Objective)[keyof typeof Objective];

export const objectiveLabels: LabelMap<Objective> = {
  SALES: { ru: ['Продажи'], en: ['Sales'] },
  LEADS: { ru: ['Лиды'], en: ['Leads'] },
  ENGAGEMENT: { ru: ['Вовлечённость'], en: ['Engagement'] },
  TRAFFIC: { ru: ['Трафик'], en: ['Traffic'] },
  AWARENESS: { ru: ['Узнаваемость'], en: ['Awareness'] },
  APP_PROMOTION: { ru: ['Продвижение приложения'], en: ['App promotion'] },
};
