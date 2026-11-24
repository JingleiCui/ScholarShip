/** Actual local conversation, follow-up, reload and citation UI acceptance. */
import { chromium, expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs';
import path from 'node:path';

// USER CONFIGURATION: own isolated fixture workspace; no cloud requests or saved cookies.
const root = path.resolve(import.meta.dirname, '..');
const base = 'http://127.0.0.1:15173';
const api = 'http://127.0.0.1:18080/api/v1';
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'var/reports/document-pipeline-real.json'), 'utf8'));
const output = { passed: false, checks: [], execution_mode: 'local_inference' };
if (!process.env.LOCAL_ACCEPTANCE_PASSWORD) throw new Error('isolated_acceptance_password_required');
const browser = await chromium.launch({ headless: true, ...(fs.existsSync(chrome) ? { executablePath: chrome } : {}) });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const login = await context.request.post(api + '/auth/login', {
    headers: { Origin: base }, data: { username: 'researcher', password: process.env.LOCAL_ACCEPTANCE_PASSWORD },
  });
  if (!login.ok()) throw new Error('isolated_chat_login_failed');
  const page = await context.newPage();
  await page.goto(base + '/#page=read');
  await page.getByLabel('研究空间', { exact: true }).selectOption(fixture.project_id);
  await page.getByLabel('当前阅读文献', { exact: true }).selectOption(fixture.documents[0].version_id);
  await page.getByRole('button', { name: '与 AI 对话', exact: true }).click();
  const chat = page.getByRole('region', { name: '论文 AI 对话', exact: true });
  await expect(chat).toBeVisible();
  const question = chat.getByRole('textbox', { name: '研究问题', exact: true });
  await expect(question).toBeVisible();
  await expect(chat.getByRole('button', { name: '发送问题', exact: true })).toBeDisabled();
  const progress = chat.getByRole('region', { name: '论文自动处理进度', exact: true });
  await expect(progress).toContainText('原文索引已校验');
  // Import a fresh self-authored fixture, with no explicit parse/index request.
  const original = await context.request.get(api + '/paper-versions/' + fixture.documents[0].version_id + '/file');
  const csrf = (await context.cookies()).find(c => c.name === 'ss_csrf')?.value;
  const imported = await context.request.post(api + '/projects/' + fixture.project_id + '/uploads', {
    headers: { Origin: base, 'X-CSRF-Token': csrf ?? '', 'Idempotency-Key': crypto.randomUUID() },
    multipart: { file: { name: 'Automatic preparation UI fixture.pdf', mimeType: 'application/pdf',
      buffer: Buffer.concat([await original.body(), Buffer.from('\n% UI fixture ' + crypto.randomUUID() + '\n')]) } },
  });
  if (!imported.ok()) throw new Error('automatic_fixture_upload_failed');
  const uploaded = await imported.json();
  if (!uploaded.preparation_run_id) throw new Error('upload_did_not_schedule_preparation');
  await page.reload();
  await page.getByLabel('当前阅读文献', { exact: true }).selectOption(uploaded.version_id);
  await expect(progress).toBeVisible();
  await page.screenshot({ path: path.join(root, 'var/reports/chat-preparation-in-progress.png'), fullPage: true });
  await expect(progress).toContainText('原文索引已校验', { timeout: 180000 });
  output.checks.push('fresh_upload_to_index_ready_without_manual_parse_or_index');
  await page.getByLabel('当前阅读文献', { exact: true }).selectOption(fixture.documents[0].version_id);
  await expect(progress).toContainText('原文索引已校验');
  await page.screenshot({ path: path.join(root, 'var/reports/chat-entry-desktop.png'), fullPage: true });
  output.checks.push('prominent_document_to_chat_entry_and_composer');
  await question.fill('测试仪器的温度是多少摄氏度？');
  await chat.getByRole('button', { name: '发送问题', exact: true }).click();
  await expect(chat.locator('.chat-answer')).toHaveCount(1, { timeout: 180000 });
  await expect(chat.locator('.chat-answer').first()).toContainText('23');
  output.checks.push('actual_local_question_and_answer');
  const followup = chat.getByRole('textbox', { name: '继续追问', exact: true });
  await followup.fill('这个温度数据来自什么仪器？请给出原文依据。');
  await followup.press('Enter');
  await expect(chat.locator('.chat-answer')).toHaveCount(2, { timeout: 180000 });
  await expect(chat.locator('.chat-message.user')).toHaveCount(2);
  await page.reload();
  await expect(page.locator('.chat-answer')).toHaveCount(2, { timeout: 30000 });
  output.checks.push('same_conversation_followup_and_reload_restore');
  await page.screenshot({ path: path.join(root, 'var/reports/chat-thread-desktop.png'), fullPage: true });
  await page.locator('.chat-citations button').first().click();
  await expect(page.getByRole('region', { name: '原文证据阅读器' })).toBeVisible();
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 30000 });
  output.checks.push('citation_opens_original_source');
  await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('button', { name: '发送问题', exact: true })).toBeVisible();
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw new Error('chat_mobile_overflow');
  const heading = await chat.locator('.chat-heading').boundingBox();
  const sources = await chat.locator('.chat-source-strip').boundingBox();
  const transcript = await chat.locator('.chat-transcript').boundingBox();
  if (!heading || !sources || !transcript || heading.y + heading.height > sources.y + 1 || sources.y + sources.height > transcript.y + 1) throw new Error('chat_mobile_sections_overlap');
  await page.screenshot({ path: path.join(root, 'var/reports/chat-mobile.png'), fullPage: true });
  output.checks.push('mobile_composer_and_no_overflow_or_section_overlap');
  await page.getByRole('button', { name: '新建对话', exact: true }).click();
  await expect(page.locator('.chat-message.user')).toHaveCount(0);
  await page.reload();
  await page.locator('.chat-history > summary').click();
  await expect(page.locator('.chat-history-item')).toHaveCount(2);
  output.checks.push('new_conversation_keeps_saved_history');
  output.passed = true;
} finally {
  fs.writeFileSync(path.join(root, 'var/reports/chat-ui.json'), JSON.stringify(output, null, 2) + '\n');
  await browser.close();
}
