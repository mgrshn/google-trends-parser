/**
 * Full UX recon — one browser session, never closes between sections,
 * actively clicks cards, applies filters, enters search queries.
 */
import { chromium } from '/Users/mgr/.npm/_npx/9833c18b2d85bc59/node_modules/playwright/index.mjs';
import fs from 'fs';
import path from 'path';

const AUTH = path.resolve('./auth.json');
const DOCS = path.resolve('./docs/ux');
const BASE = 'https://www.semrush.com/app/exploding-topics/pro/dashboard';

// Viewport-level nav coords (confirmed from earlier session)
const NAV = {
  dashboard:         { x: 97, y: 156 },
  'trend-tracking':  { x: 97, y: 210 },
  'trends-database': { x: 97, y: 264 },
  'tiktok-insights': { x: 97, y: 318 },
  'trend-analysis':  { x: 97, y: 372 },
  'trending-startups':{ x: 97, y: 426 },
  'trending-products':{ x: 97, y: 480 },
  'meta-trends':     { x: 97, y: 534 },
  'reports-library': { x: 97, y: 588 },
};

// ── helpers ──────────────────────────────────────────────────────────────────
function dir(id) { const d = path.join(DOCS, id); fs.mkdirSync(d, { recursive: true }); return d; }

async function shot(page, id, name) {
  const fp = path.join(dir(id), `${name}.png`);
  await page.screenshot({ path: fp, type: 'png' });
  console.log(`  📸 ${id}/${name}.png`);
}

async function shotFull(page, id, name) {
  const fp = path.join(dir(id), `${name}.png`);
  await page.screenshot({ path: fp, type: 'png', fullPage: true });
  console.log(`  📸 ${id}/${name}.png [full]`);
}

async function waitEt(page, ms = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const f = page.frames().find(f => f.url().includes('ac.explodingtopics'));
    if (f) return f;
    await page.waitForTimeout(500);
  }
  return null;
}

async function clickNav(page, id) {
  const { x, y } = NAV[id];
  await page.mouse.click(x, y);
  await page.waitForTimeout(3500);
  const et = await waitEt(page, 8000);
  if (et) await et.waitForLoadState('domcontentloaded').catch(() => {});
  await page.waitForTimeout(2000);
  return et ?? page.frames().find(f => f.url().includes('ac.explodingtopics'));
}

// Collect API calls for `ms` ms; returns array of {url, method, shape}
function apiCollector(page, et) {
  const calls = [];
  const handle = async resp => {
    const u = resp.url();
    if (u.includes('google') || u.includes('doubleclick') || u.includes('.chunk.')) return;
    if (u.includes('/api/') || u.includes('/v1/') || u.includes('/v2/') || u.includes('graphql') || /\.json(\?|$)/.test(u)) {
      const body = await resp.json().catch(() => null);
      if (!body) return;
      calls.push({
        url: u.replace(/[?#].*/, ''),
        qs:  u.includes('?') ? Object.fromEntries(new URLSearchParams(u.split('?')[1])) : {},
        method: resp.request().method(),
        shape:  shapeOf(body),
        sample: JSON.stringify(body).slice(0, 600),
      });
    }
  };
  page.on('response', handle);
  if (et) et.on('response', handle);
  return { calls, stop: () => { page.off('response', handle); if (et) et.off('response', handle); } };
}

function shapeOf(v, d = 0) {
  if (d > 3) return '…';
  if (Array.isArray(v)) return `[${shapeOf(v[0], d+1)}, …×${v.length}]`;
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).slice(0,8).map(([k,vv])=>[k,shapeOf(vv,d+1)]));
  return typeof v;
}

async function etInner(et, sel) {
  return et?.evaluate(s => document.querySelector(s)?.innerText?.trim() ?? '', sel).catch(() => '') ?? '';
}
async function etAll(et, sel) {
  return et?.evaluate(s => [...document.querySelectorAll(s)].map(el=>el.innerText?.trim()).filter(Boolean), sel).catch(() => []) ?? [];
}

