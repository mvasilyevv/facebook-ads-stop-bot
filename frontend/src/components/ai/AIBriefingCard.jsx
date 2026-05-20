import React, { useState, useEffect } from 'react';
import { getAIAnalysis } from '../../api';
import { renderMarkdown } from '../../utils/markdown';

/**
 * Карточка глобального AI брифинга с поддержкой кэширования и принудительного обновления.
 */
export default function AIBriefingCard() {
  const [loading, setLoading] = useState(false);
  const [content, setContent] = useState('');
  const [cachedAt, setCachedAt] = useState(null);
  const [expiresAt, setExpiresAt] = useState(null);
  const [warning, setWarning] = useState(null);
  const [error, setError] = useState(null);

  const fetchBriefing = async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAIAnalysis('briefing', 'global', force);
      setContent(data.content);
      setCachedAt(data.cached_at);
      setExpiresAt(data.expires_at);
      setWarning(data.warning);
    } catch (err) {
      console.error(err);
      setError('Не удалось загрузить AI брифинг. Пожалуйста, попробуйте позже.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchBriefing(false);
  }, []);

  // Форматирование даты
  const formatDate = (isoString) => {
    if (!isoString) return '';
    const date = new Date(isoString);
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  return (
    <div className="relative overflow-hidden rounded-md border border-border bg-surface p-md transition-all hover:shadow-[0_0_15px_rgba(255,107,0,0.05)]">
      {/* Шапка карточки */}
      <div className="flex items-center justify-between border-b border-border pb-sm mb-md">
        <div className="flex items-center gap-xs">
          <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
          <span className="font-mono text-2xs uppercase tracking-wider text-text">
            Global AI Briefing
          </span>
        </div>

        <div className="flex items-center gap-md">
          {cachedAt && (
            <span className="font-mono text-[10px] text-text-dim">
              Кэш от: {formatDate(cachedAt)}
            </span>
          )}
          <button
            onClick={() => fetchBriefing(true)}
            disabled={loading}
            className="flex items-center gap-xs rounded bg-accent px-xs py-2xs font-mono text-[10px] text-bg font-semibold transition hover:bg-opacity-90 disabled:opacity-50"
          >
            {loading ? 'Генерация...' : 'Обновить брифинг ✦'}
          </button>
        </div>
      </div>

      {/* Оповещение о предупреждении */}
      {warning && (
        <div className="mb-md rounded border border-warn/30 bg-warn/10 p-sm text-2xs text-warn font-mono">
          {warning}
        </div>
      )}

      {/* Ошибка */}
      {error && (
        <div className="mb-md rounded border border-stop/30 bg-stop/10 p-sm text-2xs text-stop font-mono">
          {error}
        </div>
      )}

      {/* Содержимое брифинга */}
      {loading ? (
        <div className="flex h-32 flex-col items-center justify-center gap-sm">
          <span className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          <span className="font-mono text-2xs text-text-dim">Сбор данных и генерация AI сводки...</span>
        </div>
      ) : content ? (
        <div 
          className="prose prose-invert max-w-none text-xs text-text-dim leading-relaxed whitespace-pre-wrap font-sans"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
        />
      ) : (
        <div className="flex h-24 items-center justify-center font-mono text-2xs text-text-muted">
          Нажмите кнопку выше, чтобы сгенерировать AI-сводку
        </div>
      )}
    </div>
  );
}
