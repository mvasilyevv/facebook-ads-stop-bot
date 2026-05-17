import type { LabelMap } from './index.js';

export const CallToAction = {
  LEARN_MORE: 'LEARN_MORE',
  SIGN_UP: 'SIGN_UP',
  SHOP_NOW: 'SHOP_NOW',
  SUBSCRIBE: 'SUBSCRIBE',
  GET_OFFER: 'GET_OFFER',
  BOOK_TRAVEL: 'BOOK_TRAVEL',
  DOWNLOAD: 'DOWNLOAD',
  CONTACT_US: 'CONTACT_US',
  APPLY_NOW: 'APPLY_NOW',
} as const;
export type CallToAction = (typeof CallToAction)[keyof typeof CallToAction];

export const ctaLabels: LabelMap<CallToAction> = {
  LEARN_MORE: { ru: ['Подробнее'], en: ['Learn more'] },
  SIGN_UP: { ru: ['Зарегистрироваться'], en: ['Sign up'] },
  SHOP_NOW: { ru: ['В магазин'], en: ['Shop now'] },
  SUBSCRIBE: { ru: ['Подписаться'], en: ['Subscribe'] },
  GET_OFFER: { ru: ['Получить предложение'], en: ['Get offer'] },
  BOOK_TRAVEL: { ru: ['Забронировать'], en: ['Book travel', 'Book now'] },
  DOWNLOAD: { ru: ['Скачать'], en: ['Download'] },
  CONTACT_US: { ru: ['Связаться с нами'], en: ['Contact us'] },
  APPLY_NOW: { ru: ['Подать заявку'], en: ['Apply now'] },
};
