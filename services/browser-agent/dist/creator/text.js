"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.normalizeText = normalizeText;
// Нормализация текста для матчинга лейблов (lowercase, trim, схлопывание пробелов,
// удаление невидимых zero-width символов).
const INVISIBLE_RE = /[​-‏‪-‮⁠-⁯﻿]/g;
function normalizeText(input) {
    return input
        .replace(INVISIBLE_RE, '')
        .toLowerCase()
        .trim()
        .replace(/\s+/g, ' ');
}
//# sourceMappingURL=text.js.map