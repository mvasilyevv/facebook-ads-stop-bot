/**
 * Хук для сортировки таблиц + SortableHeader компонент.
 * Заменяет дублирующуюся логику в 6+ файлах.
 */
import { useState, useCallback } from 'react';

/**
 * Хук для управления состоянием сортировки таблицы.
 * @param {string} defaultKey — колонка по умолчанию
 * @param {string} [defaultDir='desc'] — направление по умолчанию
 * @returns {{ sortKey, sortDir, handleSort, sortRows }}
 */
export function useTableSort(defaultKey, defaultDir = 'desc') {
  const [state, setState] = useState({ key: defaultKey, dir: defaultDir });

  const handleSort = useCallback((key) => {
    setState((prev) => {
      if (prev.key === key) {
        return { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' };
      }
      return { key, dir: 'desc' };
    });
  }, []);

  return { sortKey: state.key, sortDir: state.dir, handleSort };
}

/**
 * Сортирует массив по ключу с учётом текстовых и числовых полей.
 * @param {Array} data — массив объектов
 * @param {string} sortKey — ключ сортировки
 * @param {string} sortDir — 'asc' | 'desc'
 * @param {Set|Array} [textKeys=[]] — колонки с текстовыми значениями
 * @returns {Array}
 */
export function sortRows(data, sortKey, sortDir, textKeys = []) {
  const textSet = textKeys instanceof Set ? textKeys : new Set(textKeys);
  return [...data].sort((a, b) => {
    if (textSet.has(sortKey)) {
      const av = String(a[sortKey] ?? '');
      const bv = String(b[sortKey] ?? '');
      return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    }
    const av = Number(a[sortKey] ?? 0) || 0;
    const bv = Number(b[sortKey] ?? 0) || 0;
    return sortDir === 'asc' ? av - bv : bv - av;
  });
}
