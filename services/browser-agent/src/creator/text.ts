// Нормализация текста для матчинга лейблов (lowercase, trim, схлопывание пробелов,
// удаление невидимых zero-width символов).
const INVISIBLE_RE = /[​-‏‪-‮⁠-⁯﻿]/g;

export function normalizeText(input: string): string {
  return input
    .replace(INVISIBLE_RE, '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, ' ');
}
