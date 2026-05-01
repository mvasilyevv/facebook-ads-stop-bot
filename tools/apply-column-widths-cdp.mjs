#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), '..');
const require = createRequire(import.meta.url);
const { chromium } = require('../services/browser-agent/node_modules/playwright');
const { applyAdsTableColumnWidthPreset } = require('../services/browser-agent/dist/ads-table.js');
const { buildAdsTableColumnWidthTargets } = require('../services/browser-agent/dist/ads-columns.js');

function parseArgs(argv) {
  const args = { page: null, port: null, list: false, url: '' };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--list') args.list = true;
    else if (arg === '--page') args.page = Number(argv[++index]);
    else if (arg.startsWith('--page=')) args.page = Number(arg.slice('--page='.length));
    else if (arg === '--port') args.port = Number(argv[++index]);
    else if (arg.startsWith('--port=')) args.port = Number(arg.slice('--port='.length));
    else if (arg === '--url') args.url = String(argv[++index] || '');
    else if (arg.startsWith('--url=')) args.url = arg.slice('--url='.length);
  }
  return args;
}

function readLastEnvValue(name) {
  const envPath = path.join(repoRoot, '.env');
  if (!fs.existsSync(envPath)) return '';
  const lines = fs.readFileSync(envPath, 'utf8').split(/\r?\n/);
  let value = '';
  for (const line of lines) {
    const match = line.match(new RegExp(`^${name}=(.*)$`));
    if (match) value = match[1].trim();
  }
  return value;
}

async function resolveCdpPort(explicitPort) {
  if (explicitPort) return explicitPort;
  if (process.env.CDP_PORT) return Number(process.env.CDP_PORT);

  const apiKey = readLastEnvValue('API_KEY');
  if (!apiKey) throw new Error('Не найден API_KEY в .env; передай --port вручную.');
  const response = await fetch('http://127.0.0.1:8100/api/settings/vision', {
    headers: { 'X-API-Key': apiKey },
  });
  if (!response.ok) throw new Error(`Не удалось прочитать Vision settings: HTTP ${response.status}`);
  const data = await response.json();
  if (!data.cdp_port) throw new Error(`В Vision settings нет cdp_port: ${JSON.stringify(data)}`);
  return Number(data.cdp_port);
}

function isAdsManagerUrl(url) {
  return /adsmanager|facebook\.com\/ads/i.test(String(url || ''));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const port = await resolveCdpPort(args.port);
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  try {
    const pages = browser.contexts().flatMap((context) => context.pages());
    const rows = pages.map((page, index) => ({
      index,
      ads: isAdsManagerUrl(page.url()),
      url: page.url(),
    }));

    if (args.list) {
      console.table(rows);
      return;
    }

    let page = null;
    if (Number.isInteger(args.page)) {
      page = pages[args.page] || null;
    } else if (args.url) {
      page = pages.find((item) => item.url().includes(args.url)) || null;
    } else {
      const adsPages = pages.filter((item) => isAdsManagerUrl(item.url()));
      page = adsPages[adsPages.length - 1] || pages[pages.length - 1] || null;
    }

    if (!page) {
      console.table(rows);
      throw new Error('Не найдена вкладка. Укажи --page N из списка выше.');
    }

    await page.bringToFront();
    console.log(`CDP port: ${port}`);
    console.log(`Page: ${pages.indexOf(page)} ${page.url()}`);

    const targets = buildAdsTableColumnWidthTargets();
    const result = await applyAdsTableColumnWidthPreset(page, targets);
    console.log(JSON.stringify(result, null, 2));

    if (!result.applied || result.matchedColumns.length < targets.length) {
      const matched = new Set(result.matchedColumns);
      const missing = targets
        .filter((target) => !matched.has(target.title))
        .map((target) => target.title);
      console.warn('Не обработаны колонки:', missing);
      process.exitCode = 2;
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error?.stack || error?.message || String(error));
  process.exit(1);
});
