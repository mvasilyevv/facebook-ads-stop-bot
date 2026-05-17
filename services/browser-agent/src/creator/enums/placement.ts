import type { LabelMap } from './index.js';

export const Placement = {
  ADVANTAGE_PLUS: 'ADVANTAGE_PLUS',
  MANUAL: 'MANUAL',
} as const;
export type Placement = (typeof Placement)[keyof typeof Placement];

export const placementLabels: LabelMap<Placement> = {
  ADVANTAGE_PLUS: {
    ru: ['Advantage+ плейсменты', 'Advantage+'],
    en: ['Advantage+ placements'],
  },
  MANUAL: { ru: ['Ручные плейсменты'], en: ['Manual placements'] },
};
