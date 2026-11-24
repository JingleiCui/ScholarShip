/** Read-only UI acceptance against the isolated local application. */
import { chromium, expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs';
import path from 'node:path';

// USER CONFIGURATION. Credentials come only from the calling private environment.
const root = path.resolve(import.meta.dirname, '..');
const base = 'http://127.0.0.1:15173';
const api = 'http://127.0.0.1:18080/api/v1';
const workspace = '本地模型真实链路验收';
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const report = path.join(root, 'var/reports/local-ui.json');
const password = process.env.LOCAL_ACCEPTANCE_PASSWORD;
if (!password) throw new Error('local_acceptance_credential_required');
const browser = await chromium.launch({ headless: true, ...(fs.existsSync(chrome) ? { executablePath: chrome } : {}) });
const result = { passed: false, checks: [], cloud_model_requests: 0 };
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const login = await context.request.post(api + '/auth/login', {
    headers: { Origin: base }, data: { username: 'researcher', password },
  });
  if (!login.ok()) throw new Error('local_ui_login_failed');
  const page = await context.newPage();
  await page.goto(base + '/#page=read&tab=qa');
  await page.getByLabel('研究空间', { exact: true }).selectOption({ label: workspace });
  await page.getByLabel('当前阅读文献', { exact: true }).selectOption({ index: 1 });
  await expect(page.locator('select:has(option[value="local_inference"])')).toHaveValue('local_inference');
  await page.locator('details.execution-options > summary').click();
  await expect(page.getByRole('region', { name: '论文 AI 对话' })).toBeVisible({ timeout: 30000 });
  await page.screenshot({ path: path.join(root, 'var/reports/local-ui-read.png'), fullPage: true });
  result.checks.push('qa_local_mode_default');
  await page.goto(base + '/#page=write');
  await expect(page.getByRole('heading', { name: '学术写作', exact: true })).toBeVisible();
  await expect(page.locator('select:has(option[value="local_inference"])')).toHaveValue('local_inference');
  await expect(page.getByText('正在加载研究工具…', { exact: true })).toHaveCount(0);
  result.checks.push('writing_local_mode_default');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(root, 'var/reports/local-ui-write-mobile.png'), fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  if (overflow) throw new Error('mobile_horizontal_overflow');
  result.checks.push('mobile_no_horizontal_overflow');
  result.passed = true;
} finally {
  fs.writeFileSync(report, JSON.stringify(result, null, 2) + '\n');
  await browser.close();
}
