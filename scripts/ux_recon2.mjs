/**
 * UX Recon - Part 2: Trend Analysis + remaining pages
 */
import { chromium } from '/Users/mgr/.npm/_npx/9833c18b2d85bc59/node_modules/playwright/index.mjs';
import fs from 'fs';
import path from 'path';

const AUTH_FILE  = path.resolve('./auth.json');
const DOCS_DIR   = path.resolve('./docs/ux');
const BASE_URL   = 'https://www.semrush.com/app/exploding-topics/pro';

function ensureDir(d) { fs.mkdirSync(d, { recursive: true }); }

async function waitEt(page, ms = 12000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const f = page.frames().find(f => f.url().includes('ac.explodingtopics'));
    if (f) return f;
    await page.waitForTimeout(600);
  }
  return null;
}

async function goto(page, sub) {
  await page.goto(`${BASE_URL}${sub}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  const et = await waitEt(page);
  if (et) await et.waitForLoadState('domcontentloaded').catch(() => {});
  await page.waitForTimeout(3000);
  return et;
}

async function shot(page, id, name) {
  const dir = path.join(DOCS_DIR, id);
  ensureDir(dir);
  const f = path.join(dir, `${name}.png`);
  await page.screenshot({ path: f, fullPage: true, type: 'png' });
  console.log(`  📸 ${id}/${name}.png`);
}

async function txt(et) {
  return et?.evaluate(() => document.body?.innerText?.slice(0, 4000)).catch(() => '') ?? '';
}

async function tableStruct(et) {
  return et?.evaluate(() => {
    const t = document.querySelector('table,[role=table],[role=grid]');
    if (!t) return null;
    const hs = [...t.querySelectorAll('th,[role=columnheader]')].map(h => h.innerText.trim());
    const rows = [...t.querySelectorAll('tr,[role=row]')].slice(1,4).map(r =>
      [...r.querySelectorAll('td,[role=cell]')].map(c => c.innerText.trim().slice(0,60))
    );
    return { headers: hs, rows };
  }).catch(() => null);
}

async function filterStruct(et) {
  return et?.evaluate(() => {
    const btns = [...document.querySelectorAll('button')].map(b=>b.innerText.trim()).filter(t=>t&&t.length<40);
    const selects = [...document.querySelectorAll('select,[role=combobox]')].map(s=>({
      label: s.getAttribute('aria-label')||'',
      options: [...s.querySelectorAll('option')].map(o=>o.text).slice(0,8)
    }));
    return { buttons: btns.slice(0,25), selects: selects.slice(0,8) };
  }).catch(() => null);
}

async function networkCapture(page, et, actionFn, ms = 4000) {
  const calls = [];
  const handler = async resp => {
    const u = resp.url();
    if ((u.includes('/api/') || u.includes('/v1/') || u.includes('/v2/') || u.includes('graphql') || u.includes('.json?')) && !u.includes('google') && !u.includes('doubleclick')) {
      const body = await resp.json().catch(() => null);
      if (body) calls.push({ url: u.split('?')[0], method: resp.request().method(), status: resp.status(), bodySnippet: JSON.stringify(body).slice(0, 500) });
    }
  };
  if (et) et.on('response', handler);
  page.on('response', handler);
  if (actionFn) await actionFn();
  await page.waitForTimeout(ms);
  if (et) et.off('response', handler);
  page.off('response', handler);
  return calls.slice(0, 8);
}

// ── Main ──────────────────────────────────────────────────────────────────────
const RESULTS = {};

async function main() {
  const browser = await chromium.launch({
    channel: 'chrome',
    headless: false,
    args: ['--window-size=1512,900'],
  });
  const ctx = await browser.newContext({
    storageState: AUTH_FILE,
    viewport: { width: 1512, height: 900 },
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  });
  const page = await ctx.newPage();

  try {

    // ── 1. Trend Analysis ────────────────────────────────────────────────────
    console.log('\n📄 Trend Analysis');
    let et = await goto(page, '/trend-analysis');
    await shot(page, 'trend-analysis', 'empty');
    RESULTS['trend-analysis'] = { text: await txt(et), filters: await filterStruct(et), api: [] };

    // Find search box any way possible
    const searchSel = await et?.evaluate(() => {
      const inp = document.querySelector('input');
      return inp ? { placeholder: inp.placeholder, name: inp.name, id: inp.id, class: inp.className.slice(0,60) } : null;
    }).catch(() => null);
    console.log('  search input attrs:', searchSel);

    for (const topic of ['AI agents', 'breathwork']) {
      const apiCalls = await networkCapture(page, et, async () => {
        if (et) {
          const inp = et.locator('input').first();
          await inp.click({ timeout: 8000 }).catch(() => {});
          await inp.fill(topic, { timeout: 8000 }).catch(() => {});
          await page.waitForTimeout(500);
          await page.keyboard.press('Enter');
          await page.waitForTimeout(5000);
        }
      }, 2000);

      await shot(page, 'trend-analysis', `topic-${topic.replace(/\s+/g,'-')}`);
      const t = await txt(et);
      const table = await tableStruct(et);
      RESULTS['trend-analysis'].topics = RESULTS['trend-analysis'].topics || [];
      RESULTS['trend-analysis'].topics.push({ topic, text: t.slice(0,1500), table, api: apiCalls });

      // scroll to see graph + related
      await et?.evaluate(() => window.scrollBy(0, 400)).catch(() => {});
      await page.waitForTimeout(800);
      await shot(page, 'trend-analysis', `topic-${topic.replace(/\s+/g,'-')}-scrolled`);
      await et?.evaluate(() => window.scrollTo(0, 0)).catch(() => {});
      await page.waitForTimeout(500);
    }

    // ── 2. Trend Tracking ────────────────────────────────────────────────────
    console.log('\n📄 Trend Tracking');
    et = await goto(page, '/tracking');
    const trackApi = await networkCapture(page, et, null, 3000);
    await shot(page, 'trend-tracking', 'empty-state');
    RESULTS['trend-tracking'] = { text: await txt(et), filters: await filterStruct(et), api: trackApi };

    // ── 3–7. Overview pages ──────────────────────────────────────────────────
    const overviews = [
      { id: 'tiktok-insights',    sub: '/tiktok' },
      { id: 'trending-startups',  sub: '/startups' },
      { id: 'trending-products',  sub: '/products' },
      { id: 'meta-trends',        sub: '/meta-trends' },
      { id: 'reports-library',    sub: '/reports' },
    ];

    for (const { id, sub } of overviews) {
      console.log(`\n📄 ${id}`);
      et = await goto(page, sub);
      const apiCalls = await networkCapture(page, et, null, 3000);
      await shot(page, id, 'full');

      // scroll middle
      await et?.evaluate(() => window.scrollBy(0, 500)).catch(() => {});
      await page.waitForTimeout(800);
      await shot(page, id, 'scrolled');

      RESULTS[id] = {
        text: await txt(et),
        table: await tableStruct(et),
        filters: await filterStruct(et),
        api: apiCalls,
      };
    }

    // ── Collect existing dashboard/database data ──────────────────────────────
    console.log('\n📄 Dashboard (re-visit for API capture)');
    et = await goto(page, '/dashboard');
    const dashApi = await networkCapture(page, et, null, 4000);
    await shot(page, 'dashboard', 'api-visit');
    RESULTS['dashboard'] = { text: await txt(et), api: dashApi };

    console.log('\n📄 Trends Database (re-visit for API + full text)');
    et = await goto(page, '/database');
    const dbApi = await networkCapture(page, et, null, 4000);
    await shot(page, 'trends-database', 'api-visit');
    RESULTS['trends-database'] = { text: await txt(et), table: await tableStruct(et), filters: await filterStruct(et), api: dbApi };

  } finally {
    fs.writeFileSync(path.join(DOCS_DIR, 'raw-data.json'), JSON.stringify(RESULTS, null, 2));
    console.log('\n✅ Saved docs/ux/raw-data.json');
    await browser.close();
  }
}

main().catch(e => { console.error(e); process.exit(1); });