// ── Section scrapers ──────────────────────────────────────────────────────────

async function doDashboard(page, et, R) {
  console.log('\n━━ DASHBOARD ━━');
  const col = apiCollector(page, et);
  await page.waitForTimeout(4000);
  col.stop();
  await shotFull(page, 'dashboard', '01-full');

  // read card data
  const cardData = await et?.evaluate(() => {
    return [...document.querySelectorAll('[class*="card"],[class*="Card"],[class*="topic"],[class*="Topic"]')]
      .filter(el => el.querySelector('button, a') && el.getBoundingClientRect().width > 100)
      .slice(0, 8).map(card => card.innerText.trim().slice(0, 200));
  }).catch(() => []) ?? [];

  // Categories dropdown — click it
  const catDropdown = et?.locator('select, [role="combobox"]').first();
  if (catDropdown) {
    await catDropdown.click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(800);
    await shot(page, 'dashboard', '02-categories-dropdown');
    await page.keyboard.press('Escape');
  }

  // Click first trend card
  const firstCard = et?.locator('[class*="card"],[class*="Card"],article').first();
  const cardClicked = await firstCard?.click({ timeout: 5000 }).catch(() => false);
  if (cardClicked !== false) {
    await page.waitForTimeout(3000);
    await shot(page, 'dashboard', '03-card-click-result');
    await page.goBack({ waitUntil: 'domcontentloaded' }).catch(() => {});
    await page.waitForTimeout(2000);
  }

  R.dashboard = { api: col.calls, cardData, semrushUrl: page.url() };
}

