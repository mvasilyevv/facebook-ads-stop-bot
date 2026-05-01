(async () => {
  // Консольный диагностический скрипт для Ads Manager.
  // Запускать во вкладке Ads Manager, где не идёт сканирование observer/browser-agent.
  if (window.__fbAutoWidthRunning) {
    console.warn('[auto-width] Уже выполняется предыдущий запуск.');
    return window.__fbAutoWidthLastResult || null;
  }

  window.__fbAutoWidthRunning = true;

  const CONFIG = {
    tolerancePx: 3,
    maxPasses: 10,
    settleMs: 120,
    scrollStepRatio: 0.65,
    restoreScroll: false,
  };

  const TARGETS = [
    { key: 'toggle', title: 'Выкл./вкл.', surfaceKey: 'toggle', widthPx: 40 },
    { key: 'name', title: 'Название объявления', surfaceKey: 'name', widthPx: 194 },
    { key: 'delivery', title: 'Статус показа', surfaceKey: 'delivery', widthPx: 110 },
    { key: 'budget', title: 'Бюджет', surfaceKey: 'budget', widthPx: 40 },
    { key: 'deposits', title: 'Результат', surfaceKey: 'results', widthPx: 137 },
    { key: 'reach', title: 'Охват', surfaceKey: 'reach', widthPx: 99 },
    { key: 'impressions', title: 'Показы', surfaceKey: 'impressions', widthPx: 113 },
    { key: 'cost_per_result', title: 'Цена за результат', surfaceKey: 'cost_per_result', widthPx: 130 },
    { key: 'spend', title: 'Сумма затрат', surfaceKey: 'spend', widthPx: 112 },
    { key: 'clicks', title: 'Клики', surfaceKey: 'clicks', widthPx: 102 },
    { key: 'cpc', title: 'CPC', surfaceKey: 'cpc', widthPx: 93 },
    { key: 'leads', title: 'Лиды', surfaceKey: 'actions', textNeedles: ['лид', 'лід', 'lead'], widthPx: 102 },
    { key: 'cost_per_lead', title: 'Цена за лид', surfaceKey: 'cost_per_action_type', textNeedles: ['лид', 'лід', 'lead'], widthPx: 105 },
    { key: 'registrations', title: 'Завершенные регистрации', surfaceKey: 'actions', textNeedles: ['регистрац', 'реєстрац', 'registration'], widthPx: 88 },
    { key: 'cost_per_registration', title: 'Цена за завершенную регистрацию', surfaceKey: 'cost_per_action_type', textNeedles: ['регистрац', 'реєстрац', 'registration'], widthPx: 100 },
    { key: 'ctr', title: 'CTR', surfaceKey: 'ctr', widthPx: 91 },
    { key: 'campaign_name', title: 'Название кампании', surfaceKey: 'campaign_group_name', textNeedles: ['название кампании'], widthPx: 40 },
    { key: 'adset_name', title: 'Название группы объявлений', surfaceKey: 'campaign_name', textNeedles: ['название группы объявлений'], widthPx: 40 },
    { key: 'outbound_clicks', title: 'Исходящие клики', surfaceKey: 'outbound_clicks', widthPx: 40 },
    { key: 'outbound_ctr', title: 'CTR исходящих кликов', surfaceKey: 'outbound_clicks_ctr', widthPx: 40 },
    { key: 'landing_page_views', title: 'Просмотры целевой страницы', surfaceKey: 'actions', textNeedles: ['целев', 'цільов', 'landing page'], widthPx: 40 },
    { key: 'cost_per_landing_page_view', title: 'Цена за просмотр целевой страницы', surfaceKey: 'cost_per_action_type', textNeedles: ['целев', 'цільов', 'landing page'], widthPx: 40 },
    { key: 'cpm', title: 'CPM', surfaceKey: 'cpm', widthPx: 40 },
    { key: 'frequency', title: 'Частота', surfaceKey: 'frequency', widthPx: 40 },
  ];

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const surfaceOf = (node) => {
    const surface = node.getAttribute('data-surface') || '';
    const match = surface.match(/table_column_header:([^/]+)/);
    return match ? match[1] : '';
  };

  function targetMatches(target, surfaceKey, text) {
    const normalizedText = normalize(text);
    const titleMatches = Boolean(normalizedText) && normalizedText === normalize(target.title);
    if (target.surfaceKey !== surfaceKey) return titleMatches;
    if (!target.textNeedles?.length) return true;
    if (!normalizedText) return true;
    return titleMatches || target.textNeedles.some((needle) => normalizedText.includes(normalize(needle)));
  }

  function findColumnCell(headerNode) {
    const directCell = headerNode.closest('._4lg0');
    if (directCell instanceof HTMLElement) {
      const rect = directCell.getBoundingClientRect();
      if (rect.width > 20 && rect.height > 20) return directCell;
    }
    for (let node = headerNode.parentElement; node instanceof HTMLElement; node = node.parentElement) {
      const rect = node.getBoundingClientRect();
      if (rect.width > 20 && rect.height > 20 && node.style.width) return node;
    }
    return null;
  }

  function collectHeaders() {
    const seen = new Set();
    return Array.from(document.querySelectorAll('[data-surface*="table_column_header:"]'))
      .map((headerNode) => {
        const surfaceKey = surfaceOf(headerNode);
        const text = headerNode.textContent || '';
        const target = TARGETS.find((item) => targetMatches(item, surfaceKey, text));
        if (!target || seen.has(target.key)) return null;
        const cell = findColumnCell(headerNode);
        if (!(cell instanceof HTMLElement)) return null;
        const rect = cell.getBoundingClientRect();
        if (rect.width <= 20 || rect.height <= 20) return null;
        seen.add(target.key);
        return {
          target,
          cell,
          left: rect.left,
          right: rect.right,
          before: rect.width,
          text: normalize(text),
        };
      })
      .filter(Boolean)
      .sort((a, b) => a.left - b.left);
  }

  function tableCellsSnapshot() {
    return Array.from(document.querySelectorAll('._4lg0'))
      .filter((cell) => cell instanceof HTMLElement)
      .map((cell) => {
        const rect = cell.getBoundingClientRect();
        return { cell, left: rect.left, right: rect.right, width: rect.width, height: rect.height };
      })
      .filter((item) => item.width > 10 && item.height > 10);
  }

  const undoRecords = new Map();
  function rememberStyle(cell) {
    if (!undoRecords.has(cell)) undoRecords.set(cell, cell.getAttribute('style'));
  }

  function setCellWidth(cell, widthPx) {
    if (!(cell instanceof HTMLElement)) return;
    rememberStyle(cell);
    const value = `${Math.round(widthPx)}px`;
    cell.style.setProperty('width', value, 'important');
    cell.style.setProperty('min-width', value, 'important');
    cell.style.setProperty('max-width', value, 'important');
    cell.style.setProperty('flex-basis', value, 'important');
    cell.style.setProperty('box-sizing', 'border-box', 'important');
    cell.style.setProperty('overflow', 'hidden', 'important');

    for (const inner of cell.querySelectorAll('[role="columnheader"], [data-surface*="table_column_header:"]')) {
      if (inner instanceof HTMLElement) {
        rememberStyle(inner);
        inner.style.setProperty('width', value, 'important');
        inner.style.setProperty('min-width', value, 'important');
        inner.style.setProperty('max-width', value, 'important');
        inner.style.setProperty('box-sizing', 'border-box', 'important');
      }
    }
  }

  function alignedCells(entry, snapshot) {
    const cells = snapshot
      .filter((item) => Math.abs(item.left - entry.left) <= 4 || Math.abs(item.right - entry.right) <= 4)
      .map((item) => item.cell);
    return Array.from(new Set([entry.cell, ...cells]));
  }

  async function applyVisiblePass(passNumber, processedKeys, rows) {
    const headers = collectHeaders().filter((entry) => !processedKeys.has(entry.target.key));
    const snapshot = tableCellsSnapshot();

    for (const entry of headers) {
      const target = entry.target;
      const cells = alignedCells(entry, snapshot);
      const delta = target.widthPx - entry.before;
      let status = 'skip';

      if (Math.abs(delta) > CONFIG.tolerancePx) {
        for (const cell of cells) setCellWidth(cell, target.widthPx);
        await sleep(20);
        status = 'set';
      }

      const after = entry.cell.getBoundingClientRect().width;
      processedKeys.add(target.key);
      rows.push({
        pass: passNumber,
        key: target.key,
        title: target.title,
        before: Math.round(entry.before),
        target: target.widthPx,
        after: Math.round(after),
        cells: cells.length,
        status,
      });
    }
  }

  function findHorizontalScrollers() {
    const candidates = [];
    const seen = new Set();
    const header = document.querySelector('[data-surface*="table_column_header:"]');
    const anchors = [
      header,
      document.querySelector('[data-surface*="table_row:"], ._1gda._2djg'),
      document.querySelector('[role="grid"]'),
      document.querySelector('[role="table"]'),
      document.querySelector('[aria-rowcount]'),
    ].filter(Boolean);

    function add(node) {
      if (!(node instanceof HTMLElement) || seen.has(node)) return;
      seen.add(node);
      const maxScrollLeft = node.scrollWidth - node.clientWidth;
      const rect = node.getBoundingClientRect();
      if (maxScrollLeft <= 8 || node.clientWidth < 180 || rect.height < 40) return;
      const containsHeader = header ? node.contains(header) : false;
      candidates.push({
        node,
        maxScrollLeft,
        score: maxScrollLeft + (containsHeader ? 10000 : 0) + Math.round(rect.width + rect.height),
      });
    }

    for (const anchor of anchors) {
      for (let node = anchor; node; node = node.parentElement) add(node);
    }
    for (const node of document.querySelectorAll('[role="grid"], [role="table"], [aria-rowcount], div')) add(node);
    return candidates.sort((a, b) => b.score - a.score);
  }

  const originalScrolls = new Map();
  function rememberScroll(scroller) {
    if (!originalScrolls.has(scroller)) originalScrolls.set(scroller, scroller.scrollLeft);
  }

  async function scrollRight() {
    const candidate = findHorizontalScrollers()[0];
    if (!candidate) return false;
    const scroller = candidate.node;
    rememberScroll(scroller);
    const before = scroller.scrollLeft;
    const step = Math.max(160, Math.round(scroller.clientWidth * CONFIG.scrollStepRatio));
    scroller.scrollLeft = Math.min(before + step, candidate.maxScrollLeft);
    scroller.dispatchEvent(new Event('scroll', { bubbles: true }));
    await sleep(CONFIG.settleMs);
    return scroller.scrollLeft > before + 2;
  }

  try {
    const processedKeys = new Set();
    const rows = [];

    for (let pass = 1; pass <= CONFIG.maxPasses; pass += 1) {
      await applyVisiblePass(pass, processedKeys, rows);
      if (processedKeys.size >= TARGETS.length) break;
      const moved = await scrollRight();
      if (!moved) break;
    }

    if (CONFIG.restoreScroll) {
      for (const [node, left] of originalScrolls.entries()) {
        node.scrollLeft = left;
        node.dispatchEvent(new Event('scroll', { bubbles: true }));
      }
    }

    window.__fbAutoWidthUndo = () => {
      for (const [node, style] of undoRecords.entries()) {
        if (style === null) node.removeAttribute('style');
        else node.setAttribute('style', style);
      }
      console.info(`[auto-width] Откатил inline-style для ${undoRecords.size} элементов.`);
    };

    const missing = TARGETS.filter((target) => !processedKeys.has(target.key)).map((target) => target.title);
    const result = {
      ok: missing.length === 0,
      processed: processedKeys.size,
      expected: TARGETS.length,
      missing,
      rows,
      undo: 'window.__fbAutoWidthUndo()',
    };

    console.table(rows);
    if (missing.length) console.warn('[auto-width] Не найдены колонки:', missing);
    else console.info('[auto-width] Все целевые колонки обработаны.');
    console.info('[auto-width] Для отката inline-стилей: window.__fbAutoWidthUndo()');

    window.__fbAutoWidthLastResult = result;
    return result;
  } finally {
    window.__fbAutoWidthRunning = false;
  }
})();
