import test from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { parseAdsFromPage } from './parser.js';

// Вспомогательная функция для запуска headless-браузера и открытия страницы с HTML
async function withPage(html: string, fn: (page: any) => Promise<void>): Promise<void> {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setContent(html);
  try {
    await fn(page);
  } finally {
    await browser.close();
  }
}

// Полный набор обязательных колонок для успешного прохождения валидации макета
const TEST_COLUMNS = [
  { surfaceKey: 'toggle', text: 'Выкл./вкл.', left: 0, width: 40 },
  { surfaceKey: 'name', text: 'Название объявления', left: 40, width: 194 },
  { surfaceKey: 'delivery', text: 'Статус показа', left: 234, width: 110 },
  { surfaceKey: 'budget', text: 'Бюджет', left: 344, width: 40 },
  { surfaceKey: 'results', text: 'Результат', left: 384, width: 137 },
  { surfaceKey: 'reach', text: 'Охват', left: 521, width: 99 },
  { surfaceKey: 'impressions', text: 'Показы', left: 620, width: 113 },
  { surfaceKey: 'cost_per_result', text: 'Цена за результат', left: 733, width: 130 },
  { surfaceKey: 'spend', text: 'Сумма затрат', left: 863, width: 112 },
  { surfaceKey: 'clicks', text: 'Клики', left: 975, width: 102 },
  { surfaceKey: 'cpc', text: 'CPC', left: 1077, width: 93 },
  { surfaceKey: 'actions', text: 'Лиды', left: 1170, width: 102 },
  { surfaceKey: 'cost_per_action_type', text: 'Цена за лид', left: 1272, width: 105 },
  { surfaceKey: 'actions', text: 'Завершенные регистрации', left: 1377, width: 88 },
  { surfaceKey: 'cost_per_action_type', text: 'Цена за завершенную регистрацию', left: 1465, width: 100 },
  { surfaceKey: 'ctr', text: 'CTR', left: 1565, width: 91 },
  { surfaceKey: 'campaign_group_name', text: 'Название кампании', left: 1656, width: 40 },
  { surfaceKey: 'campaign_name', text: 'Название группы объявлений', left: 1696, width: 40 },
  { surfaceKey: 'outbound_clicks', text: 'Исходящие клики', left: 1736, width: 40 },
  { surfaceKey: 'outbound_clicks_ctr', text: 'CTR исходящих кликов', left: 1776, width: 40 },
  { surfaceKey: 'actions', text: 'Просмотры целевой страницы', left: 1816, width: 40 },
  { surfaceKey: 'cost_per_action_type', text: 'Цена за просмотр целевой страницы', left: 1856, width: 40 },
  { surfaceKey: 'cpm', text: 'CPM', left: 1896, width: 40 },
  { surfaceKey: 'frequency', text: 'Частота', left: 1936, width: 40 },
];

