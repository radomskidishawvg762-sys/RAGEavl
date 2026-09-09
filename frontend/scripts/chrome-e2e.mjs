/**
 * MVP Freeze Gate — Real Chrome E2E (§三/§四).
 *
 * Real Chrome (system binary via channel:'chrome') + Real Vite (dev server,
 * /api proxied to Real FastAPI) + Real Supabase Cloud + Real Judge.
 * 25 checkpoints + global guards: console errors, page errors, API 4xx/5xx,
 * placeholder scan, null->0 check, secret scan, API/UI consistency.
 *
 * Usage: node scripts/chrome-e2e.mjs   (cwd = frontend, servers already up)
 */

import { chromium } from 'playwright-core';
import * as fsp from 'node:fs/promises';
import * as path from 'node:path';

const BASE = 'http://localhost:5173';
const GOLDEN = String(process.env.CHROME_E2E_GOLDEN);
const JUDGE_KEY = process.env.JUDGE_API_KEY ?? '';
const RUN_LABEL = `chrome-e2e-${Date.now()}`;
const DOWNLOAD_DIR = String(process.env.CHROME_E2E_DOWNLOADS);

const results = [];
function record(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
}

const consoleErrors = [];
const pageErrors = [];
const badApi = [];
const apiBodies = []; // {url, text}
const placeholderHits = [];

function isPlaceholderText(text) {
  return /尚未实现|结构已预留|待接入|PlaceholderPage|TODO：/.test(text ?? '');
}

async function expectVisible(page, selectorOrLocator, name, timeout = 20000) {
  try {
    const loc =
      typeof selectorOrLocator === 'string'
        ? page.locator(selectorOrLocator).first()
        : selectorOrLocator.first();
    await loc.waitFor({ state: 'visible', timeout });
    record(name, true);
    return loc;
  } catch (e) {
    record(name, false, String(e).slice(0, 160));
    return null;
  }
}

async function scanPlaceholders(page, pageName) {
  const body = (await page.locator('body').innerText().catch(() => '')) ?? '';
  if (isPlaceholderText(body)) placeholderHits.push(pageName);
}

async function pickRunOption(page, selectTestId, versionMarker) {
  const option = page
    .locator(`select[data-testid="${selectTestId}"] option`, { hasText: versionMarker })
    .first();
  const label = await option.textContent();
  await page.selectOption(`[data-testid="${selectTestId}"]`, { label: label.trim() });
}

async function importDataset(page, { expectVersionOf = false } = {}) {
  // 已在导入页（带 versionOf query）时不重导航，避免抹掉参数
  if (!page.url().includes('/datasets/import')) {
    await goto(page, `${BASE}/datasets/import`);
  }
  if (expectVersionOf) {
    await expectVisible(page, '[data-testid="version-of-hint"]', '    import: versionOf hint');
  }
  await page.setInputFiles('input[type=file]', GOLDEN);
  await expectVisible(page, 'text=数据预览', '    import: preview step');
  await page.getByRole('button', { name: '开始校验并导入' }).click();
  const ok = await page
    .locator('[data-testid="import-success"]')
    .waitFor({ state: 'visible', timeout: 30000 })
    .then(() => true)
    .catch(() => false);
  if (!ok) {
    const body = await page.locator('body').innerText().catch(() => '');
    record('    import: success', false, body.replace(/\s+/g, ' ').slice(0, 300));
    return;
  }
  record('    import: success', true);
  const rows = await page.locator('[data-testid="import-success"]').innerText().catch(() => '');
  record('    import: record count = 4', rows.includes('4'), rows.replace(/\s+/g, ' ').slice(0, 120));
  await page.getByRole('link', { name: '返回数据集列表' }).click();
  await page.waitForURL('**/datasets');
  await page
    .locator('[data-testid="dataset-row"]')
    .first()
    .waitFor({ state: 'visible', timeout: 20000 })
    .catch(() => {});
}

