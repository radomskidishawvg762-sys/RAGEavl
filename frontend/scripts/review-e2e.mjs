/** Read-only regression audit against real Vite + FastAPI + persisted runs.
 * Servers must already be running. No imports, evaluation launches or Judge calls.
 * Usage: node scripts/review-e2e.mjs [base URL]
 * Optional REVIEW_RUN_IDS is a comma-separated pair of preserved run IDs.
 */
import { chromium } from 'playwright-core';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';

const base = process.argv[2] ?? 'http://127.0.0.1:5173';
const runIds = (process.env.REVIEW_RUN_IDS ??
  '3b8cb849-9a61-4b6d-ab62-8cb25d2cb494,b8759669-4352-432f-849c-f5cfcb3db570').split(',');
const checks = [];
function check(name, ok, detail) {
  checks.push({ name, ok: Boolean(ok), detail });
  console.log(JSON.stringify(checks.at(-1)));
}
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const pageErrors = [], badResponses = [];
  page.on('pageerror', e => pageErrors.push(e.message));
  page.on('response', r => {
    if (r.url().includes('/api/') && r.status() >= 400) badResponses.push({ path: new URL(r.url()).pathname, status: r.status() });
  });
  async function get(path) {
    const response = await page.request.get(base + path);
    if (!response.ok()) throw new Error(`HTTP ${response.status()} at ${path}`);
    return response.json();
  }
  async function goto(path) {
    await page.goto(base + path);
    await page.waitForLoadState('networkidle');
  }
  const reports = await Promise.all(runIds.map(id => get(`/api/evaluations/${id}/report`)));
  const datasets = await get('/api/datasets?page_size=100');
  if (!datasets.items.length) throw new Error('Need a persisted dataset for the detail-route check');
  const routes = ['/', '/projects', '/evaluations', '/evaluations/new', `/evaluations/${runIds[0]}`,
    '/compare', '/regression', '/quality-gate', '/datasets', '/datasets/import',
    `/datasets/${datasets.items[0].id}`, '/failures', '/profiles', '/metrics', '/settings'];
  const overflow = [];
  for (const width of [1280, 1440, 1600]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of routes) {
      await goto(route);
      const extent = await page.evaluate(() => {
        const main = document.querySelector('main');
        return { document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          main: main ? main.scrollWidth - main.clientWidth : 0 };
      });
      if (extent.document > 1 || extent.main > 1) overflow.push({ width, route, ...extent });
    }
  }
  check('45 route/viewport combinations: no page overflow', overflow.length === 0, { combinations: 45, overflow });

  const headers = [], numberWraps = [], evidenceWraps = [], panels = [];
  let diagnosticEnglish = 0, visibleUrls = 0, expandedErrors = 0, errorRows = 0;
  for (const width of [1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [index, id] of runIds.entries()) {
      await goto(`/evaluations/${id}`);
      const panel = page.locator('section').filter({ has: page.getByRole('heading', { name: '运行级诊断汇总', exact: true }) });
      const values = await panel.locator('[class*="execVal"]').allTextContents();
      const expected = reports[index].run_level_diagnoses;
      panels.push({ width, id, values, expected: [expected.total_failure_records, expected.total_diagnoses, expected.undetermined_count].map(String) });
      await page.getByRole('tab', { name: /^指标/ }).click();
      const measured = await page.locator('table:has([data-testid^="metric-"])').evaluateAll(tables => tables.map(table => {
        const head = table.querySelector('thead');
        const threshold = [...table.querySelectorAll('th')].find(th => th.textContent === '阈值');
        const r = document.createRange(); r.selectNodeContents(threshold);
        return { height: head.getBoundingClientRect().height, thresholdLines: new Set([...r.getClientRects()].filter(x => x.width).map(x => Math.round(x.top))).size };
      }));
      headers.push({ width, id, measured });
      const wrapped = await page.locator('[data-testid^="metric-"] > td:nth-child(2)').evaluateAll(cells => cells.filter(cell => {
        const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
        const tops = []; let text;
        while ((text = walker.nextNode())) {
          const r = document.createRange(); r.selectNodeContents(text);
          tops.push(...[...r.getClientRects()].filter(x => x.width).map(x => Math.round(x.top)));
        }
        return new Set(tops).size > 1;
      }).map(c => c.textContent));
      numberWraps.push(...wrapped.map(value => ({ width, id, value })));
      await page.getByRole('tab', { name: /^诊断/ }).click();
      const text = await page.locator('main').innerText();
      diagnosticEnglish += (text.match(/cannot reliably judge/gi) ?? []).length;
      visibleUrls += (await page.getByTestId('error-item').allInnerTexts()).filter(t => /https?:\/\//i.test(t)).length;
      errorRows += await page.getByTestId('error-item').count();
      expandedErrors += await page.locator('[data-testid="error-item"] details[open]').count();
      if (index === 0) {
        await page.getByRole('tab', { name: /^证据/ }).click();
        await page.getByTestId('evidence-table').waitFor();
        const wrapped = await page.locator('[data-testid="evidence-row"] > td:nth-child(3), [data-testid="evidence-row"] > td:nth-child(4)').evaluateAll(cells => cells.filter(cell => {
          const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
          const tops = []; let text;
          while ((text = walker.nextNode())) {
            const r = document.createRange(); r.selectNodeContents(text);
            tops.push(...[...r.getClientRects()].filter(x => x.width).map(x => Math.round(x.top)));
          }
          return new Set(tops).size > 1;
        }).map(c => c.textContent));
        evidenceWraps.push(...wrapped.map(value => ({ width, value })));
      }
    }
  }
  check('Metric table headers have equal heights and single-line thresholds', headers.every(h => h.measured.length >= 1 && new Set(h.measured.map(m => m.height)).size === 1 && h.measured.every(m => m.thresholdLines === 1)), headers);
  check('Evidence type/source cells do not wrap', evidenceWraps.length === 0, { wrapped: evidenceWraps.length });
  check('Metric scores do not wrap', numberWraps.length === 0, { wrapped: numberWraps.length });
  check('Fixed diagnostic phrase localized', diagnosticEnglish === 0, { hits: diagnosticEnglish });
  check('Execution-error details collapsed; visible messages contain no URLs', errorRows > 0 && expandedErrors === 0 && visibleUrls === 0, { errorRows, expandedErrors, visibleUrls });
  check('Run diagnosis counts match real report API', panels.every(p => JSON.stringify(p.values) === JSON.stringify(p.expected)), panels);
  await page.setViewportSize({ width: 1280, height: 900 });
  await goto(`/evaluations/${runIds[0]}`);
  await page.getByRole('tab', { name: /^证据/ }).click();
  const stress = await page.locator('[data-testid="evidence-row"] > td:nth-child(2)').first().evaluate(cell => {
    cell.textContent = 'long_unbroken_metric_identifier_'.repeat(30);
    const range = document.createRange(); range.selectNodeContents(cell.firstChild);
    const main = document.querySelector('main');
    return { lines: new Set([...range.getClientRects()].map(r => Math.round(r.top))).size,
      documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      mainOverflow: main.scrollWidth - main.clientWidth };
  });
  check('Long second-column token wraps without page overflow', stress.lines > 1 && stress.lines <= 80 && stress.documentOverflow <= 1 && stress.mainOverflow <= 1, stress);
  check('No browser exceptions or failed API responses', pageErrors.length === 0 && badResponses.length === 0, { pageErrors, badResponses });
} finally {
  await browser.close();
}
const out = path.resolve('../exports/review-2026-10-11');
await fs.mkdir(out, { recursive: true });
await fs.writeFile(path.join(out, 'review-results.json'), JSON.stringify(checks, null, 2));
if (checks.some(c => !c.ok)) process.exitCode = 1;