// Сценарий: Парсер сопоставляет ячейки по координатам left при наличии горизонтальной виртуализации
test('parseAdsFromPage — корректно сопоставляет ячейки по координатам left при виртуализации', async () => {
  const headersHtml = TEST_COLUMNS.map(c => `
    <div data-surface="table_column_header:${c.surfaceKey}" class="header-cell">${c.text}</div>
  `).join('\n');

  const html = `
    <html>
      <head>
        <style>
          .header-cell { display: inline-block; height: 30px; }
          .row-cell { display: inline-block; height: 30px; }
        </style>
      </head>
      <body>
        <!-- Заголовки таблицы -->
        ${headersHtml}

        <!-- Строка данных объявления -->
        <div class="_1gda _2djg">
          <input type="checkbox" checked />
          <div role="switch" aria-checked="true"></div>
          <!-- Ячейка названия -->
          <div class="_4lg0">
            <span class="_3dfi _3dfj">Ad 1</span>
          </div>
          <!-- Ячейка результатов (депозиты) на своем месте (left = 384px) -->
          <div class="_4lg0">5</div>
          <!-- В DOM строки отсутствует ячейка "Сумма затрат" (скрыта из-за скролла / виртуализации) -->
          <!-- Ячейка показов на своем месте (left = 620px) -->
          <div class="_4lg0">100</div>
        </div>
      </body>
    </html>
  `;

  await withPage(html, async (page) => {
    // Подменяем getBoundingClientRect в контексте браузера для точного тестирования координат
    await page.evaluate((cols: any) => {
      // Мокаем заголовки
      const headers = Array.from(document.querySelectorAll('[data-surface*="table_column_header:"]'));
      headers.forEach((h, i) => {
        const spec = cols[i];
        if (!spec) return;
        h.getBoundingClientRect = () => ({
          left: spec.left,
          width: spec.width,
          top: 0,
          right: spec.left + spec.width,
          bottom: 30,
          height: 30,
          x: spec.left,
          y: 0,
          toJSON: () => {},
        });
      });

      // Мокаем строку
      const row = document.querySelector('._1gda._2djg')!;
      // Добавляем React Fiber/Props свойства, чтобы парсер мог прочитать fbAdId
      (row as any).__reactFiber$test = {
        memoizedProps: {
          objectID: '1234567890123',
        },
      };

      // Мокаем ячейки строки. Обратите внимание: ячейки spend НЕТ в DOM.
      const cells = Array.from(row.querySelectorAll('._4lg0'));
      // Координаты для видимых ячеек: name (40px), results (384px), impressions (620px)
      const cellCoords = [
        { left: 40, width: 194 },    // name (Ad 1)
        { left: 384, width: 137 },   // results (5)
        { left: 620, width: 113 },   // impressions (100)
      ];
      cells.forEach((c, i) => {
        c.getBoundingClientRect = () => ({
          left: cellCoords[i].left,
          width: cellCoords[i].width,
          top: 30,
          right: cellCoords[i].left + cellCoords[i].width,
          bottom: 60,
          height: 30,
          x: cellCoords[i].left,
          y: 30,
          toJSON: () => {},
        });
      });
    }, TEST_COLUMNS);

    // По новой семантике (после редизайна цикла): парсер не бросает throw при отсутствии
    // отдельной ячейки в строке. Вместо этого:
    //   - строка попадает в rows с тем что прочиталось (impressions=100, results=5);
    //   - её fb_ad_id попадает в partialRowIds — observer пометит цикл OK_PARTIAL.
    // Это доказывает, что колонка spend не была ложно считана из ячейки "Показы" (100)
    // или "Результат" (5) — её просто нет в rowMissing, но fb_ad_id флагнут как partial.
    const result = await parseAdsFromPage(page);
    assert.equal(result.rows.length, 1, 'строка должна попасть в rows даже при отсутствии одной ячейки');
    assert.equal(result.rows[0].fb_ad_id, '1234567890123');
    // Показы и Результат должны быть прочитаны корректно (не подменены)
    assert.equal(result.rows[0].impressions, 100, 'Показы не должны быть ложно подменены');
    assert.equal(result.rows[0].deposits, 5, 'Результат не должен быть ложно подменён');
    // fb_ad_id попал в partialRowIds — это сигнал observer'у пометить OK_PARTIAL
    assert.ok(
      result.partialRowIds.includes('1234567890123'),
      `partialRowIds должен содержать fb_ad_id строки с пропущенной ячейкой: ${JSON.stringify(result.partialRowIds)}`,
    );
  });
});