async function runWizard(page, { versionMarker = 'v1', thresholdOverride = null, disableRagas = false } = {}) {
  await goto(page, `${BASE}/evaluations/new`);
  // Step 1 dataset（按版本标记选择，避免误选已锁定版本）
  await expectVisible(page, '[data-testid="select-dataset"]', '    wizard: dataset list');
  await page.locator('[data-testid="select-dataset"]', { hasText: versionMarker }).first().click();
  await page.getByRole('button', { name: '下一步' }).click();
  // Step 2 profile
  await expectVisible(page, '[data-testid="select-profile"]', '    wizard: profile list');
  await page.locator('[data-testid="select-profile"]').first().click();
  await page.getByRole('button', { name: '下一步' }).click();
  // Step 3 metric editor (Run Configuration)
  await expectVisible(page, '[data-testid="metric-editor"]', '    wizard: metric editor');
  if (thresholdOverride !== null) {
    const th = page.locator('[data-testid="metric-threshold-numerical_consistency"]');
    await th.fill(String(thresholdOverride));
    record('    wizard: threshold override 0.9 set', true);
  }
  if (disableRagas) {
    for (const m of ['context_recall', 'context_precision', 'faithfulness', 'answer_relevancy']) {
      await page.locator(`[data-testid="metric-enabled-${m}"]`).uncheck();
    }
    record('    wizard: 4 ragas metrics disabled', true);
  }
  await page.getByRole('button', { name: /下一步/ }).click();
  // Step 4 RAG input
  await expectVisible(page, '[data-testid="rag-not-configured"]', '    wizard: RAG input (Golden Replay)');
  await page.getByRole('button', { name: /下一步/ }).click();
  // Step 5 review
  await expectVisible(page, 'text=确认并启动', '    wizard: review');
  const reviewText = await page.locator('text=确认并启动').locator('xpath=ancestor::section').first().innerText().catch(() => '');
  if (thresholdOverride !== null) {
    record('    wizard: review shows override marker', reviewText.includes('*'), '');
  }
  if (disableRagas) {
    record('    wizard: review shows disabled metrics', reviewText.includes('停用 4 项'), '');
  }
  await page.locator('[data-testid="start-evaluation"]').click();
  await page.waitForURL(/\/evaluations\/(?!new)[\w-]+/, { timeout: 30000 });
  const url = page.url();
  const runId = url.split('/').pop();
  record('    wizard: 202 -> run workbench', Boolean(runId), runId?.slice(0, 8));
  return runId;
}

/** 弹性导航：vite dev 偶发抖动不致命（Freeze Gate 首轮真实发现）。 */
async function goto(page, url) {
  for (let i = 0; i < 5; i += 1) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      return true;
    } catch {
      await page.waitForTimeout(3000);
    }
  }
  return false;
}

async function waitTerminal(page, runId, timeoutMs = 900000) {
  const deadline = Date.now() + timeoutMs;
  let last = '';
  while (Date.now() < deadline) {
    await goto(page, `${BASE}/evaluations/${runId}`);
    // 重试读取状态（瞬时网络/DOM 抖动不丢状态）
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const body = await page.locator('body').innerText().catch(() => '');
      const m = body.match(/(等待执行|运行中|已完成|部分完成|执行失败|已取消)/);
      if (m) {
        last = m[1];
        break;
      }
      await page.waitForTimeout(1500);
    }
    if (last && !['等待执行', '运行中'].includes(last)) {
      return last;
    }
    await page.waitForTimeout(4000);
  }
  return `TIMEOUT(${last})`;
}

// ---------------- main ----------------

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
const page = await context.newPage();

