/**
 * UX Reconnaissance script for Exploding Topics Pro.
 * Run: node scripts/ux_recon.mjs
 * Uses real Chrome + saved session to bypass bot detection.
 */

import { chromium } from '/Users/mgr/.npm/_npx/9833c18b2d85bc59/node_modules/playwright/index.mjs';
import fs from 'fs';
import path from 'path';

const AUTH_FILE = path.resolve('./auth.json');
const DOCS_DIR = path.resolve('./docs/ux');
const BASE_URL = 'https://www.semrush.com/app/exploding-topics/pro';

const PAGES = [
  { id: 'dashboard',         path: '/dashboard',         label: 'Dashboard' },
  { id: 'trends-database',   path: '/database',          label: 'Trends Database' },
  { id: 'trend-analysis',    path: '/trend-analysis',    label: 'Trend Analysis' },
  { id: 'trend-tracking',    path: '/tracking',          label: 'Trend Tracking' },
  { id: 'tiktok-insights',   path: '/tiktok',            label: 'TikTok Insights' },
  { id: 'trending-startups', path: '/startups',          label: 'Trending Startups' },
  { id: 'trending-products', path: '/products',          label: 'Trending Products' },
  { id: 'meta-trends',       path: '/meta-trends',       label: 'Meta Trends' },
  { id: 'reports-library',   path: '/reports',           label: 'Reports Library' },
];

// For Trend Analysis: topics to probe
const ANALYSIS_TOPICS = ['AI agents', 'mushroom coffee', 'breathwork'];

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

async function waitForEtFrame(page, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const et = page.frames().find(f => f.url().includes('explodingtopics') && !f.url().includes('fls.'));
    if (et && et.url().length > 30) return et;
    await page.waitForTimeout(500);
  }
  return null;
}

async function captureNetworkFromFrame(etFrame, label) {
  const captured = [];
  etFrame.on('response', async (resp) => {
    const url = resp.url();
    if ((url.includes('/api/') || url.includes('graphql') || url.includes('.json')) && !url.includes('chunk')) {
      try {
        const body = await resp.json().catch(() => null);
        if (body) captured.push({ url, status: resp.status(), body });
      } catch {}
    }
  });
  return captured;
}

async function screenshotPage(page, etFrame, pageId, suffix = 'full') {
  const dir = path.join(DOCS_DIR, pageId);
  ensureDir(dir);
  const file = path.join(dir, `${suffix}.png`);
  await page.screenshot({ path: file, fullPage: true, type: 'png' });
  console.log(`  📸 ${pageId}/${suffix}.png`);
  return file;
}

async function getPageText(etFrame) {
  if (!etFrame) return '';
  return etFrame.evaluate(() => document.body?.innerText?.substring(0, 5000)).catch(() => '');
}

async function getNavItems(etFrame) {
  if (!etFrame) return [];
  return etFrame.evaluate(() => {
    return [...document.querySelectorAll('a, [role="link"], nav *')]
      .filter(el => el.offsetParent !== null && el.textContent.trim())
      .map(el => ({ tag: el.tagName, text: el.textContent.trim().substring(0, 60), href: el.href || '' }))
      .slice(0, 30);
  }).catch(() => []);
}

async function getFilters(etFrame) {
  if (!etFrame) return [];
  return etFrame.evaluate(() => {
    const selects = [...document.querySelectorAll('select, [role="combobox"], [role="listbox"]')];
    const buttons = [...document.querySelectorAll('button')].filter(b => b.textContent.trim().length > 0 && b.textContent.trim().length < 40);
    return {
      selects: selects.map(s => ({ label: s.getAttribute('aria-label') || s.closest('label')?.textContent || '', options: [...s.querySelectorAll('option')].map(o => o.text) })).slice(0, 10),
      buttons: buttons.map(b => b.textContent.trim()).slice(0, 20),
    };
  }).catch(() => ({}));
}

async function getTableStructure(etFrame) {
  if (!etFrame) return null;
  return etFrame.evaluate(() => {
    const table = document.querySelector('table, [role="table"], [role="grid"]');
    if (!table) return null;
    const headers = [...table.querySelectorAll('th, [role="columnheader"]')].map(h => h.textContent.trim());
    const rows = [...table.querySelectorAll('tr, [role="row"]')].slice(1, 4).map(row =>
      [...row.querySelectorAll('td, [role="cell"]')].map(c => c.textContent.trim().substring(0, 50))
    );
    return { headers, sampleRows: rows };
  }).catch(() => null);
}

async function getCardStructure(etFrame) {
  if (!etFrame) return null;
  return etFrame.evaluate(() => {
    const cards = [...document.querySelectorAll('[class*="card"], [class*="Card"], article')].slice(0, 3);
    return cards.map(card => ({
      fields: [...card.querySelectorAll('*')]
        .filter(el => el.children.length === 0 && el.textContent.trim())
        .map(el => el.textContent.trim().substring(0, 60))
        .filter(Boolean)
        .slice(0, 15)
    }));
  }).catch(() => null);
}

async function getApiCallsFromFrame(etFrame, waitMs = 3000) {
  if (!etFrame) return [];
  const calls = [];
  const handler = async (resp) => {
    const url = resp.url();
    if ((url.includes('/api/') || url.includes('/v1/') || url.includes('/v2/') || url.includes('graphql')) && !url.includes('google') && !url.includes('doubleclick')) {
      try {
        const body = await resp.json().catch(() => null);
        calls.push({ url, status: resp.status(), method: resp.request().method(), body });
      } catch {}
    }
  };
  etFrame.on('response', handler);
  await etFrame.waitForTimeout(waitMs);
  etFrame.off('response', handler);
  return calls.slice(0, 5);
}

