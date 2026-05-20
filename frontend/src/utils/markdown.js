/**
 * Простая утилита для безопасного рендеринга базового Markdown в HTML.
 * Предотвращает XSS-атаки с помощью экранирования спецсимволов.
 *
 * @param {string} text - Исходный текст в формате Markdown
 * @returns {string} Безопасный HTML-код для вставки через dangerouslySetInnerHTML
 */
export function renderMarkdown(text) {
  if (!text) return '';

  // Экранирование HTML тегов для предотвращения XSS
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Парсинг заголовков ### (h3)
  html = html.replace(/^###\s+(.+)$/gm, '<h3 style="font-size: 14px; font-weight: 700; margin-top: 14px; margin-bottom: 6px; color: var(--accent);">$1</h3>');
  
  // Парсинг заголовков ## (h2)
  html = html.replace(/^##\s+(.+)$/gm, '<h2 style="font-size: 15px; font-weight: 700; margin-top: 16px; margin-bottom: 8px; color: var(--accent);">$1</h2>');

  // Парсинг заголовков # (h1)
  html = html.replace(/^#\s+(.+)$/gm, '<h1 style="font-size: 16px; font-weight: 800; margin-top: 18px; margin-bottom: 10px; color: var(--accent);">$1</h1>');

  // Парсинг жирного текста **текст**
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong style="font-weight: 700; color: var(--text);">$1</strong>');

  // Парсинг списков "- элемент" или "* элемент"
  html = html.replace(/^\s*[-*]\s+(.+)$/gm, '<div style="display: flex; align-items: flex-start; gap: 6px; margin-left: 4px; margin-bottom: 4px;"><span style="color: var(--accent);">•</span><span>$1</span></div>');

  // Парсинг инлайн-кода `код`
  html = html.replace(/`(.*?)`/g, '<code style="font-family: monospace; background: rgba(255,255,255,0.06); padding: 2px 5px; border-radius: 4px; font-size: 12px; color: var(--info); border: 1px solid rgba(255,255,255,0.08); word-break: break-all;">$1</code>');

  return html;
}