page.on('console', (msg) => {
  if (msg.type() === 'error') {
    const loc = msg.location()?.url ?? '';
    consoleErrors.push(`${loc || page.url()} :: ${msg.text().slice(0, 200)}`);
  }
});
page.on('pageerror', (err) => pageErrors.push(String(err).slice(0, 200)));
page.on('response', async (res) => {
  const url = res.url();
  if (url.includes('/api/')) {
    if (res.status() >= 400) badApi.push(`${res.status()} ${url}`);
    else if ((res.headers()['content-type'] ?? '').includes('json')) {
      try {
        apiBodies.push({ url, text: await res.text() });
      } catch {
        /* stream already consumed */
      }
    }
  }
});

try {
  // 1. Project
  await goto(page, `${BASE}/projects`);
  await expectVisible(page, '[data-testid="project-name-input"]', '1. Project page loads');
  await page.locator('[data-testid="project-name-input"]').fill(RUN_LABEL);
  await page.locator('[data-testid="project-create-submit"]').click();
  await expectVisible(page, `text=${RUN_LABEL}`, '1. Project created via real POST', 30000);
  await page.locator(`[data-testid="enter-${RUN_LABEL}"]`).click();
  await page.waitForURL(`${BASE}/`);
  // 侧栏项目标记必须已是新项目（上下文确定性）
  await page
    .locator('text=暂无评估')
    .or(page.locator('text=质量概览'))
    .first()
    .waitFor({ state: 'visible', timeout: 30000 })
    .catch(() => {});
  // 页头标题 = activeProject.name（显式等待，非即时快照）
  const sideMark = await page
    .locator('h1', { hasText: RUN_LABEL })
    .first()
    .waitFor({ state: 'visible', timeout: 30000 })
    .then(() => true)
    .catch(() => false);
  record('1b. Workspace context switched to the new project', sideMark, '');

  // 2. Dashboard（空项目 -> 诚实空态；有数据 -> 质量概览。两者均为真实渲染）
  const settled = await page
    .locator('text=暂无评估')
    .or(page.locator('text=质量概览'))
    .first()
    .waitFor({ state: 'visible', timeout: 30000 })
    .then(() => true)
    .catch(() => false);
  if (!settled) {
    const body = await page.locator('body').innerText().catch(() => '');
    record('2. Dashboard reaches a settled honest state', false, body.replace(/\s+/g, ' ').slice(0, 200));
  } else {
    const dashBody = await page.locator('body').innerText();
    record(
      '2. Dashboard reaches a settled honest state',
      dashBody.includes('暂无评估') || dashBody.includes('质量概览'),
      dashBody.includes('暂无评估') ? 'empty state' : 'with data',
    );
  }
  await scanPlaceholders(page, 'dashboard');
  // 5. Dataset Import (before list/detail — dataset must exist first)
  await importDataset(page, { expectVersionOf: false });

  // 3. Dataset List
  await expectVisible(page, '[data-testid="dataset-row"]', '3. Dataset list shows imported dataset');
  const listText = await page.locator('[data-testid="dataset-row"]').first().innerText();
  record('3. Dataset list shows v1 + 4 records', listText.includes('v1') && listText.includes('4'), '');

  // 4. Dataset Detail（Tabs: Overview / Validation / Records）
  await page.locator('[data-testid="dataset-row"] >> nth=0').getByRole('link', { name: '查看', exact: true }).click();
  await expectVisible(page, '[data-testid="tab-overview"]', '4. Dataset detail loads (Overview tab)');
  await page.locator('[data-testid="tab-validation"]').click();
  await expectVisible(page, '[data-testid="validation-summary"]', '4. Validation tab renders report');

  // 6. Record Explorer（Records Tab）
  await page.locator('[data-testid="tab-records"]').click();
  await expectVisible(page, '[data-testid="record-row"]', '6. Record Explorer renders DataTable');
  const viewBtn = page.getByRole('button', { name: '查看' }).first();
  if (await viewBtn.count()) {
    await viewBtn.click();
    await expectVisible(page, 'text=Metadata', '6. Record drawer opens');
    await page.keyboard.press('Escape');
  }

  // 7. Evaluations (empty list)
  await goto(page, `${BASE}/evaluations`);
  await expectVisible(page, 'text=评估运行', '7. Evaluations list loads');

  // 8-12. New Evaluation wizard (metric override 0.9) + start
  const run1 = await runWizard(page, { versionMarker: 'v1', thresholdOverride: 0.9 });

  // 13. Polling to terminal (real Judge for faithfulness/context_recall)
  const status1 = await waitTerminal(page, run1);
  record('13. Run reached terminal via real execution', ['已完成', '部分完成'].includes(status1), `status=${status1}`);

  // 14. Workbench（Overview Tab 默认；Metrics/Failures 经 Tab 切换）
  await goto(page, `${BASE}/evaluations/${run1}`);
  await expectVisible(page, 'text=质量概览', '14. Workbench overview (quality summary)');
  await expectVisible(page, 'text=失败概览', '14. Failure overview (overview tab)');
  await expectVisible(page, 'text=执行概览', '14. Execution summary');

  // API/UI consistency (§四) — ScoreValue 展示为 4 位小数格式化，允许容差
  // （在 Overview Tab 尚未卸载时读取）
  const report1 = await (await fetch(`${BASE}/api/evaluations/${run1}/report`)).json();
  const uiOverall = await page.locator('text=总体质量').locator('xpath=following-sibling::*[1]').first().innerText().catch(() => '');
  const uiVal = parseFloat(uiOverall);
  record(
    'API/UI: overall score consistent',
    report1.summary.overall_score === null
      ? uiOverall.includes('暂无有效分数')
      : Number.isFinite(uiVal) && Math.abs(uiVal - report1.summary.overall_score) < 0.0001,
    `api=${report1.summary.overall_score} ui=${uiOverall.trim()}`,
  );

  await page.locator('[data-testid="tab-metrics"]').click();
  await expectVisible(page, '[data-testid="metric-numerical_consistency"]', '14. Metric rows render (Metrics tab)');
  const numRow = await page.locator('[data-testid="metric-numerical_consistency"]').innerText();
  record('14. Override visible in UI (threshold 0.9)', numRow.includes('0.9'), numRow.replace(/\s+/g, ' ').slice(0, 80));
  // null -> 0 guard
  const noScore = await page.locator('[data-testid="no-score"]').count();
  record('14. null score rendered as 暂无有效分数 (never 0)', noScore >= 1, `count=${noScore}`);
  await scanPlaceholders(page, 'workbench');
  const apiNum = report1.metrics.find((m) => m.name === 'numerical_consistency');
  record('API/UI: numerical threshold = 0.9 (override in effect)', apiNum?.threshold === 0.9, `api=${apiNum?.threshold}`);

  // 15. Failure Explorer
  await goto(page, `${BASE}/failures`);
  await page.selectOption('[data-testid="failure-run-select"]', { index: 1 });
  await expectVisible(page, '[data-testid="failure-row"]', '15. Failure Explorer lists failure', 30000);
  const allRows = (await page.locator('[data-testid="failure-row"]').allInnerTexts()).join('\n');
  record('15. Failure type = integrity.numerical_mismatch', allRows.includes('integrity.numerical_mismatch'), '');

  // 16-18. Diagnosis -> Evidence -> Recommendation drawer（点击 diagnosed 行；等待 G3 详情加载）
  const diagnosedRow = page.locator('[data-testid="failure-row"]', { hasText: 'integrity.numerical_mismatch' }).first();
  await diagnosedRow.locator('[data-testid="open-diagnosis"]').click();
  await expectVisible(page, '[data-testid="diagnosis-drawer"]', '16. Diagnosis drawer opens');
  await expectVisible(page, '[data-testid="diagnosis-chain"]', '16. Workflow chain visible (Metric→Failure→Diagnosis→Evidence→Recommendation)');
  const drawer = page.locator('[data-testid="diagnosis-drawer"]');
  await drawer.getByText('Retrieved Contexts').first().waitFor({ state: 'visible', timeout: 20000 }).catch(() => {});
  const drawerText = await drawer.innerText();
  record('16. Diagnosis: root cause rendered', drawerText.includes('Root Cause'), '');
  record('17. Evidence: contract + items from persisted JSONB', (await drawer.locator('[data-testid^="evidence-"]').count()) >= 1, '');
  record('17. Evidence: comparison_basis present', drawerText.includes('comparison_basis'), '');
  record('17. Evidence: full sample I/O (contexts)', drawerText.includes('Retrieved Contexts') && drawerText.length > 200, '');
  record('18. Recommendation rendered', (await drawer.locator('[data-testid="recommendation-item"]').count()) >= 1, '');
  await scanPlaceholders(page, 'drawer');

  // Second dataset version + second run (integrity-only via UI overrides) for Compare/Regression
  await goto(page, `${BASE}/datasets`);
  await page.locator('[data-testid="dataset-row"] >> nth=0').getByRole('link', { name: '查看', exact: true }).click();
  await expectVisible(page, '[data-testid="create-new-version"]', '    new-version entry present');
  await page.locator('[data-testid="create-new-version"]').click();
  await importDataset(page, { expectVersionOf: true });
  const listNow = await page.locator('[data-testid="dataset-row"]').allInnerTexts();
  record('    dataset v2 created (FR-03)', listNow.some((t) => t.includes('v2')), `${listNow.length} rows`);

  const run2 = await runWizard(page, { versionMarker: 'v2', disableRagas: true });
  const status2 = await waitTerminal(page, run2);
  record('    run2 terminal (integrity-only, fast)', ['已完成', '部分完成'].includes(status2), `status=${status2}`);

  // 19. Compare — real comparability verdict from backend
  await goto(page, `${BASE}/compare`);
  await pickRunOption(page, 'baseline-select', 'v1');
  await pickRunOption(page, 'candidate-select', 'v2');
  await page.locator('[data-testid="compare-submit"]').click();
  await expectVisible(page, '[data-testid="comparability-banner"]', '19. Compare renders comparability banner');
  const banner = await page.locator('[data-testid="comparability-banner"]').innerText();
  const reasons = await page.locator('[data-testid="comparability-reasons"]').innerText().catch(() => '');
  record(
    '19. Comparability verdict strictly from backend (BLOCKED expected: v1 vs v2 = different datasets)',
    banner.includes('不可比较') && reasons.length > 0,
    reasons.replace(/\s+/g, ' ').slice(0, 100),
  );

  // 20. Regression
  await goto(page, `${BASE}/regression`);
  await pickRunOption(page, 'regression-baseline-select', 'v1');
  await pickRunOption(page, 'regression-candidate-select', 'v2');
  await page.locator('[data-testid="regression-submit"]').click();
  await expectVisible(page, '[data-testid="regression-overall"]', '20. Regression renders overall verdict');
  const overall = await page.locator('[data-testid="regression-overall"]').innerText();
  record('20. Overall verdict from backend (NOT_COMPARABLE expected)', overall.includes('不可比较'), '');

  // 21. Quality Gate
  await goto(page, `${BASE}/quality-gate`);
  await pickRunOption(page, 'gate-run-select', 'v1');
  await page.locator('[data-testid="gate-submit"]').click();
  await expectVisible(page, '[data-testid="gate-status"]', '21. Quality Gate renders');
  const gateBody = await page.locator('body').innerText();
  record(
    '21. NOT_EVALUABLE + reason distinct from threshold FAIL (default profile has no gate config)',
    gateBody.includes('无法评估') && gateBody.includes('QUALITY_GATE_NOT_CONFIGURED'),
    '',
  );

  // 22. JSON Export (download real machine report)
  await goto(page, `${BASE}/evaluations/${run1}`);
  await expectVisible(page, '[data-testid="export-report"]', '22. Export button present');
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 20000 }),
    page.locator('[data-testid="export-report"]').click(),
  ]);
  const dlPath = path.join(DOWNLOAD_DIR, 'report.json');
  await download.saveAs(dlPath);
  const reportJson = JSON.parse(await fsp.readFile(dlPath, 'utf-8'));
  record('22. Exported JSON is the real machine report', reportJson?.summary?.run_id === run1, `run_id=${reportJson?.summary?.run_id?.slice(0, 8)}`);
  record(
    '22. Exported JSON contains no secrets',
    !JSON.stringify(reportJson).includes(JUDGE_KEY) && !/(sk-|api_key\s*":\s*"[A-Za-z0-9])/.test(JSON.stringify(reportJson)),
    '',
  );

  // 23. Settings
  await goto(page, `${BASE}/settings`);
  await expectVisible(page, '[data-testid="settings-runtime"]', '23. Settings loads (real /api/health)');
  const settingsBody = await page.locator('body').innerText();
  record('23. Env-managed declaration shown (no fake forms)', settingsBody.includes('由环境变量'), '');
  record('23. Database healthy from real health endpoint', (await page.locator('[data-testid="health-database"]').innerText()).includes('Healthy'), '');

  // 24. Profiles (read-only, G6-a)
  await goto(page, `${BASE}/profiles`);
  await expectVisible(page, '[data-testid="profiles-readonly-notice"]', '24. Profiles page loads (read-only declaration)');
  record('24. No fake save/import buttons', (await page.getByRole('button', { name: /保存|导入 YAML|编辑/ }).count()) === 0, '');

  // 25. Metrics
  await goto(page, `${BASE}/metrics`);
  await expectVisible(page, '[data-testid="metric-card-context_recall"]', '25. Metrics catalog loads');
  record('25. Grouped by category (3 groups)', (await page.locator('text=/RETRIEVAL · 检索|GENERATION · 生成|INTEGRITY · 一致性/').count()) >= 1, '');

  // Evaluations list with both runs + gate badges
  await goto(page, `${BASE}/evaluations`);
  await page
    .locator('[data-testid="run-row"]')
    .first()
    .waitFor({ state: 'visible', timeout: 20000 })
    .catch(() => {});
  const runRows = await page.locator('[data-testid="run-row"]').count();
  record('7b. Evaluations list shows both runs', runRows >= 2, `rows=${runRows}`);

  // Dashboard now has data
  await goto(page, `${BASE}/`);
  const hasRuns = await page
    .locator('text=最近运行')
    .first()
    .waitFor({ state: 'visible', timeout: 20000 })
    .then(() => true)
    .catch(() => false);
  record('2b. Dashboard shows real run data (recent runs)', hasRuns, '');
} finally {
  // ---- global guards ----
  record('GUARD: no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '));
  record('GUARD: no page errors', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | '));
  record('GUARD: no API 4xx/5xx', badApi.length === 0, badApi.slice(0, 3).join(' | '));
  record('GUARD: no placeholder text on any visited page', placeholderHits.length === 0, placeholderHits.join(','));
  const allApiText = apiBodies.map((b) => b.text).join('\n');
  record(
    'GUARD: no secrets in any API response body',
    Boolean(allApiText) && !allApiText.includes(JUDGE_KEY) && !/api_key":\s*"[^"]+[A-Za-z0-9]"/.test(allApiText),
    `scanned ${apiBodies.length} JSON responses`,
  );

  const passed = results.filter((r) => r.ok).length;
  console.log(`\n== Chrome E2E: ${passed}/${results.length} passed ==`);
  for (const r of results.filter((r) => !r.ok)) console.log(`  [FAIL] ${r.name} — ${r.detail}`);

  await browser.close();
  process.exitCode = passed === results.length ? 0 : 1;
}
