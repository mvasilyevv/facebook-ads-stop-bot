/**
 * Безопасный рендеринг расширенного Markdown в HTML в стиле Neo Control Room.
 *
 * Поддерживает: H1–H3, **жирный**, *курсив*, инлайн `код`, маркированные и
 * нумерованные списки, блок-цитаты `> ...`, ссылки `[text](url)`, GFM-таблицы.
 *
 * Экранирует HTML до парсинга — безопасно для dangerouslySetInnerHTML.
 *
 * @param {string} text - Исходный текст в формате Markdown
 * @param {{ theme?: 'desktop' | 'tg' }} [opts] - Тема цветов (desktop по умолчанию)
 * @returns {string} Безопасный HTML
 */
export function renderMarkdown(text, opts = {}) {
  if (!text) return '';

  const theme = opts.theme === 'tg' ? TG_THEME : DESKTOP_THEME;

  // Нормализация переносов строк
  let src = String(text).replace(/\r\n/g, '\n').replace(/\r/g, '\n');

  // Глобальный XSS-escape до любого парсинга
  src = src.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const lines = src.split('\n');
  const out = [];
  let i = 0;

  const flushParagraph = (buf) => {
    if (!buf.length) return;
    const joined = buf.join(' ').trim();
    if (!joined) return;
    out.push(`<p style="${theme.p}">${inline(joined, theme)}</p>`);
  };

  let paragraph = [];

  while (i < lines.length) {
    const line = lines[i];

    // Пустая строка завершает абзац
    if (!line.trim()) {
      flushParagraph(paragraph);
      paragraph = [];
      i += 1;
      continue;
    }

    // GFM-таблица: текущая строка | разделительная |---|
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
      flushParagraph(paragraph);
      paragraph = [];
      const headerCells = splitRow(line);
      const aligns = splitRow(lines[i + 1]).map(parseAlign);
      const bodyRows = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        bodyRows.push(splitRow(lines[i]));
        i += 1;
      }
      out.push(renderTable(headerCells, aligns, bodyRows, theme));
      continue;
    }

    // Заголовки
    const h = line.match(/^(#{1,3})\s+(.*?)\s*$/);
    if (h) {
      flushParagraph(paragraph);
      paragraph = [];
      const level = h[1].length;
      const style = level === 1 ? theme.h1 : level === 2 ? theme.h2 : theme.h3;
      out.push(`<h${level} style="${style}">${inline(h[2], theme)}</h${level}>`);
      i += 1;
      continue;
    }

    // Блок-цитаты
    if (/^\s*&gt;\s?/.test(line)) {
      flushParagraph(paragraph);
      paragraph = [];
      const quote = [];
      while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*&gt;\s?/, ''));
        i += 1;
      }
      out.push(`<blockquote style="${theme.blockquote}">${inline(quote.join(' '), theme)}</blockquote>`);
      continue;
    }

    // Маркированный список
    if (/^\s*[-*]\s+/.test(line)) {
      flushParagraph(paragraph);
      paragraph = [];
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''));
        i += 1;
      }
      out.push(
        `<ul style="${theme.ul}">${items.map((it) => `<li style="${theme.li}"><span style="${theme.bullet}">•</span><span>${inline(it, theme)}</span></li>`).join('')}</ul>`
      );
      continue;
    }

    // Нумерованный список
    if (/^\s*\d+\.\s+/.test(line)) {
      flushParagraph(paragraph);
      paragraph = [];
      const items = [];
      let n = 1;
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        const m = lines[i].match(/^\s*(\d+)\.\s+(.*)$/);
        items.push({ idx: m ? m[1] : String(n), text: m ? m[2] : lines[i] });
        n += 1;
        i += 1;
      }
      out.push(
        `<ol style="${theme.ol}">${items.map((it) => `<li style="${theme.li}"><span style="${theme.olIdx}">${it.idx}.</span><span>${inline(it.text, theme)}</span></li>`).join('')}</ol>`
      );
      continue;
    }

    paragraph.push(line);
    i += 1;
  }

  flushParagraph(paragraph);

  return out.join('');
}

function splitRow(line) {
  const trimmed = line.trim().replace(/^\||\|$/g, '');
  return trimmed.split('|').map((c) => c.trim());
}

function parseAlign(sep) {
  const s = sep.trim();
  const left = s.startsWith(':');
  const right = s.endsWith(':');
  if (left && right) return 'center';
  if (right) return 'right';
  return 'left';
}

function renderTable(header, aligns, rows, theme) {
  const th = header
    .map((c, idx) => `<th style="${theme.th}; text-align:${aligns[idx] || 'left'}">${inline(c, theme)}</th>`)
    .join('');
  const tr = rows
    .map(
      (row) =>
        `<tr>${row
          .map((c, idx) => `<td style="${theme.td}; text-align:${aligns[idx] || 'left'}">${inline(c, theme)}</td>`)
          .join('')}</tr>`
    )
    .join('');
  return `<div style="${theme.tableWrap}"><table style="${theme.table}"><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table></div>`;
}

