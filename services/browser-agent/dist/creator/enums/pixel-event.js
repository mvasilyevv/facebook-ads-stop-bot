"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.pixelEventLabels = exports.PixelEvent = void 0;
exports.PixelEvent = {
    PURCHASE: 'PURCHASE',
    LEAD: 'LEAD',
    COMPLETE_REGISTRATION: 'COMPLETE_REGISTRATION',
    ADD_TO_CART: 'ADD_TO_CART',
    INITIATE_CHECKOUT: 'INITIATE_CHECKOUT',
    SUBSCRIBE: 'SUBSCRIBE',
    ADD_PAYMENT_INFO: 'ADD_PAYMENT_INFO',
    CONTACT: 'CONTACT',
    SEARCH: 'SEARCH',
    VIEW_CONTENT: 'VIEW_CONTENT',
};
exports.pixelEventLabels = {
    PURCHASE: { ru: ['Покупка'], en: ['Purchase'] },
    LEAD: { ru: ['Лид'], en: ['Lead'] },
    COMPLETE_REGISTRATION: {
        ru: ['Завершённая регистрация', 'Регистрация'],
        en: ['Complete registration', 'Completed registration'],
    },
    ADD_TO_CART: { ru: ['Добавление в корзину'], en: ['Add to cart'] },
    INITIATE_CHECKOUT: { ru: ['Начало оформления'], en: ['Initiate checkout'] },
    SUBSCRIBE: { ru: ['Подписка'], en: ['Subscribe'] },
    ADD_PAYMENT_INFO: { ru: ['Добавление платёжной информации'], en: ['Add payment info'] },
    CONTACT: { ru: ['Контакт'], en: ['Contact'] },
    SEARCH: { ru: ['Поиск'], en: ['Search'] },
    VIEW_CONTENT: { ru: ['Просмотр контента'], en: ['View content'] },
};
//# sourceMappingURL=pixel-event.js.map