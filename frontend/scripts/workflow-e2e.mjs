/** Real browser + isolated test database workflow, without paid Judge calls.
 * Start: python scripts/serve_review.py; Vite must also be running on :5173.
 * Run from frontend: node scripts/workflow-e2e.mjs
 * Creates uniquely named fixtures in TEST_DATABASE_URL only; never resets data.
 * Do not run while pytest PostgreSQL tests are active.
 */
import { chromium } from 'playwright-core';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import { execFileSync } from 'node:child_process';

const base = 'http://127.0.0.1:5173', backend = 'http://127.0.0.1:8001';
const out = path.resolve('../exports/review-2026-10-11');
await fs.mkdir(out, { recursive: true });
const checks = [];
function check(name, ok, detail = {}) {
  checks.push({ name, ok: Boolean(ok), detail });
  console.log(JSON.stringify(checks.at(-1)));
  if (!ok) throw new Error(name);
}
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
try {
  const health = await page.request.get(backend + '/api/health');
  check('Backend is isolated from development data', health.headers()['x-rageval-review'] === 'isolated-test-database');
  async function request(method, route, data) {
    const r = await page.request[method](backend + route, { data });
    if (!r.ok()) throw new Error(`${method} ${route}: HTTP ${r.status()} ${(await r.text()).slice(0, 400)}`);
    return r.json();
  }
  await page.route(/^http:\/\/127\.0\.0\.1:5173\/api\//, async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: backend + url.pathname + url.search });
    await route.fulfill({ response });
  });
  const project = await request('post', '/api/projects', { name: 'review-ui-' + Date.now(), domain: 'general' });
  await page.addInitScript(id => localStorage.setItem('rageval.activeProjectId', id), project.id);
  async function goto(route) {
    await page.goto(base + route);
    await page.waitForLoadState('networkidle');
  }
  async function upload(envelope) {
    await goto('/datasets/import');
    await page.setInputFiles('input[type=file]', { name: 'review.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(envelope)) });
    await page.getByTestId('do-import').waitFor();
    const response = page.waitForResponse(r => r.url().includes('datasets:import') && r.request().method() === 'POST');
    await page.getByTestId('do-import').click();
    return response;
  }
  const invalidResponse = await upload({ name: 'review-invalid', records: [{ question: '' }] });
  await page.getByText(/无效数据集不会被当作成功导入/).waitFor();
  check('Import step 3 displays real validation failure', invalidResponse.status() === 400 && (await invalidResponse.json()).code === 'BIZ_VALIDATION_FAILED');
  const envelope = { name: 'review-golden', domain: 'general', records: [
    { question: '营业收入是多少？', reference_answer: '100元', reference_contexts: ['营业收入100元'], metadata: { answer: '200元', contexts: ['营业收入100元'] } },
    { question: '成本是多少？', reference_answer: '50元', reference_contexts: ['成本50元'], metadata: { answer: '50元', contexts: ['成本50元'] } },
  ] };
  const validResponse = await upload(envelope);
  await page.getByTestId('import-success').waitFor();
  const imported = await validResponse.json();
  const dsid = imported.dataset_id ?? imported.id;
  const dataset = await request('get', `/api/datasets/${dsid}`);
  check('Import step 4 really persists the dataset', validResponse.status() === 201 && dataset.record_count === 2 && dataset.validation_status === 'valid', { datasetId: dsid, count: dataset.record_count });

  function yaml(threshold) { return `profile:\n  name: review_integrity\nmetrics:\n  numerical_consistency: {enabled: true, threshold: ${threshold}, weight: 1.0}\n  temporal_consistency: {enabled: false}\n  entity_consistency: {enabled: false}\npipeline:\n  engines: [integrity]\n  diagnosis: {enabled: true}\nrag_input:\n  mode: golden_replay\nquality_gate: null\n`; }
  await goto('/profiles');
  await page.getByRole('tab', { name: '导入 YAML' }).click();
  await page.getByTestId('yaml-textarea').fill(yaml(0.9));
  await page.getByTestId('yaml-preview-btn').click();
  await page.getByTestId('diff-table').waitFor();
  check('Configuration diff preview is populated by the backend', await page.locator('[data-testid="diff-table"] tbody tr').count() > 0);
  const createdConfig = page.waitForResponse(r => new URL(r.url()).pathname === '/api/configs/import' && r.request().method() === 'POST');
  await page.getByTestId('create-version-btn').click();
  const cfg1 = await (await createdConfig).json();
  await page.getByTestId('config-table').waitFor();
  const cfg2 = await request('post', '/api/configs/import', { project_id: project.id, domain: 'general', yaml: yaml(0.8) });
  check('Config changes create separate immutable versions', cfg1.config_id !== cfg2.config_id && cfg1.config_version !== cfg2.config_version);

  async function launch(configId) {
    await goto('/evaluations/new');
    await page.getByTestId('select-dataset').filter({ hasText: 'review-golden' }).click();
    await page.getByRole('button', { name: '下一步', exact: true }).click();
    await page.getByTestId('select-stored-' + configId).click();
    await page.getByRole('button', { name: '下一步', exact: true }).click();
    await page.getByTestId('metric-editor').waitFor();
    await page.getByRole('button', { name: /^下一步/ }).click();
    await page.getByRole('button', { name: /^下一步/ }).click();
    const start = page.getByTestId('start-evaluation');
    if (!(await start.isEnabled())) throw new Error('Reusable dataset was blocked');
    await start.click();
    await page.waitForURL(/\/evaluations\/(?!new)[\w-]+/);
    const id = new URL(page.url()).pathname.split('/').pop();
    let run;
    for (let attempt = 0; attempt < 40; attempt++) {
      run = await request('get', '/api/evaluations/' + id);
      if (!['pending', 'running'].includes(run.status)) break;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    check('Wizard launch reaches a real terminal run', ['completed', 'completed_with_errors'].includes(run.status), { id, status: run.status });
    return id;
  }
  const first = await launch(cfg1.config_id), second = await launch(cfg1.config_id), third = await launch(cfg2.config_id);
  const after = await request('get', `/api/datasets/${dsid}`);
  check('Three runs reuse the same locked dataset without changing content', first !== second && after.is_locked && after.record_count === 2);
  const otherProject = await request('post', '/api/projects', { name: 'review-ui-other-' + Date.now(), domain: 'general' });
  const cross = await page.request.post(backend + '/api/evaluations', { data: { project_id: otherProject.id, dataset_id: dsid, config_id: cfg1.config_id } });
  check('Cross-project creation is rejected by the real API', cross.status() === 409 && (await cross.json()).code === 'BIZ_PROJECT_MISMATCH');

  async function compare(candidate, status) {
    await goto('/compare');
    await page.getByTestId('baseline-select').selectOption(first);
    await page.getByTestId('candidate-select').selectOption(candidate);
    await page.getByTestId('compare-submit').click();
    await page.getByTestId('comparability-banner').waitFor();
    const data = await request('get', `/api/comparisons?baseline_run_id=${first}&candidate_run_id=${candidate}`);
    check('Browser comparison reaches ' + status, data.comparability.status === status && await page.getByTestId('comparison-table').count() === 1, { rows: data.metrics.length, reasonCount: data.comparability.reasons.length });
    if (status === 'LIMITED') {
      const reasons = await page.getByTestId('comparability-reasons').innerText();
      check('LIMITED reason is explained in Chinese', /配置指纹不同/.test(reasons));
    }
  }
  await compare(second, 'DIRECT');
  await compare(third, 'LIMITED');
  await page.screenshot({ path: path.join(out, 'comparison.png'), fullPage: true, animations: 'disabled' });
  await goto('/regression');
  await page.getByTestId('regression-baseline-select').selectOption(first);
  await page.getByTestId('regression-candidate-select').selectOption(second);
  await page.getByTestId('regression-submit').click();
  await page.getByTestId('regression-table').waitFor();
  check('Regression renders real comparison results', true);

  await goto('/evaluations/' + first);
  await page.getByRole('tab', { name: /^失败样本/ }).click();
  await page.getByTestId('failure-sample').first().click();
  await page.getByTestId('diagnosis-drawer').waitFor();
  const drawer = await page.getByTestId('diagnosis-drawer').innerText();
  check('Record drawer loads full persisted input/output and metric data', drawer.includes('营业收入是多少') && drawer.includes('200元') && drawer.includes('100元') && drawer.includes('numerical_consistency'));
  await page.screenshot({ path: path.join(out, 'record-drawer.png'), fullPage: true, animations: 'disabled' });
  await goto('/evaluations/' + first);
  for (const format of ['json', 'pdf']) {
    const pending = page.waitForEvent('download');
    await page.getByTestId('export-report-' + format).click();
    const file = await pending;
    const saved = path.join(out, 'report.' + format);
    await file.saveAs(saved);
    const bytes = await fs.readFile(saved);
    check('Real ' + format.toUpperCase() + ' export downloads', format === 'pdf' ? bytes.subarray(0, 5).toString() === '%PDF-' : JSON.parse(bytes.toString()).run.run_id === first, { bytes: bytes.length });
  }
  // This rare contract path uses a marked, persisted test fixture. The three
  // runs and all other checks above use the real import/evaluation pipeline.
  const python = path.resolve('../.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  execFileSync(python, [path.resolve('../scripts/seed_review_missing_evidence.py'), first], { cwd: path.resolve('..') });
  await goto('/evaluations/' + first);
  await page.getByRole('tab', { name: /^诊断/ }).click();
  const missing = await page.getByTestId('undetermined-item').allInnerTexts();
  check('Persisted missing-evidence fixture renders Chinese contract paths', missing.some(t => t.includes('参考证据的可比较性') && t.includes('可核验的答案声明') && t.includes('比较依据的差异记录')));
  await page.screenshot({ path: path.join(out, 'missing-evidence.png'), fullPage: true, animations: 'disabled' });
  check('No browser exceptions', errors.length === 0, { errors });
} catch (e) {
  console.error(String(e), errors);
  await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true, animations: 'disabled' }).catch(() => {});
  process.exitCode = 1;
} finally {
  await fs.writeFile(path.join(out, 'workflow-results.json'), JSON.stringify(checks, null, 2));
  await browser.close();
}
