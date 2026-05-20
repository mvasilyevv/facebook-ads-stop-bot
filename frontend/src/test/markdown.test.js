import { describe, it, expect } from 'vitest';
import { renderMarkdown } from '../utils/markdown';

describe('Тестирование утилиты renderMarkdown', () => {
  
  // Тест сценария: проверка экранирования спецсимволов HTML для предотвращения XSS
  it('должен безопасно экранировать HTML-теги для предотвращения XSS', () => {
    const input = '<script>alert("xss")</script> & <div>проверка</div>';
    const result = renderMarkdown(input);
    expect(result).not.toContain('<script>');
    expect(result).not.toContain('<div>');
    expect(result).toContain('&lt;script&gt;alert("xss")&lt;/script&gt; &amp; &lt;div&gt;проверка&lt;/div&gt;');
  });

  // Тест сценария: проверка правильного парсинга заголовков разного уровня (#, ##, ###)
  it('должен правильно рендерить заголовки #, ##, ### в HTML-теги со стилями', () => {
    const h1Input = '# Заголовок 1';
    const h2Input = '## Заголовок 2';
    const h3Input = '### Заголовок 3';

    expect(renderMarkdown(h1Input)).toContain('<h1 style="font-size: 16px; font-weight: 800; margin-top: 18px; margin-bottom: 10px; color: var(--accent);">Заголовок 1</h1>');
    expect(renderMarkdown(h2Input)).toContain('<h2 style="font-size: 15px; font-weight: 700; margin-top: 16px; margin-bottom: 8px; color: var(--accent);">Заголовок 2</h2>');
    expect(renderMarkdown(h3Input)).toContain('<h3 style="font-size: 14px; font-weight: 700; margin-top: 14px; margin-bottom: 6px; color: var(--accent);">Заголовок 3</h3>');
  });

  // Тест сценария: проверка форматирования жирного текста с помощью **
  it('должен форматировать жирный текст, заключенный в двойные звездочки', () => {
    const input = 'Это **жирный текст** в середине строки.';
    const result = renderMarkdown(input);
    expect(result).toContain('<strong style="font-weight: 700; color: var(--text);">жирный текст</strong>');
  });

  // Тест сценария: проверка форматирования маркированных списков
  it('должен корректно преобразовывать списки с дефисами или звездочками в HTML-структуру с буллитами', () => {
    const inputDash = '- Элемент списка один\n- Элемент списка два';
    const resultDash = renderMarkdown(inputDash);
    expect(resultDash).toContain('•');
    expect(resultDash).toContain('Элемент списка один');
    expect(resultDash).toContain('Элемент списка два');

    const inputStar = '* Элемент со звездочкой';
    const resultStar = renderMarkdown(inputStar);
    expect(resultStar).toContain('•');
    expect(resultStar).toContain('Элемент со звездочкой');
  });

  // Тест сценария: проверка форматирования встроенного моноширинного кода
  it('должен преобразовывать текст в обратных кавычках во встроенный моноширинный код', () => {
    const input = 'Вызовите метод `getAIAnalysis` для получения данных.';
    const result = renderMarkdown(input);
    expect(result).toContain('<code style="font-family: monospace; background: rgba(255,255,255,0.06); padding: 2px 5px; border-radius: 4px; font-size: 12px; color: var(--info); border: 1px solid rgba(255,255,255,0.08); word-break: break-all;">getAIAnalysis</code>');
  });
});
