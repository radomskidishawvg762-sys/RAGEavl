/** Real browser and API against a local model-error fixture. No paid calls.
 * Requires Vite :5173 and scripts/serve_review.py --with-judge on :8001.
 * Draft probes never save or change the active Judge configuration.
 */
import { chromium } from 'playwright-core';
import { createServer } from 'node:http';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';

const backend = 'http://127.0.0.1:8001', base = 'http://127.0.0.1:5173';
const out = path.resolve('../exports/judge-review-2026-10-11');
await fs.mkdir(out, { recursive: true });
let mode = 'permission', calls = 0;
const fixture = createServer(async (req, res) => {
  for await (const _ of req) { /* Consume request without persisting keys or headers. */ }
  calls++;
  const status = mode === 'permission' ? 403 : mode === 'quota' ? 429 : 200;
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(status === 200 ? { choices: [] } : { error: {
    message: mode === 'quota' ? 'insufficient_quota weekly usage limit' : 'denied test-probe-secret https://private.invalid',
  } }));
});
await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const checks = [], errors = [];
function check(name, ok) {
  checks.push({ name, ok: Boolean(ok) });
  console.log(JSON.stringify(checks.at(-1)));
  if (!ok) throw new Error(name);
}
page.on('pageerror', e => errors.push(e.message));
try {
  const health = await page.request.get(backend + '/api/health');
  check('Isolated review backend', health.headers()['x-rageval-review'] === 'isolated-test-database');
  const before = await (await page.request.get(backend + '/api/settings/judge')).json();
  let saves = 0;
  await page.route(/^http:\/\/127\.0\.0\.1:5173\/api\//, async route => {
    const url = new URL(route.request().url());
    if (route.request().method() === 'PUT') saves++;
    await route.fulfill({ response: await route.fetch({ url: backend + url.pathname + url.search }) });
  });
  await page.goto(base + '/settings');
  await page.getByTestId('judge-settings-form').waitFor();
  await page.getByLabel('服务商 Provider', { exact: true }).fill('openai');
  await page.getByLabel('模型 Model', { exact: true }).fill('fixture-model');
  await page.getByLabel('接入地址 Base URL', { exact: true }).fill(`http://127.0.0.1:${fixture.address().port}/v1`);
  await page.getByLabel('临时 API Key', { exact: true }).fill('test-probe-secret');
  for (const [nextMode, code, message] of [
    ['permission', 'EXT_JUDGE_PERMISSION_DENIED', /无权调用该模型/],
    ['quota', 'EXT_JUDGE_QUOTA_EXHAUSTED', /额度已用尽/],
    ['format', 'EXT_JUDGE_RESPONSE_INVALID', /返回格式不正确/],
  ]) {
    mode = nextMode;
    const count = calls;
    const result = page.waitForResponse(r => r.url().endsWith('/settings/judge/test'));
    await page.getByRole('button', { name: '测试连接', exact: true }).click();
    const body = await (await result).json();
    console.log(JSON.stringify({ mode, calls: calls - count, code: body.error_code, message: body.message }));
    await page.getByRole('status').filter({ hasText: message }).waitFor();
    check(nextMode + ': one real HTTP request and correct failure message', calls === count + 1 && body.error_code === code);
    const visible = await page.locator('body').innerText();
    check(nextMode + ': upstream secrets and URLs are absent', !visible.includes('test-probe-secret') && !visible.includes('private.invalid'));
  }
  await page.screenshot({ path: path.join(out, 'judge-format-error.png'), fullPage: true });
  await page.getByLabel('模型 Model', { exact: true }).fill('edited-after-test');
  check('Editing draft clears the previous result', await page.getByRole('status').count() === 0);
  const after = await (await page.request.get(backend + '/api/settings/judge')).json();
  check('Probes never save or alter the active Judge', saves === 0 && JSON.stringify(before) === JSON.stringify(after));
  check('No browser errors', errors.length === 0);
} finally {
  await fs.writeFile(path.join(out, 'browser-results.json'), JSON.stringify({ checks, errors }, null, 2));
  await browser.close();
  await new Promise(resolve => fixture.close(resolve));
}
