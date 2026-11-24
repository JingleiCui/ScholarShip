/** Read-only review of generated source/translation alignment in isolated acceptance. */
import { chromium, expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs';
import path from 'node:path';

// USER CONFIGURATION. Never persist browser cookies or print credentials.
const root = path.resolve(import.meta.dirname, '..');
const base = 'http://127.0.0.1:15173';
const api = 'http://127.0.0.1:18080/api/v1';
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const report = JSON.parse(fs.readFileSync(path.join(root, 'var/reports/document-pipeline-real.json'), 'utf8'));
if (!process.env.LOCAL_ACCEPTANCE_PASSWORD) throw new Error('acceptance_password_required');
const browser = await chromium.launch({ headless: true, ...(fs.existsSync(chrome) ? { executablePath: chrome } : {}) });
const result = { passed: false, checks: [] };
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const login = await context.request.post(api + '/auth/login', {
    headers: { Origin: base }, data: { username: 'researcher', password: process.env.LOCAL_ACCEPTANCE_PASSWORD },
  });
  if (!login.ok()) throw new Error('isolated_ui_login_failed');
  const page = await context.newPage();
  await page.goto(base + '/#page=read');
  await page.getByLabel('研究空间', { exact: true }).selectOption(report.project_id);
  await page.getByLabel('当前阅读文献', { exact: true }).selectOption(report.documents[1].version_id);
  await page.locator('.document-preparation-disclosure > summary').click();
  const workbench = page.getByRole('region', { name: '本地解析与中英对应' });
  await expect(workbench).toBeVisible();
  await expect(workbench.getByText('The sample uses eight GPU devices.', { exact: false }).first()).toBeVisible();
  await expect(workbench.getByText('尚未翻译', { exact: true })).toHaveCount(0);
  await expect(workbench.locator('.alignment-pair')).not.toHaveCount(0);
  await page.screenshot({ path: path.join(root, 'var/reports/document-alignment-desktop.png'), fullPage: true });
  result.checks.push('real_ocr_source_and_translation_visible');
  await workbench.getByRole('button', { name: '对照 PDF 与结构', exact: true }).click();
  await expect(page.getByRole('region', { name: '原文证据阅读器' })).toBeVisible();
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 30000 });
  await page.waitForFunction(() => {
    const c = document.querySelector('canvas');
    if (!c?.width || !c?.height) return false;
    const pixels = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let ink = 0;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3] && pixels[i] + pixels[i + 1] + pixels[i + 2] < 600) ink++;
    return ink > 50;
  }, { timeout: 30000 });
  result.checks.push('original_pdf_navigation');
  await page.setViewportSize({ width: 390, height: 844 });
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw new Error('mobile_overflow');
  await page.screenshot({ path: path.join(root, 'var/reports/document-alignment-mobile.png'), fullPage: true });
  result.checks.push('mobile_source_alignment_no_overflow');
  result.passed = true;
} finally {
  fs.writeFileSync(path.join(root, 'var/reports/document-ui.json'), JSON.stringify(result, null, 2) + '\n');
  await browser.close();
}
