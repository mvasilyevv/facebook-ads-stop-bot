// Бенчмарк headless-конфигураций Chromium на тяжёлой SPA (syntx Nano Banana).
// Изолированные temp-профили — боевой recon_profile НЕ трогаем.
// Метрика: время до готовности UI (textarea промта) + RSS браузера.
import pwcore from '/Users/markvasilev/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core/index.js';
const { chromium } = pwcore;
import { execSync } from 'node:child_process';
import os from 'node:os';
import fs from 'node:fs';

const URL = 'https://syntx.ai/ru/image/banana';
const READY = 'textarea';            // признак «UI построился»
const NAV_TIMEOUT = 90000;

function rssMB(pid) {
  try { return Math.round(+execSync(`ps -o rss= -p ${pid}`).toString().trim() / 1024); }
  catch { return -1; }
}

async function run(name, opts) {
  const dir = fs.mkdtempSync(`${os.tmpdir()}/bench_${name}_`);
  const t0 = Date.now();
  let ctx, ready = -1, domReady = -1, rss = -1, err = null;
  try {
    ctx = await chromium.launchPersistentContext(dir, {
      headless: true, viewport: { width: 1280, height: 800 }, timeout: NAV_TIMEOUT, ...opts,
    });
    const page = ctx.pages()[0] || await ctx.newPage();
    const tNav = Date.now();
    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT });
    domReady = Date.now() - tNav;
    await page.waitForSelector(READY, { timeout: NAV_TIMEOUT });
    ready = Date.now() - tNav;
    const pid = ctx.browser()?.process?.()?.pid;
    rss = pid ? rssMB(pid) : -1;
  } catch (e) { err = e.message.split('\n')[0]; }
  finally { if (ctx) await ctx.close().catch(()=>{}); fs.rmSync(dir, { recursive: true, force: true }); }
  const total = Date.now() - t0;
  console.log(JSON.stringify({ name, domReady_ms: domReady, ui_ready_ms: ready, rss_mb: rss, total_ms: total, err }));
}

const BASE = ['--no-first-run','--no-default-browser-check','--mute-audio','--disable-extensions','--disable-background-networking','--disable-dev-shm-usage'];
const HOME = os.homedir();
const SHELL = `${HOME}/Library/Caches/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-mac-arm64/chrome-headless-shell`;
const FULL  = `${HOME}/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing`;

// 1. headless-shell (lightweight, дефолт старого MCP, без disable-gpu)
await run('A_shell_default', { executablePath: SHELL, args: BASE });
// 2. headless-shell + мой disable-gpu (что я добавил в конфиг — проверяем, вредит ли)
await run('B_shell_disablegpu', { executablePath: SHELL, args: [...BASE,'--disable-gpu','--disable-software-rasterizer'] });
// 3. полный Chrome-for-Testing + GPU через ANGLE (новый headless)
await run('C_full_angle_gpu', { executablePath: FULL, args: [...BASE,'--use-angle=gl','--use-gl=angle','--ignore-gpu-blocklist'] });

console.log('DONE');