function inline(text, theme) {
  // Порядок важен: код → ссылки → жирный → курсив
  let s = text;
  s = s.replace(/`([^`]+)`/g, (_, c) => `<code style="${theme.code}">${c}</code>`);
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
    const safeUrl = /^(https?:|mailto:|\/|#)/i.test(url) ? url : '#';
    return `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" style="${theme.a}">${label}</a>`;
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, (_, c) => `<strong style="${theme.strong}">${c}</strong>`);
  s = s.replace(/(^|[\s(])\*([^*\n]+)\*/g, (_, pre, c) => `${pre}<em style="${theme.em}">${c}</em>`);
  return s;
}

// Тема для основной (desktop) фронт-панели: Neo Control Room
const DESKTOP_THEME = {
  p: 'margin: 6px 0; line-height: 1.5;',
  h1: 'font-size: 16px; font-weight: 800; margin: 16px 0 8px; color: var(--accent, #FF6B00); letter-spacing: 0.02em;',
  h2: 'font-size: 14px; font-weight: 700; margin: 14px 0 6px; color: var(--accent, #FF6B00); letter-spacing: 0.02em; text-transform: uppercase; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;',
  h3: 'font-size: 12px; font-weight: 700; margin: 12px 0 4px; color: var(--accent, #FF6B00); text-transform: uppercase; letter-spacing: 0.04em; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;',
  ul: 'margin: 6px 0; padding: 0; list-style: none;',
  ol: 'margin: 6px 0; padding: 0; list-style: none;',
  li: 'display: flex; align-items: flex-start; gap: 6px; margin-left: 4px; margin-bottom: 3px;',
  bullet: 'color: var(--accent, #FF6B00); flex-shrink: 0;',
  olIdx: 'color: var(--accent, #FF6B00); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 700; flex-shrink: 0; min-width: 18px;',
  blockquote:
    'margin: 8px 0; padding: 6px 10px; border-left: 2px solid var(--accent, #FF6B00); background: rgba(255,107,0,0.06); color: var(--text, #E8EBEE); font-style: italic;',
  code: 'font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: rgba(255,255,255,0.06); padding: 1px 5px; border-radius: 3px; font-size: 11px; color: var(--info, #5CE6FF); border: 1px solid var(--border, rgba(255,255,255,0.1));',
  a: 'color: var(--info, #5CE6FF); text-decoration: underline; text-underline-offset: 2px;',
  strong: 'font-weight: 700; color: var(--text, #E8EBEE);',
  em: 'font-style: italic; color: var(--text-dim, #B8BDC4);',
  tableWrap: 'overflow-x: auto; margin: 10px 0; border: 1px solid var(--border, rgba(255,255,255,0.1)); border-radius: 4px;',
  table:
    'width: 100%; border-collapse: collapse; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px;',
  th: 'padding: 6px 8px; background: var(--surface-2, rgba(255,255,255,0.04)); color: var(--text, #E8EBEE); font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; border-bottom: 1px solid var(--border, rgba(255,255,255,0.1)); font-size: 10px;',
  td: 'padding: 5px 8px; border-bottom: 1px solid var(--border, rgba(255,255,255,0.06)); color: var(--text-dim, #B8BDC4);',
};

// Тема для Telegram Mini App: использует tg-токены из WebApp
const TG_THEME = {
  p: 'margin: 6px 0; line-height: 1.5;',
  h1: 'font-size: 16px; font-weight: 800; margin: 16px 0 8px; color: var(--tg-link-color, #FF6B00);',
  h2: 'font-size: 15px; font-weight: 700; margin: 14px 0 6px; color: var(--tg-link-color, #FF6B00);',
  h3: 'font-size: 14px; font-weight: 700; margin: 12px 0 4px; color: var(--tg-link-color, #FF6B00);',
  ul: 'margin: 6px 0; padding: 0; list-style: none;',
  ol: 'margin: 6px 0; padding: 0; list-style: none;',
  li: 'display: flex; align-items: flex-start; gap: 6px; margin-left: 4px; margin-bottom: 3px;',
  bullet: 'color: var(--tg-link-color, #FF6B00); flex-shrink: 0;',
  olIdx: 'color: var(--tg-link-color, #FF6B00); font-weight: 700; flex-shrink: 0; min-width: 18px;',
  blockquote:
    'margin: 8px 0; padding: 6px 10px; border-left: 2px solid var(--tg-link-color, #FF6B00); background: rgba(255,107,0,0.08); color: var(--tg-text-color, #E8EBEE); font-style: italic;',
  code: 'font-family: monospace; background: rgba(255,255,255,0.06); padding: 2px 5px; border-radius: 4px; font-size: 12px; color: var(--color-info, #5CE6FF); border: 1px solid rgba(255,255,255,0.08); word-break: break-all;',
  a: 'color: var(--tg-link-color, #5CE6FF); text-decoration: underline;',
  strong: 'font-weight: 700; color: var(--tg-text-color, #E8EBEE);',
  em: 'font-style: italic; color: var(--tg-hint-color, #B8BDC4);',
  tableWrap: 'overflow-x: auto; margin: 10px 0; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px;',
  table: 'width: 100%; border-collapse: collapse; font-size: 12px;',
  th: 'padding: 6px 8px; background: rgba(255,255,255,0.04); color: var(--tg-text-color, #E8EBEE); font-weight: 700; border-bottom: 1px solid rgba(255,255,255,0.1); font-size: 11px;',
  td: 'padding: 5px 8px; border-bottom: 1px solid rgba(255,255,255,0.06); color: var(--tg-hint-color, #B8BDC4);',
};
