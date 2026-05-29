import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { triggerScanNow } from '../api.js';

const NAV_COMMANDS = [
  { id: 'nav-dashboard', label: 'Дашборд', hint: 'Мониторинг', page: 'dashboard' },
  { id: 'nav-ads', label: 'Объявления', hint: 'Список объявлений', page: 'ads' },
  { id: 'nav-offers', label: 'Офферы', hint: 'Правила и офферы', page: 'offers' },
  { id: 'nav-settings', label: 'Настройки', hint: 'Конфигурация системы', page: 'settings' },
];

/** Палитра команд: навигация и быстрые действия (⌘K / Ctrl+K) */
export function CommandPalette({ open, onClose, onNavigate }) {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const [scanPending, setScanPending] = useState(false);
  const [scanError, setScanError] = useState(null);
  const inputRef = useRef(null);

  const actionCommands = useMemo(
    () => [
      {
        id: 'action-scan',
        label: 'Обновить скан',
        hint: 'Немедленный цикл observer',
        keywords: ['скан', 'обновить', 'refresh'],
        run: async () => {
          setScanPending(true);
          setScanError(null);
          try {
            await triggerScanNow();
            onClose();
          } catch (err) {
            setScanError(err?.message || 'Не удалось запустить скан');
          } finally {
            setScanPending(false);
          }
        },
        disabled: scanPending,
      },
    ],
    [onClose, scanPending],
  );

  const allCommands = useMemo(() => {
    const nav = NAV_COMMANDS.map((cmd) => ({
      ...cmd,
      group: 'Навигация',
      run: () => {
        onNavigate?.(cmd.page);
        onClose();
      },
    }));
    const actions = actionCommands.map((cmd) => ({
      ...cmd,
      group: 'Действия',
    }));
    return [...nav, ...actions];
  }, [actionCommands, onClose, onNavigate]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allCommands;
    return allCommands.filter((cmd) => {
      const haystack = `${cmd.label} ${cmd.hint || ''} ${(cmd.keywords || []).join(' ')}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [allCommands, query]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActiveIndex(0);
      setScanError(null);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  const runActive = useCallback(() => {
    const cmd = filtered[activeIndex];
    if (cmd && !cmd.disabled) cmd.run();
  }, [activeIndex, filtered]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIndex((i) => Math.min(i + 1, Math.max(0, filtered.length - 1)));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        runActive();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, filtered.length, onClose, runActive]);

  if (!open) return null;

  let lastGroup = null;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center bg-black/60 px-4 pt-[12vh]"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-lg border border-border bg-surface shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-label="Палитра команд"
      >
        <div className="border-b border-border px-4 py-3">
          <input
            ref={inputRef}
            type="search"
            className="w-full bg-transparent text-sm text-primary outline-none placeholder:text-muted"
            placeholder="Команда или страница…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Поиск команд"
          />
          <p className="mt-1 text-2xs text-muted">↑↓ выбор · Enter выполнить · Esc закрыть</p>
        </div>

        <ul className="max-h-72 overflow-y-auto py-1" role="listbox">
          {filtered.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-muted">Ничего не найдено</li>
          )}
          {filtered.map((cmd, index) => {
            const showGroup = cmd.group !== lastGroup;
            lastGroup = cmd.group;
            const isActive = index === activeIndex;
            return (
              <li key={cmd.id} role="presentation">
                {showGroup && (
                  <p className="px-4 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-widest text-muted/60">
                    {cmd.group}
                  </p>
                )}
                <button
                  type="button"
                  role="option"
                  aria-selected={isActive}
                  disabled={cmd.disabled}
                  className={`flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm transition-colors ${
                    isActive ? 'bg-accent-muted text-accent' : 'text-primary hover:bg-elevated'
                  } disabled:opacity-50`}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => !cmd.disabled && cmd.run()}
                >
                  <span className="font-medium">{cmd.label}</span>
                  <span className="text-2xs text-muted">{cmd.hint}</span>
                </button>
              </li>
            );
          })}
        </ul>

        {scanError && (
          <p className="border-t border-danger/20 bg-danger-muted px-4 py-2 text-2xs text-danger">
            {scanError}
          </p>
        )}
      </div>
    </div>
  );
}

/** Глобальный хоткей ⌘K / Ctrl+K */
export function useCommandPaletteHotkey(onOpen) {
  useEffect(() => {
    const onKeyDown = (e) => {
      const isK = e.key === 'k' || e.key === 'K';
      if (!isK || (!e.metaKey && !e.ctrlKey)) return;
      const tag = e.target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target?.isContentEditable) {
        return;
      }
      e.preventDefault();
      onOpen();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onOpen]);
}