async function navigateToSection(page, sectionPath) {
  const fullUrl = `${BASE_URL}${sectionPath}`;
  await page.goto(fullUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  const et = await waitForEtFrame(page, 12000);
  if (et) await et.waitForLoadState('domcontentloaded').catch(() => {});
  await page.waitForTimeout(3000);
  return et;
}

async function clickNavInFrame(page, etFrame, y) {
  await page.mouse.click(104, y);
  await page.waitForTimeout(3000);
  return page.frames().find(f => f.url().includes('explodingtopics') && !f.url().includes('fls.'));
}

// ─── SECTION HANDLERS ────────────────────────────────────────────────────────

async function exploreDashboard(page, etFrame) {
  console.log('\n📄 Dashboard');
  await screenshotPage(page, etFrame, 'dashboard', 'full');

  const cards = await getCardStructure(etFrame);
  const nav = await getNavItems(etFrame);
  const text = await getPageText(etFrame);

  // Scroll down to see more cards
  await etFrame?.evaluate(() => window.scrollBy(0, 400)).catch(() => {});
  await page.waitForTimeout(1000);
  await screenshotPage(page, etFrame, 'dashboard', 'scrolled');

  return { nav, cards, text_excerpt: text.substring(0, 1000) };
}

async function exploreTrendsDatabase(page, etFrame) {
  console.log('\n📄 Trends Database');
  await screenshotPage(page, etFrame, 'trends-database', 'full');

  const filters = await getFilters(etFrame);
  const table = await getTableStructure(etFrame);
  const text = await getPageText(etFrame);

  // Click first category to see drill-down
  await etFrame?.evaluate(() => {
    const firstCat = document.querySelector('[class*="category"], [class*="Category"], li a');
    if (firstCat) firstCat.click();
  }).catch(() => {});
  await page.waitForTimeout(2500);
  await screenshotPage(page, etFrame, 'trends-database', 'category-drilldown');

  // Capture a sample trend card
  const cardData = await getCardStructure(etFrame);

  return { filters, table, cardData, text_excerpt: text.substring(0, 2000) };
}

async function exploreTrendAnalysis(page, etFrame) {
  console.log('\n📄 Trend Analysis');
  await screenshotPage(page, etFrame, 'trend-analysis', 'empty');

  const results = [];
  for (const topic of ANALYSIS_TOPICS.slice(0, 2)) {
    console.log(`  🔍 Analyzing: ${topic}`);
    // Type in search
    const searchInput = etFrame?.locator('input[type="text"], input[placeholder*="topic"], input[placeholder*="search"]').first();
    if (searchInput) {
      await searchInput.fill(topic);
      await page.waitForTimeout(500);
      await searchInput.press('Enter');
      await page.waitForTimeout(4000);
      await screenshotPage(page, etFrame, 'trend-analysis', `topic-${topic.replace(/\s+/g, '-')}`);
      const text = await getPageText(etFrame);
      const table = await getTableStructure(etFrame);
      results.push({ topic, text_excerpt: text.substring(0, 1000), table });
      // Clear for next search
      await searchInput.fill('');
      await page.waitForTimeout(500);
    }
  }

  return { topics: results };
}

async function exploreTrendTracking(page, etFrame) {
  console.log('\n📄 Trend Tracking');
  await screenshotPage(page, etFrame, 'trend-tracking', 'empty-state');
  const text = await getPageText(etFrame);
  const filters = await getFilters(etFrame);
  return { text_excerpt: text.substring(0, 500), filters };
}

async function exploreOverview(page, etFrame, id) {
  console.log(`\n📄 ${id} (overview)`);
  await screenshotPage(page, etFrame, id, 'full');
  const text = await getPageText(etFrame);
  const table = await getTableStructure(etFrame);
  const filters = await getFilters(etFrame);
  const cards = await getCardStructure(etFrame);
  return { text_excerpt: text.substring(0, 1500), table, filters, cards };
}

// ─── MAIN ─────────────────────────────────────────────────────────────────────

async function main() {
  ensureDir(DOCS_DIR);
  console.log('🚀 Starting UX recon with real Chrome + saved session...');

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

  const data = {};

  try {
    // ── Dashboard ──
    let etFrame = await navigateToSection(page, '/dashboard');
    if (!etFrame) { console.error('❌ Cannot load iframe — ac.explodingtopics.com blocked'); await browser.close(); return; }
    data.dashboard = await exploreDashboard(page, etFrame);

    // ── Trends Database ──
    etFrame = await navigateToSection(page, '/database');
    data['trends-database'] = await exploreTrendsDatabase(page, etFrame);

    // ── Trend Analysis ──
    etFrame = await navigateToSection(page, '/trend-analysis');
    data['trend-analysis'] = await exploreTrendAnalysis(page, etFrame);

    // ── Trend Tracking ──
    etFrame = await navigateToSection(page, '/tracking');
    data['trend-tracking'] = await exploreTrendTracking(page, etFrame);

    // ── Overview pages ──
    for (const p of ['tiktok-insights', 'trending-startups', 'trending-products', 'meta-trends', 'reports-library']) {
      const pathMap = {
        'tiktok-insights': '/tiktok', 'trending-startups': '/startups',
        'trending-products': '/products', 'meta-trends': '/meta-trends', 'reports-library': '/reports',
      };
      etFrame = await navigateToSection(page, pathMap[p] || `/${p}`);
      data[p] = await exploreOverview(page, etFrame, p);
    }

    // Save raw data
    fs.writeFileSync(path.join(DOCS_DIR, 'raw-data.json'), JSON.stringify(data, null, 2));
    console.log('\n✅ Raw data saved to docs/ux/raw-data.json');

  } finally {
    await browser.close();
  }
}

main().catch(err => { console.error(err); process.exit(1); });
