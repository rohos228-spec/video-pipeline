// Headless Chrome pass over the media recovery panel. Start scripts/e2e_media_recovery_ui_server.py first (E2E_PORT=8803),
// run with node from anywhere; screenshots go to E2E_OUT (default: <system temp>/vp_ui_check/media/). No network, no real spend:
// the resume plan is only previewed (never run); the release plan runs against a throw-away ledger.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(repoRoot, 'web', 'package.json'));
const { chromium } = require('@playwright/test');
const out = (process.env.E2E_OUT || path.join(os.tmpdir(), 'vp_ui_check', 'media')) + path.sep;
fs.mkdirSync(out, { recursive: true });
const PORT = process.env.E2E_PORT || '8803';
const BASE = `http://127.0.0.1:${PORT}`;
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
const page = await browser.newPage({ viewport: { width: 1500, height: 1200 } });
const errors = [];
const writes = [];
page.on('response', r => { if (r.status() >= 500 && !r.url().includes('from-text')) errors.push(r.status() + ': ' + r.url()); });
page.on('request', r => { if (r.method() !== 'GET' && /media-recovery/.test(r.url())) writes.push(r.method() + ' ' + r.url()); });
page.on('pageerror', e => errors.push('pageerror: ' + e));
page.on('console', m => { if (m.type() === 'error' && !/500|502/.test(m.text())) errors.push('console: ' + m.text()); });
await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
const row = page.locator('[data-project-id="1"]');
await row.waitFor({ timeout: 30000 });
await row.click();
await page.waitForTimeout(2500);
const big = page.getByTitle('Большое окно'); // the orchestrator panel is a 208px strip until it is expanded
if (await big.count()) await big.click();
await page.waitForTimeout(800);
const panel = page.getByTestId('media-recovery-panel');
await panel.scrollIntoViewIfNeeded();
await panel.locator('summary').click();
await page.getByTestId('media-recovery-row').first().waitFor({ timeout: 20000 });
await page.waitForTimeout(500);
const shot = async (name, el = panel) => { await el.scrollIntoViewIfNeeded(); await page.waitForTimeout(200); await page.screenshot({ path: out + name }); };
const text = async (loc) => (await loc.innerText()).replace(/\n+/g, ' | ');
await shot('01-panel.png');
console.log('summary:', await text(page.getByTestId('media-recovery-coverage-summary')));
const rows = page.getByTestId('media-recovery-row');
for (let i = 0; i < await rows.count(); i++) console.log('row:', (await text(rows.nth(i))).slice(0, 420));
const api = await (await fetch(BASE + '/api/projects/1/observations/node-coverage')).json();
console.log('API coverage summary:', JSON.stringify(api.summary));

// 1 resume: items is resumable, videos is not (an unknown-outcome key blocks the node)
const itemsRow = page.locator('[data-testid="media-recovery-row"][data-node="items"]');
const videosRow = page.locator('[data-testid="media-recovery-row"][data-node="videos"]');
console.log('videos resume disabled:', await videosRow.getByTestId('media-resume-button').isDisabled(), '|', await text(videosRow.getByTestId('media-resume-why')));
console.log('items resume disabled:', await itemsRow.getByTestId('media-resume-button').isDisabled());
await itemsRow.getByTestId('media-resume-button').click();
await page.getByTestId('action-plan-card').first().waitFor({ timeout: 20000 });
await page.waitForTimeout(500);
await shot('02-resume-plan.png');
console.log('resume plan card:', (await text(panel.getByTestId('action-plan-card'))).slice(0, 600));
console.log('resume plan buttons:', await panel.getByRole('button', { name: /Выполнить|Подтвердить/ }).allInnerTexts());
await panel.getByRole('button', { name: 'Отклонить план' }).click();

// 2 unknown outcome
await shot('03-unknown-block.png', page.getByTestId('media-unknown-block'));
console.log('unknown rows:', await page.getByTestId('media-unknown-row').allInnerTexts());
await page.getByTestId('media-unknown-row').filter({ hasText: 'clip_005' }).getByTestId('media-release-button').click();
const dlg = page.getByTestId('media-release-dialog');
await dlg.waitFor();
await page.waitForTimeout(400);
await page.screenshot({ path: out + '04-release-dialog-empty.png' });
console.log('dialog plan button disabled (empty):', await dlg.getByTestId('media-release-plan').isDisabled());
await dlg.getByTestId('media-release-reason').fill('коротко');
console.log('dialog plan button disabled (7 chars):', await dlg.getByTestId('media-release-plan').isDisabled());
await dlg.getByTestId('media-release-reason').fill('Сверил историю задач и баланс Kie: списания по clip_005 не было');
console.log('dialog plan button disabled (ok reason):', await dlg.getByTestId('media-release-plan').isDisabled());
await page.screenshot({ path: out + '05-release-dialog-filled.png' });
console.log('dialog text:', await text(dlg));
await dlg.getByTestId('media-release-plan').click();
await page.getByTestId('action-plan-card').first().waitFor({ timeout: 20000 });
await page.waitForTimeout(600);
await shot('06-release-plan.png');
console.log('release plan card:', (await text(panel.getByTestId('action-plan-card'))).slice(0, 900));
console.log('release plain run button present:', await panel.getByRole('button', { name: 'Выполнить', exact: true }).count());
await panel.getByRole('button', { name: /Подтвердить и выполнить/ }).click();
await panel.getByRole('alertdialog').waitFor();
await shot('07-release-confirm.png');
console.log('confirm dialog:', await text(panel.getByRole('alertdialog')));
await panel.getByRole('button', { name: 'Да, подтверждаю и выполняю' }).click();
await page.getByTestId('action-run-progress').first().waitFor({ timeout: 20000 });
await page.waitForTimeout(4000);
await shot('08-release-done.png');
console.log('release progress:', (await text(panel.getByTestId('action-run-progress'))).slice(0, 500));
console.log('unknown rows after release:', await page.getByTestId('media-unknown-row').allInnerTexts());
const after = await (await fetch(BASE + '/api/projects/1/media-recovery/unknown-outcomes')).json();
console.log('API unknown after:', after.blocked.map(b => b.key + '/' + b.provider).join(', '));

// 3 retry hint: the first from-text answers 502 retryable, the retry goes through
let first = true;
await page.route('**/api/action-layer/plans/from-text', async (route) => {
  if (first) {
    first = false;
    await route.fulfill({ status: 502, contentType: 'application/json',
      body: JSON.stringify({ detail: { code: 'planner_transport', message: 'Планировщик временно недоступен', retryable: true } }) });
  } else await route.continue();
});
const alp = page.getByTestId('action-layer-panel');
await alp.scrollIntoViewIfNeeded();
await alp.getByPlaceholder(/что сделать/).fill('покажи состояние доски монтажа');
await alp.getByRole('button', { name: 'Составить план' }).click();
await page.getByTestId('planner-retry').waitFor({ timeout: 20000 });
await alp.screenshot({ path: out + '09-planner-retry.png' });
console.log('retry error:', await text(page.getByTestId('action-command-bar')));
await page.getByTestId('planner-retry').click();
await page.getByText('План до выполнения').first().waitFor({ timeout: 20000 });
console.log('after retry plan card shown; retry button count:', await page.getByTestId('planner-retry').count());
await alp.screenshot({ path: out + '10-planner-retry-ok.png' });

console.log('direct writes to media-recovery from the page:', JSON.stringify(writes));
console.log('errors:', JSON.stringify(errors));
await browser.close();
