import React, { useState } from 'react';
import { getAIAnalysis } from '../../api';

/**
 * Кнопка панели "✦ Анализ" для Офферов или Алертов, открывающая оверлей с AI-аналитикой.
 */
export default function AIPanelButton({ blockType, title = 'Анализ панели' }) {
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [content, setContent] = useState('');
  const [cachedAt, setCachedAt] = useState(null);
  const [warning, setWarning] = useState(null);
  const [error, setError] = useState(null);

  const fetchAnalysis = async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAIAnalysis(blockType, 'global', force);
      setContent(data.content);
      setCachedAt(data.cached_at);
      setWarning(data.warning);
    } catch (err) {
      console.error(err);
      setError('Не удалось загрузить AI анализ панели.');
    } finally {
      setLoading(false);
    }
  };

  const handleOpen = () => {
    setIsOpen(true);
    fetchAnalysis(false);
  };

  const handleClose = () => {
    setIsOpen(false);
  };

  const formatDate = (isoString) => {
    if (!isoString) return '';
    const date = new Date(isoString);
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  return (
    <>
      {/* Кнопка активации */}
      <button
        onClick={handleOpen}
        className="flex items-center gap-xs rounded border border-accent bg-accent-soft px-sm py-xs font-mono text-2xs font-semibold text-accent transition hover:bg-accent hover:text-bg"
      >
        ✦ Анализ
      </button>

      {/* Модальное окно (Оверлей) */}
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 backdrop-blur-sm p-md">
          <div className="relative flex h-full max-h-[80vh] w-full max-w-2xl flex-col rounded-md border border-border bg-surface shadow-2xl">
            {/* Шапка модального окна */}
            <div className="flex items-center justify-between border-b border-border p-md">
              <div className="flex items-center gap-sm">
                <span className="h-2 w-2 rounded-full bg-accent" />
                <span className="font-mono text-xs font-bold uppercase tracking-wider text-text">
                  AI {title}
                </span>
                {cachedAt && (
                  <span className="font-mono text-[10px] text-text-dim">
                    (Кэш от: {formatDate(cachedAt)})
                  </span>
                )}
              </div>
              <button
                onClick={handleClose}
                className="font-mono text-xs text-text-dim transition hover:text-text"
              >
                [Закрыть]
              </button>
            </div>

            {/* Контентная часть */}
            <div className="flex-1 overflow-y-auto p-md font-sans">
              {warning && (
                <div className="mb-md rounded border border-warn/30 bg-warn/10 p-sm text-2xs text-warn font-mono">
                  {warning}
                </div>
              )}

              {error && (
                <div className="mb-md rounded border border-stop/30 bg-stop/10 p-sm text-2xs text-stop font-mono">
                  {error}
                </div>
              )}

              {loading ? (
                <div className="flex h-48 flex-col items-center justify-center gap-sm">
                  <span className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
                  <span className="font-mono text-2xs text-text-dim">Сбор контекста панели и анализ...</span>
                </div>
              ) : (
                <div className="prose prose-invert max-w-none text-xs text-text-dim leading-relaxed whitespace-pre-wrap">
                  {content}
                </div>
              )}
            </div>

            {/* Подвал модального окна */}
            <div className="flex items-center justify-between border-t border-border p-sm bg-surface-2">
              <span className="font-mono text-[9px] text-text-muted">
                Neo Control Room AI Assistant
              </span>
              <button
                onClick={() => fetchAnalysis(true)}
                disabled={loading}
                className="rounded bg-accent px-xs py-2xs font-mono text-[10px] text-bg font-semibold transition hover:bg-opacity-90 disabled:opacity-50"
              >
                Обновить ✦
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
