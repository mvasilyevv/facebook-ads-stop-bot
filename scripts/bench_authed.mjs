// Бенч на КОПИИ залогиненного профиля: меряем authenticated-UI syntx + overhead a11y-снапшота.
import pwcore from '/Users/markvasilev/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core/index.js';
const { chromium } = pwcore;
import { execSync } from 'node:child_process';
import os from 'node:os';
import fs from 'node:fs';

const PROFILE = fs.readFileSync('/tmp/recon_copy_path.txt','utf8').trim();
const HOME = os.homedir();
const SHELL = `${HOME}/Library/Caches/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-mac-arm64/chrome-headless-shell`;
const URL = 'https://syntx.ai/ru/image/banana';
const T = 120000;

const ctx = await chromium.launchPersistentContext(PROFILE, {
  headless: true, executablePath: SHELL, viewport: { width: 1280, height: 800 }, timeout: T,
  args: ['--no-first-run','--no-default-browser-check','--mute-audio','--disable-extensions','--disable-background-networking','--disable-dev-shm-usage'],
});
const pid = ctx.browser()?.process?.()?.pid;
const rss = () => { try { return Math.round(+execSync(`ps -o rss= -p ${pid}`).toString().trim()/1024); } catch { return -1; } };
const page = ctx.pages()[0] || await ctx.newPage();

const tNav = Date.now();
await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: T });
const dom = Date.now() - tNav;

let uiReady = -1;
try { await page.waitForSelector('textarea', { timeout: T }); uiReady = Date.now() - tNav; } catch {}

// время на network-idle (полная загрузка ресурсов SPA)
let netIdle = -1;
try { const t = Date.now(); await page.waitForLoadState('networkidle', { timeout: 30000 }); netIdle = Date.now() - t; } catch {}

// overhead a11y-снапшота (то, что MCP делает на КАЖДОЕ действие)
let snapMs = -1, snapNodes = -1;
try {
  const t = Date.now();
  const snap = await page.accessibility.snapshot({ interestingOnly: false });
  snapMs = Date.now() - t;
  const count = n => 1 + (n.children?.reduce((s,c)=>s+count(c),0) || 0);
  snapNodes = snap ? count(snap) : 0;
} catch (e) { snapMs = -2; }

console.log(JSON.stringify({ dom_ms: dom, ui_ready_ms: uiReady, networkidle_ms: netIdle, a11y_snapshot_ms: snapMs, a11y_nodes: snapNodes, rss_mb: rss(), logged_in: uiReady>0 }, null, 2));
await ctx.close();
fs.rmSync(PROFILE, { recursive: true, force: true });