// Сценарий: Парсер успешно считывает все данные, если ячейки на своих координатах, даже при наличии сдвигов и пустых значений в других метриках
test('parseAdsFromPage — успешно парсит строку при корректном координатном сопоставлении', async () => {
  const headersHtml = TEST_COLUMNS.map(c => `
    <div data-surface="table_column_header:${c.surfaceKey}" class="header-cell">${c.text}</div>
  `).join('\n');

  const html = `
    <html>
      <body>
        <!-- Заголовки таблицы -->
        ${headersHtml}

        <!-- Строка данных объявления -->
        <div class="_1gda _2djg">
          <input type="checkbox" checked />
          <div role="switch" aria-checked="true"></div>
          <!-- 1. Название объявления (40) -->
          <div class="_4lg0">Ad 2</div>
          <!-- 2. Результат (384) -->
          <div class="_4lg0">7</div>
          <!-- 3. Сумма затрат (863) -->
          <div class="_4lg0">12.50</div>
          <!-- 4. Показы (620) -->
          <div class="_4lg0">1000</div>
          <!-- 5. Остальные обязательные ячейки со значениями-заглушками (для не-метрики "текст" или нулями для остальных) -->
          <!-- delivery (234) -->
          <div class="_4lg0">ACTIVE</div>
          <!-- budget (344) -->
          <div class="_4lg0">100</div>
          <!-- reach (521) -->
          <div class="_4lg0">500</div>
          <!-- cost_per_result (733) -->
          <div class="_4lg0">1.78</div>
          <!-- clicks (975) -->
          <div class="_4lg0">50</div>
          <!-- cpc (1077) -->
          <div class="_4lg0">0.25</div>
          <!-- actions/leads (1170) -->
          <div class="_4lg0">0</div>
          <!-- cost_per_lead (1272) -->
          <div class="_4lg0">0</div>
          <!-- actions/registrations (1377) -->
          <div class="_4lg0">0</div>
          <!-- cost_per_registration (1465) -->
          <div class="_4lg0">0</div>
          <!-- ctr (1565) -->
          <div class="_4lg0">2.5</div>
          <!-- campaign_group_name (1656) -->
          <div class="_4lg0">Campaign 1</div>
          <!-- campaign_name (1696) -->
          <div class="_4lg0">Adset 1</div>
          <!-- outbound_clicks (1736) -->
          <div class="_4lg0">0</div>
          <!-- outbound_clicks_ctr (1776) -->
          <div class="_4lg0">0</div>
          <!-- actions/landing_page_views (1816) -->
          <div class="_4lg0">0</div>
          <!-- cost_per_landing_page_view (1856) -->
          <div class="_4lg0">0</div>
          <!-- cpm (1896) -->
          <div class="_4lg0">12.50</div>
          <!-- frequency (1936) -->
          <div class="_4lg0">1.2</div>
        </div>
      </body>
    </html>
  `;

  await withPage(html, async (page) => {
    await page.evaluate((cols: any) => {
      // Мокаем заголовки
      const headers = Array.from(document.querySelectorAll('[data-surface*="table_column_header:"]'));
      headers.forEach((h, i) => {
        const spec = cols[i];
        if (!spec) return;
        h.getBoundingClientRect = () => ({
          left: spec.left,
          width: spec.width,
          top: 0,
          right: spec.left + spec.width,
          bottom: 30,
          height: 30,
          x: spec.left,
          y: 0,
          toJSON: () => {},
        });
      });

      const row = document.querySelector('._1gda._2djg')!;
      (row as any).__reactFiber$test = {
        memoizedProps: {
          objectID: '9876543210987',
        },
      };

      // Все ячейки присутствуют
      const cells = Array.from(row.querySelectorAll('._4lg0'));
      // Координаты для ячеек, строго соответствующие колонкам из cols
      const cellCoords = [
        { left: 40, width: 194 },    // name (Ad 2)
        { left: 384, width: 137 },   // results (7)
        { left: 863, width: 112 },   // spend (12.50)
        { left: 620, width: 113 },   // impressions (1000)
        { left: 234, width: 110 },   // delivery (ACTIVE)
        { left: 344, width: 40 },    // budget (100)
        { left: 521, width: 99 },    // reach (500)
        { left: 733, width: 130 },   // cost_per_result (1.78)
        { left: 975, width: 102 },   // clicks (50)
        { left: 1077, width: 93 },   // cpc (0.25)
        { left: 1170, width: 102 },  // leads (0)
        { left: 1272, width: 105 },  // cost_per_lead (0)
        { left: 1377, width: 88 },   // registrations (0)
        { left: 1465, width: 100 },  // cost_per_registration (0)
        { left: 1565, width: 91 },   // ctr (2.5)
        { left: 1656, width: 40 },   // campaign_name (Campaign 1)
        { left: 1696, width: 40 },   // adset_name (Adset 1)
        { left: 1736, width: 40 },   // outbound_clicks (0)
        { left: 1776, width: 40 },   // outbound_ctr (0)
        { left: 1816, width: 40 },   // landing_page_views (0)
        { left: 1856, width: 40 },   // cost_per_landing_page_view (0)
        { left: 1896, width: 40 },   // cpm (12.50)
        { left: 1936, width: 40 },   // frequency (1.2)
      ];
      cells.forEach((c, i) => {
        c.getBoundingClientRect = () => ({
          left: cellCoords[i].left,
          width: cellCoords[i].width,
          top: 30,
          right: cellCoords[i].left + cellCoords[i].width,
          bottom: 60,
          height: 30,
          x: cellCoords[i].left,
          y: 30,
          toJSON: () => {},
        });
      });
    }, TEST_COLUMNS);

    const parsed = await parseAdsFromPage(page);
    assert.equal(parsed.rows.length, 1);
    assert.equal(parsed.rows[0].fb_ad_id, '9876543210987');
    assert.equal(parsed.rows[0].ad_name, 'Ad 2');
    assert.equal(parsed.rows[0].deposits, 7);
    assert.equal(parsed.rows[0].spend, '12.50');
    assert.equal(parsed.rows[0].impressions, 1000);
  });
});
