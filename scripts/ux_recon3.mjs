/**
 * UX Recon - Part 3: correct nav via mouse clicks + Trend Analysis probe
 * Nav item viewport coords (x≈97, y from bounding boxes found in prior session):
 *   Dashboard      y≈156
 *   Trend Tracking y≈210
 *   Trends DB      y≈264
 *   TikTok         y≈318
 *   Trend Analysis y≈372
 *   T. Startups    y≈426
 *   T. Products    y≈480
 *   Meta Trends    y≈534
 *   Reports        y≈588
 *   API Access     y≈642
 */
import { chromium } from '/Users/mgr/.npm/_npx/9833c18b2d85bc59/node_modules/playwright/index.mjs';
import fs from 'fs';
import path from 'path';

const AUTH  = path.resolve('./auth.json');
const DOCS  = path.resolve('./docs/ux');
const BASE  = 'https://www.semrush.com/app/exploding-topics/pro/dashboard';

const NAV = [
  { id: 'dashboard',          y: 156 },
  { id: 'trend-tracking',     y: 210 },
  { id: 'trends-database',    y: 264 },
  { id: 'tiktok-insights',    y: 318 },
  { id: 'trend-analysis',     y: 372 },
  { id: 'trending-startups',  y: 426 },
  { id: 'trending-products',  y: 480 },
  { id: 'meta-trends',        y: 534 },
  { id: 'reports-library',    y: 588 },
];
const NAV_X = 97;

function ensureDir(d) { fs.mkdirSync(d, { recursive: true }); }

async function waitEt(page, ms = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const f = page.frames().find(f => f.url().includes('ac.explodingtopics'));
    if (f) return f;
    await page.waitForTimeout(500);
  }
  return null;
}

async function shot(page, id, name) {
  ensureDir(path.join(DOCS, id));
  const fp = path.join(DOCS, id, `${name}.png`);
  await page.screenshot({ path: fp, fullPage: true, type: 'png' });
  console.log(`  📸 ${id}/${name}.png`);
  return fp;
}

async function etText(et) {
  return et?.evaluate(() => document.body?.innerText?.slice(0, 5000)).catch(() => '') ?? '';
}

async function etFilters(et) {
  if (!et) return null;
  return et.evaluate(() => {
    const selects = [...document.querySelectorAll('select,[role=combobox],[role=listbox]')]
      .map(s => ({
        label: s.getAttribute('aria-label') || s.closest('label')?.innerText?.trim() || s.id || '',
        options: [...(s.querySelectorAll('option,[role=option]') || [])].map(o => o.innerText?.trim()).filter(Boolean).slice(0, 10),
      })).slice(0, 8);
    const btns = [...document.querySelectorAll('button')]
      .map(b => b.innerText?.trim()).filter(t => t && t.length < 50).slice(0, 20);
    return { selects, buttons: btns };
  }).catch(() => null);
}

async function etTable(et) {
  if (!et) return null;
  return et.evaluate(() => {
    const t = document.querySelector('table,[role=table],[role=grid]');
    if (!t) return null;
    const headers = [...t.querySelectorAll('th,[role=columnheader]')].map(h => h.innerText?.trim()).filter(Boolean);
    const rows = [...t.querySelectorAll('tr,[role=row]')].slice(1, 4)
      .map(r => [...r.querySelectorAll('td,[role=cell]')].map(c => c.innerText?.trim()?.slice(0, 80)));
    return { headers, sampleRows: rows };
  }).catch(() => null);
}

async function etCards(et) {
  if (!et) return null;
  return et.evaluate(() => {
    const cards = [...document.querySelectorAll('[class*="card"],[class*="Card"],article,[class*="trend-"]')]
      .filter(el => el.children.length > 0).slice(0, 3);
    return cards.map(card => {
      const leaves = [...card.querySelectorAll('*')]
        .filter(el => el.children.length === 0 && el.innerText?.trim())
        .map(el => el.innerText.trim().slice(0, 80)).filter(Boolean).slice(0, 15);
      return { fields: leaves };
    });
  }).catch(() => null);
}

