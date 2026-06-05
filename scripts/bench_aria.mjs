// Меряем overhead aria-снапшота (то, что MCP делает на КАЖДОЕ действие) на тяжёлом authenticated SPA.
import pwcore from '/Users/markvasilev/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core/index.js';
const { chromium } = pwcore;
import os from 'node:os'; import fs from 'node:fs';

const PROFILE = fs.readFileSync('/tmp/recon_copy_path.txt','utf8').trim();
const HOME = os.homedir();
const SHELL = `${HOME}/Library/Caches/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-mac-arm64/chrome-headless-shell`;
const T = 120000;

const ctx = await chromium.launchPersistentContext(PROFILE, {
  headless: true, executablePath: SHELL, viewport: { width: 1280, height: 800 }, timeout: T,
  args: ['--no-first-run','--mute-audio','--disable-extensions','--disable-background-networking','--disable-dev-shm-usage'],
});
const page = ctx.pages()[0] || await ctx.newPage();
await page.goto('https://syntx.ai/ru/image/banana', { waitUntil: 'domcontentloaded', timeout: T });
await page.waitForSelector('textarea', { timeout: T });
await page.waitForTimeout(2000); // дать SPA дорисоваться как при реальной работе

// aria-снапшот всей страницы — ровно то, что делает browser_snapshot в MCP
const times = [];
for (let i=0;i<3;i++){ const t=Date.now(); const s=await page.locator('body').ariaSnapshot(); times.push(Date.now()-t); if(i===0) global.__len=s.length; }

// для сравнения — точечный evaluate (как browser_evaluate, чем я и работаю)
const te=Date.now();
await page.evaluate(()=>document.querySelectorAll('*').length);
const evalMs=Date.now()-te;

console.log(JSON.stringify({
  aria_snapshot_ms: times, aria_snapshot_avg: Math.round(times.reduce((a,b)=>a+b)/times.length),
  aria_chars: global.__len, dom_elements: await page.evaluate(()=>document.querySelectorAll('*').length),
  evaluate_ms: evalMs,
}, null, 2));
await ctx.close(); fs.rmSync(PROFILE,{recursive:true,force:true});