async function doTrendsDatabase(page, et, R) {
  console.log('\n━━ TRENDS DATABASE ━━');
  const col = apiCollector(page, et);
  await page.waitForTimeout(3000);
  col.stop();
  await shotFull(page, 'trends-database', '01-categories');

  // Read category list
  const categories = await etAll(et, '[class*="category"],[class*="Category"],li').then(list =>
    list.filter(t => /\d/.test(t)).slice(0, 20)
  );

  // Click Business (first category)
  const bizLink = et?.locator('text=Business').first();
  await bizLink?.click({ timeout: 6000 }).catch(() => {});
  await page.waitForTimeout(3500);
  await shotFull(page, 'trends-database', '02-business-category');

  // Read filter bar
  const col2 = apiCollector(page, et);
  const filterBar = await et?.evaluate(() => {
    const labels = [...document.querySelectorAll('label,[class*="filter"],[class*="Filter"]')].map(el => el.innerText?.trim()).filter(Boolean);
    const selects = [...document.querySelectorAll('select,[role="combobox"]')].map(s => ({
      label: s.getAttribute('aria-label') || s.id || '',
      options: [...s.querySelectorAll('option,[role="option"]')].map(o => o.innerText?.trim()).filter(Boolean).slice(0,8),
    }));
    return { labels, selects };
  }).catch(() => null);

  // Open Sort By dropdown
  const sortBtn = et?.locator('text=Sort By, text=SORT BY, [aria-label*="sort"], [aria-label*="Sort"]').first();
  await sortBtn?.click({ timeout: 4000 }).catch(() => {});
  await page.waitForTimeout(800);
  await shot(page, 'trends-database', '03-sort-dropdown');
  await page.keyboard.press('Escape');

  // Change timeframe to 1 Year
  const tfSelect = et?.locator('select,[role="combobox"]').nth(1);
  await tfSelect?.click({ timeout: 4000 }).catch(() => {});
  await page.waitForTimeout(600);
  await shot(page, 'trends-database', '04-timeframe-dropdown');
  await page.keyboard.press('Escape');

  // Click first trend card to see detail
  const firstCard = et?.locator('[class*="card"],[class*="Card"],[class*="topic"],[class*="Topic"]').first();
  await firstCard?.click({ timeout: 6000 }).catch(() => {});
  await page.waitForTimeout(4000);
  await shotFull(page, 'trends-database', '05-trend-detail');

  const detailText = await etAll(et, '*').then(t => t.join('\n').slice(0, 3000)).catch(() => '');

  // Search box
  await page.goBack({ waitUntil: 'domcontentloaded' }).catch(() => {});
  await page.waitForTimeout(2000);
  const searchBox = et?.locator('[placeholder*="Search"], [aria-label*="Search"], input[type="text"]').first();
  await searchBox?.click({ timeout: 5000 }).catch(() => {});
  await searchBox?.fill('AI', { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(2500);
  await shot(page, 'trends-database', '06-search-AI');
  col2.stop();

  R['trends-database'] = {
    semrushUrl: page.url(), categories, filterBar,
    api: [...col.calls, ...col2.calls].slice(0, 8), detailText,
  };
}

async function doTrendAnalysis(page, et, R) {
  console.log('\n━━ TREND ANALYSIS ━━');
  await page.waitForTimeout(2000);
  await shotFull(page, 'trend-analysis', '01-empty');

  const text0 = await et?.evaluate(() => document.body?.innerText?.slice(0, 500)).catch(() => '') ?? '';
  const topics = [];

  for (const topic of ['AI agents', 'matcha latte', 'breathwork']) {
    console.log(`  🔍 ${topic}`);
    const col = apiCollector(page, et);

    // find input
    const inp = et?.locator('input[type="text"],input:not([type]),input[type="search"]').first();
    const inpVisible = await inp?.isVisible({ timeout: 5000 }).catch(() => false);
    if (inpVisible) {
      await inp.triple_click?.().catch(() => inp.click());
      await inp.fill(topic, { timeout: 5000 }).catch(() => page.keyboard.type(topic));
    } else {
      // click center of content area and type
      await page.mouse.click(756, 400);
      await page.waitForTimeout(300);
      await page.keyboard.type(topic, { delay: 50 });
    }
    await page.keyboard.press('Enter');
    await page.waitForTimeout(6000);
    col.stop();

    await shotFull(page, 'trend-analysis', `02-${topic.replace(/\s+/g,'-')}`);
    await et?.evaluate(() => window.scrollBy(0, 600)).catch(() => {});
    await page.waitForTimeout(600);
    await shot(page, 'trend-analysis', `02-${topic.replace(/\s+/g,'-')}-chart`);
    await et?.evaluate(() => window.scrollBy(0, 600)).catch(() => {});
    await page.waitForTimeout(600);
    await shot(page, 'trend-analysis', `02-${topic.replace(/\s+/g,'-')}-related`);

    // Timeframe tabs
    const timeTabs = await etAll(et, '[role="tab"],button[class*="period"],button[class*="time"]');

    const bodyText = await et?.evaluate(() => document.body?.innerText?.slice(0, 3000)).catch(() => '') ?? '';

    topics.push({ topic, api: col.calls, timeTabs, bodyText });

    // go back to empty state for next topic
    await clickNav(page, 'trend-analysis');
    et = page.frames().find(f => f.url().includes('ac.explodingtopics')) ?? et;
    await page.waitForTimeout(1500);
  }

  R['trend-analysis'] = { emptyStateText: text0, topics };
}

async function doTrendTracking(page, et, R) {
  console.log('\n━━ TREND TRACKING ━━');
  const col = apiCollector(page, et);
  await page.waitForTimeout(3000);
  col.stop();
  await shotFull(page, 'trend-tracking', '01-state');

  const bodyText = await et?.evaluate(() => document.body?.innerText?.slice(0, 2000)).catch(() => '') ?? '';
  const buttons  = await etAll(et, 'button');

  // Click "Create New Project" to see modal
  const createBtn = et?.locator('text=Create New Project, text=Add Topics, button').first();
  const clicked = await createBtn?.click({ timeout: 5000 }).catch(() => false);
  if (clicked !== false) {
    await page.waitForTimeout(2000);
    await shot(page, 'trend-tracking', '02-create-modal');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(1000);
  }

  R['trend-tracking'] = { bodyText, buttons, api: col.calls };
}

async function doTikTok(page, et, R) {
  console.log('\n━━ TIKTOK INSIGHTS ━━');
  const col = apiCollector(page, et);
  await page.waitForTimeout(3000);
  col.stop();
  await shotFull(page, 'tiktok-insights', '01-full');

  const table  = await et?.evaluate(() => {
    const headers = [...document.querySelectorAll('th,[role="columnheader"]')].map(h=>h.innerText?.trim()).filter(Boolean);
    const rows = [...document.querySelectorAll('tr,[role="row"]')].slice(1,4).map(r =>
      [...r.querySelectorAll('td,[role="cell"]')].map(c=>c.innerText?.trim()?.slice(0,80)));
    return { headers, rows };
  }).catch(() => null);

  // Filters
  const filters = await et?.evaluate(() => {
    const combos = [...document.querySelectorAll('select,[role="combobox"]')].map(s=>({
      label: s.getAttribute('aria-label') || s.closest('label')?.innerText?.trim() || '',
      selected: s.value || s.innerText?.trim(),
    }));
    return combos;
  }).catch(() => []);

  // Click first combobox to see options
  const firstCombo = et?.locator('select,[role="combobox"]').first();
  await firstCombo?.click({ timeout: 4000 }).catch(() => {});
  await page.waitForTimeout(600);
  await shot(page, 'tiktok-insights', '02-filter-dropdown');
  await page.keyboard.press('Escape');

  // click first row to see if detail opens
  const firstRow = et?.locator('tr,[role="row"]').nth(1);
  await firstRow?.click({ timeout: 4000 }).catch(() => {});
  await page.waitForTimeout(2500);
  await shot(page, 'tiktok-insights', '03-row-click');

  R['tiktok-insights'] = { table, filters, api: col.calls };
}

async function doStartups(page, et, R) {
  console.log('\n━━ TRENDING STARTUPS ━━');
  const col = apiCollector(page, et);
  await page.waitForTimeout(3000);
  col.stop();
  await shotFull(page, 'trending-startups', '01-full');

  const table = await et?.evaluate(() => {
    const headers = [...document.querySelectorAll('th,[role="columnheader"]')].map(h=>h.innerText?.trim()).filter(Boolean);
    const rows = [...document.querySelectorAll('tr,[role="row"]')].slice(1,3).map(r =>
      [...r.querySelectorAll('td,[role="cell"]')].map(c=>c.innerText?.trim()?.slice(0,80)));
    return { headers, rows };
  }).catch(() => null);

  // apply a filter: Category → Health
  const catFilter = et?.locator('[aria-label*="Category"],[aria-label*="category"],select').first();
  await catFilter?.click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(600);
  await shot(page, 'trending-startups', '02-category-filter');
  await page.keyboard.press('Escape');

  // click a startup row
  const firstRow = et?.locator('tr,[role="row"]').nth(1);
  await firstRow?.click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(3000);
  await shot(page, 'trending-startups', '03-startup-detail');

  R['trending-startups'] = { table, api: col.calls };
}

async function doProducts(page, et, R) {
  console.log('\n━━ TRENDING PRODUCTS ━━');
  const col = apiCollector(page, et);
  await page.waitForTimeout(3000);
  col.stop();
  await shotFull(page, 'trending-products', '01-full');
  await et?.evaluate(() => window.scrollBy(0, 500)).catch(() => {});
  await page.waitForTimeout(600);
  await shot(page, 'trending-products', '02-scrolled');

  const table = await et?.evaluate(() => {
    const headers = [...document.querySelectorAll('th,[role="columnheader"]')].map(h=>h.innerText?.trim()).filter(Boolean);
    const rows = [...document.querySelectorAll('tr,[role="row"]')].slice(1,3).map(r =>
      [...r.querySelectorAll('td,[role="cell"]')].map(c=>c.innerText?.trim()?.slice(0,80)));
    return { headers, rows };
  }).catch(() => null);

  R['trending-products'] = { table, api: col.calls };
}

async function doMetaTrends(page, et, R) {
  console.log('\n━━ META TRENDS ━━');
  const col = apiCollector(page, et);
  await page.waitForTimeout(3000);
  col.stop();
  await shotFull(page, 'meta-trends', '01-full');

  // read cards
  const cards = await etAll(et, '[class*="card"],[class*="Card"],article,h2,h3');
  await et?.evaluate(() => window.scrollBy(0, 600)).catch(() => {});
  await page.waitForTimeout(600);
  await shot(page, 'meta-trends', '02-scrolled');

  // click first card → View Analysis
  await et?.evaluate(() => window.scrollTo(0,0)).catch(() => {});
  const viewBtn = et?.locator('text=View Analysis').first();
  await viewBtn?.click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(4000);
  await shotFull(page, 'meta-trends', '03-analysis-detail');
  await et?.evaluate(() => window.scrollBy(0, 600)).catch(() => {});
  await page.waitForTimeout(600);
  await shot(page, 'meta-trends', '04-analysis-scrolled');

  R['meta-trends'] = { cards, api: col.calls };
}

async function doReports(page, et, R) {
  console.log('\n━━ REPORTS LIBRARY ━━');
  const col = apiCollector(page, et);
  await page.waitForTimeout(3000);
  col.stop();
  await shotFull(page, 'reports-library', '01-full');
  const bodyText = await et?.evaluate(() => document.body?.innerText?.slice(0, 2000)).catch(() => '') ?? '';
  R['reports-library'] = { bodyText, api: col.calls };
}

// ─── MAIN ────────────────────────────────────────────────────────────────────
async function main() {
  fs.mkdirSync(DOCS, { recursive: true });

  const browser = await chromium.launch({
    channel: 'chrome',
    headless: false,
    args: ['--window-size=1512,900','--disable-blink-features=AutomationControlled'],
  });
  const ctx = await browser.newContext({
    storageState: AUTH,
    viewport: { width: 1512, height: 900 },
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  });
  const page = await ctx.newPage();
  const R = {};

  try {
    console.log('🚀 Loading dashboard…');
    await page.goto(BASE, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    let et = await waitEt(page, 18000);
    if (!et) { console.error('❌ iframe never loaded'); return; }
    await et.waitForLoadState('domcontentloaded').catch(() => {});
    await page.waitForTimeout(4000);
    console.log('  ✅ iframe:', et.url().slice(0, 70));

    await doDashboard(page, et, R);

    et = await clickNav(page, 'trends-database');
    await doTrendsDatabase(page, et, R);

    et = await clickNav(page, 'trend-analysis');
    await doTrendAnalysis(page, et, R);

    et = await clickNav(page, 'trend-tracking');
    await doTrendTracking(page, et, R);

    et = await clickNav(page, 'tiktok-insights');
    await doTikTok(page, et, R);

    et = await clickNav(page, 'trending-startups');
    await doStartups(page, et, R);

    et = await clickNav(page, 'trending-products');
    await doProducts(page, et, R);

    et = await clickNav(page, 'meta-trends');
    await doMetaTrends(page, et, R);

    et = await clickNav(page, 'reports-library');
    await doReports(page, et, R);

    fs.writeFileSync(path.join(DOCS, 'raw-data.json'), JSON.stringify(R, null, 2));
    console.log('\n✅ All done! docs/ux/raw-data.json saved');
    console.log('   Browser stays open for 30s for manual review…');
    await page.waitForTimeout(30000);

  } finally {
    await browser.close();
  }
}

main().catch(e => { console.error(e); process.exit(1); });