async function captureApiCalls(page, et, ms = 4000) {
  const calls = [];
  const handle = async resp => {
    const u = resp.url();
    if (!u.includes('google') && !u.includes('doubleclick') && !u.includes('chunk') &&
        (u.includes('/api/') || u.includes('/v1/') || u.includes('/v2/') || u.includes('graphql') || /\.json(\?|$)/.test(u))) {
      const body = await resp.json().catch(() => null);
      if (body) calls.push({
        url:    u.replace(/[?#].*/, ''),
        query:  u.includes('?') ? u.split('?')[1].slice(0, 200) : '',
        method: resp.request().method(),
        status: resp.status(),
        shape:  summariseShape(body),
      });
    }
  };
  page.on('response', handle);
  if (et) et.on('response', handle);
  await page.waitForTimeout(ms);
  page.off('response', handle);
  if (et) et.off('response', handle);
  return calls.slice(0, 8);
}

function summariseShape(obj, depth = 0) {
  if (depth > 2) return typeof obj;
  if (Array.isArray(obj)) return `Array(${obj.length}) of ${summariseShape(obj[0], depth + 1)}`;
  if (obj && typeof obj === 'object') {
    return Object.fromEntries(Object.entries(obj).slice(0, 6).map(([k, v]) => [k, summariseShape(v, depth + 1)]));
  }
  return typeof obj;
}

// ── Navigate via mouse click in nav ──────────────────────────────────────────
async function navTo(page, y, waitMs = 4000) {
  await page.mouse.click(NAV_X, y);
  await page.waitForTimeout(waitMs);
  const et = await waitEt(page, 6000);
  return et;
}

// ── Trend Analysis: search for a topic ──────────────────────────────────────
async function searchTopic(page, et, topic) {
  if (!et) return null;
  // Try any visible input
  const inp = et.locator('input').first();
  try {
    await inp.waitFor({ state: 'visible', timeout: 8000 });
    await inp.click();
    await inp.fill(topic);
    await page.waitForTimeout(400);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(5000);
    return true;
  } catch {
    // fallback: click center of page and type
    await page.keyboard.type(topic, { delay: 60 });
    await page.keyboard.press('Enter');
    await page.waitForTimeout(5000);
    return true;
  }
}

// ─── MAIN ─────────────────────────────────────────────────────────────────────
async function main() {
  ensureDir(DOCS);
  const browser = await chromium.launch({
    channel: 'chrome',
    headless: false,
    args: ['--window-size=1512,900', '--disable-blink-features=AutomationControlled'],
  });
  const ctx = await browser.newContext({
    storageState: AUTH,
    viewport: { width: 1512, height: 900 },
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  });
  const page = await ctx.newPage();
  const results = {};

  try {
    // Load dashboard first so nav is available
    console.log('🚀 Loading dashboard...');
    await page.goto(BASE, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    let et = await waitEt(page, 15000);
    if (!et) { console.error('❌ iframe not loading'); await browser.close(); return; }
    await et.waitForLoadState('domcontentloaded').catch(() => {});
    await page.waitForTimeout(3000);
    console.log('  ✅ iframe loaded:', et.url().slice(0, 60));

    // ── Dashboard ──────────────────────────────────────────────────────────
    console.log('\n📄 Dashboard');
    const dashApi = await captureApiCalls(page, et, 3000);
    await shot(page, 'dashboard', 'loaded');
    await et.evaluate(() => window.scrollBy(0, 500)).catch(() => {});
    await page.waitForTimeout(800);
    await shot(page, 'dashboard', 'scrolled');
    await et.evaluate(() => window.scrollTo(0, 0)).catch(() => {});
    const dashCards = await etCards(et);
    results.dashboard = { text: await etText(et), cards: dashCards, api: dashApi };

    // ── Loop through nav sections ──────────────────────────────────────────
    for (const { id, y } of NAV.slice(1)) {
      console.log(`\n📄 ${id} (click y=${y})`);
      et = await navTo(page, y, 4000);
      const url = page.url();
      console.log(`  url: ${url}`);

      // capture API calls for 3s while page settles
      const api = await captureApiCalls(page, et, 3000);
      await shot(page, id, 'full');

      // scroll and second shot
      await et?.evaluate(() => window.scrollBy(0, 500)).catch(() => {});
      await page.waitForTimeout(700);
      await shot(page, id, 'scrolled');
      await et?.evaluate(() => window.scrollTo(0, 0)).catch(() => {});
      await page.waitForTimeout(300);

      const data = {
        semrushUrl: url,
        etUrl: et?.url(),
        text:    await etText(et),
        table:   await etTable(et),
        filters: await etFilters(et),
        cards:   await etCards(et),
        api,
      };

      // ── Trend Analysis: probe topics ──────────────────────────────────
      if (id === 'trend-analysis') {
        data.topics = [];
        for (const topic of ['AI agents', 'breathwork']) {
          console.log(`  🔍 Probing: ${topic}`);
          const topicApi = await captureApiCalls(page, et, async () => {
            await searchTopic(page, et, topic);
          }, 3000);
          await shot(page, id, `topic-${topic.replace(/\s+/g, '-')}`);
          await et?.evaluate(() => window.scrollBy(0, 600)).catch(() => {});
          await page.waitForTimeout(600);
          await shot(page, id, `topic-${topic.replace(/\s+/g, '-')}-chart`);

          const topicData = {
            topic,
            text:  await etText(et),
            table: await etTable(et),
            api:   topicApi,
          };
          data.topics.push(topicData);

          // reset: go back to analysis page via nav click
          await navTo(page, y, 3000);
          et = page.frames().find(f => f.url().includes('ac.explodingtopics')) ?? et;
        }
      }

      results[id] = data;
    }

    fs.writeFileSync(path.join(DOCS, 'raw-data.json'), JSON.stringify(results, null, 2));
    console.log('\n✅ Done! Saved docs/ux/raw-data.json');

  } finally {
    await browser.close();
  }
}

// helper that accepts actionFn in captureApiCalls
async function captureApiCallsWithAction(page, et, actionFn, ms = 3000) {
  const calls = [];
  const handle = async resp => {
    const u = resp.url();
    if (!u.includes('google') && !u.includes('doubleclick') && !u.includes('chunk') &&
        (u.includes('/api/') || u.includes('/v1/') || u.includes('/v2/') || /\.json(\?|$)/.test(u))) {
      const body = await resp.json().catch(() => null);
      if (body) calls.push({ url: u.replace(/[?#].*/, ''), method: resp.request().method() });
    }
  };
  page.on('response', handle);
  if (et) et.on('response', handle);
  if (actionFn) await actionFn();
  await page.waitForTimeout(ms);
  page.off('response', handle);
  if (et) et.off('response', handle);
  return calls;
}

main().catch(e => { console.error(e); process.exit(1); });
