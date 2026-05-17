import type { LabelMap } from './index.js';

export const OptimizationGoal = {
  CONVERSIONS: 'CONVERSIONS',
  LANDING_PAGE_VIEWS: 'LANDING_PAGE_VIEWS',
  LINK_CLICKS: 'LINK_CLICKS',
  IMPRESSIONS: 'IMPRESSIONS',
  REACH: 'REACH',
  VALUE: 'VALUE',
} as const;
export type OptimizationGoal = (typeof OptimizationGoal)[keyof typeof OptimizationGoal];

export const optimizationGoalLabels: LabelMap<OptimizationGoal> = {
  CONVERSIONS: { ru: ['Конверсии'], en: ['Conversions'] },
  LANDING_PAGE_VIEWS: { ru: ['Просмотры целевой страницы'], en: ['Landing page views'] },
  LINK_CLICKS: { ru: ['Клики по ссылке'], en: ['Link clicks'] },
  IMPRESSIONS: { ru: ['Показы'], en: ['Impressions'] },
  REACH: { ru: ['Охват'], en: ['Reach'] },
  VALUE: { ru: ['Ценность'], en: ['Value'] },
};
