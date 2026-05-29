import React, { useState } from 'react';
import { getAIAnalysis } from '../../api';

/**
 * Небольшая инлайновая кнопка "✦" рядом со строкой алерта, открывающая модальный разбор конкретного инцидента.
 */
export default function AIInlineButton({ alertId }) {
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [content, setContent] = useState('');
  const [warning, setWarning] = useState(null);
  const [error, setError] = useState(null);

  const fetchInlineAnalysis = async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAIAnalysis('alert_inline', alertId, force);
      setContent(data.content);
      setWarning(data.warning);
    } catch (err) {
      console.error(err);
      setError('Не удалось загрузить разбор инцидента.');
    } finally {
      setLoading(false);
    }
  };

  const handleOpen = (e) => {
    e.stopPropagation(); // Не активируем клик по строке списка
    setIsOpen(true);
    fetchInlineAnalysis(false);
  };

  const handleClose = (e) => {
    e.stopPropagation();
    setIsOpen(false);
  };

  return (
    <>
      <button
        onClick={handleOpen}
        title="AI Разбор инцидента"
        className="inline-flex h-5 w-5 items-center justify-center rounded border border-accent/40 bg-accent-soft text-2xs font-semibold text-accent transition hover:border-accent hover:bg-accent hover:text-bg"
      >
        ✦
      </button>

      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/85 backdrop-blur-sm p-md">
          <div className="relative flex h-full max-h-[70vh] w-full max-w-lg flex-col rounded-md border border-border bg-surface shadow-2xl">
            {/* Шапка разбора */}
            <div className="flex items-center justify-between border-b border-border p-md">
              <span className="font-mono text-xs font-bold uppercase tracking-wider text-text">
                ✦ AI Разбор Инцидента
              </span>
              <button
                onClick={handleClose}
                className="font-mono text-xs text-text-dim transition hover:text-text"
              >
                [Закрыть]
              </button>
            </div>

            {/* Контент */}
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
                <div className="flex h-36 flex-col items-center justify-center gap-sm">
                  <span className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
                  <span className="font-mono text-2xs text-text-dim">Анализируем причину инцидента...</span>
                </div>
              ) : (
                <div className="prose prose-invert max-w-none text-xs text-text-dim leading-relaxed whitespace-pre-wrap">
                  {content}
                </div>
              )}
            </div>

            {/* Подвал */}
            <div className="flex justify-end gap-sm border-t border-border p-sm bg-surface-2">
              <button
                onClick={() => fetchInlineAnalysis(true)}
                disabled={loading}
                className="rounded bg-accent px-xs py-2xs font-mono text-[9px] text-bg font-semibold transition hover:bg-opacity-90 disabled:opacity-50"
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
