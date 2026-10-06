// Headless Chrome pass over the command line card. Start scripts/e2e_command_line_ui_server.py first (port 8802, E2E_PORT to change),
// run with node from the repo root; screenshots go to C:/video-pipeline/_ui_check/cmdline/ (create the folder first). No network, no real spend.
import { createRequire } from 'node:module';
const require = createRequire('C:/video-pipeline/video-pipeline-observe-v1/web/package.json');
const { chromium } = require('@playwright/test');
const out = 'C:/video-pipeline/_ui_check/cmdline/';
const PORT = process.env.E2E_PORT || '8802';
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
const errors = [];
page.on('response', r => { if (r.status() >= 500) errors.push(r.status() + ': ' + r.url()); });
page.on('pageerror', e => errors.push('pageerror: ' + e));
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: 'domcontentloaded' });
const row = page.locator('[data-project-id="1"]');
await row.waitFor({ timeout: 30000 });
await row.click();
await page.waitForTimeout(2500);
const panel = page.getByTestId('action-layer-panel');
await panel.scrollIntoViewIfNeeded();
const input = panel.getByPlaceholder(/что сделать/);
const shot = async (name) => { await panel.scrollIntoViewIfNeeded(); await panel.screenshot({ path: out + name }); };
const plan = async (text) => { await input.fill(text); await panel.getByRole('button', { name: 'Составить план' }).click(); };
const card = page.getByTestId('action-plan-card');

// 1 read
await plan('покажи состояние доски монтажа');
await page.getByText('План до выполнения').waitFor({ timeout: 20000 });
await shot('30-read-plan.png');
console.log('read card:', (await card.innerText()).replace(/\n+/g, ' | ').slice(0, 400));
console.log('read: needs-confirm button present:', await page.getByRole('button', { name: /Подтвердить и выполнить/ }).count());
await page.getByRole('button', { name: 'Выполнить', exact: true }).click();
await page.getByTestId('action-run-progress').waitFor({ timeout: 20000 });
await page.waitForTimeout(2500);
await shot('31-read-done.png');
console.log('read progress:', (await page.getByTestId('action-run-progress').innerText()).replace(/\n+/g, ' | ').slice(0, 400));

// 2 local_safe write (swap shots of frame 4)
await plan('поменяй местами шот 1 и шот 2 в кадре 4');
await page.getByText('План до выполнения').waitFor({ timeout: 20000 });
await shot('32-swap-plan.png');
console.log('swap card:', (await card.innerText()).replace(/\n+/g, ' | ').slice(0, 500));
await page.getByRole('button', { name: 'Выполнить', exact: true }).click();
await page.waitForTimeout(3500);
await shot('33-swap-done.png');
console.log('swap progress:', (await page.getByTestId('action-run-progress').innerText()).replace(/\n+/g, ' | ').slice(0, 500));

// 3 destructive: needs confirmation
await plan('тест стирающее действие');
await page.getByText('План до выполнения').waitFor({ timeout: 20000 });
console.log('destructive: plain run button present:', await page.getByRole('button', { name: 'Выполнить', exact: true }).count());
await shot('34-destructive-plan.png');
await page.getByRole('button', { name: /Подтвердить и выполнить/ }).click();
await page.getByRole('alertdialog').waitFor();
await shot('35-destructive-confirm.png');
console.log('confirm dialog:', (await page.getByRole('alertdialog').innerText()).replace(/\n+/g, ' | '));
await page.getByRole('button', { name: 'Да, подтверждаю и выполняю' }).click();
await page.waitForTimeout(3000);
await shot('36-destructive-done.png');
console.log('destructive progress:', (await page.getByTestId('action-run-progress').innerText()).replace(/\n+/g, ' | ').slice(0, 400));

// 4 paid within cap, then paid over the cap
await plan('тест платное действие');
await page.getByText('План до выполнения').waitFor({ timeout: 20000 });
await shot('37-paid-plan.png');
console.log('paid card:', (await card.innerText()).replace(/\n+/g, ' | ').slice(0, 500));
await page.getByRole('button', { name: 'Отклонить план' }).click();
await plan('тест платное действие дважды');
await page.getByText('План до выполнения').waitFor({ timeout: 20000 });
await shot('38-paid-over-cap.png');
console.log('over cap card:', (await card.innerText()).replace(/\n+/g, ' | ').slice(0, 600));
console.log('over cap: approve button enabled:', await page.getByRole('button', { name: /Подтвердить и выполнить/ }).isEnabled());
await page.getByRole('button', { name: 'Отклонить план' }).click();

// 5 unknown phrase -> clarification, nothing built
await plan('удали проект');
await page.getByTestId('clarification-question').waitFor({ timeout: 20000 });
await shot('39-clarify.png');
console.log('clarify:', await page.getByTestId('clarification-question').innerText());
await page.getByRole('button', { name: 'Закрыть' }).first().click().catch(() => {});

// 6 kill switch from the UI, then a plan is refused
const ks = page.getByTestId('kill-switch-panel');
await ks.scrollIntoViewIfNeeded();
await ks.getByLabel('Основание аварийного стопа').fill('проверка командной строки');
await ks.getByRole('button', { name: 'Включить аварийный стоп' }).click();
await ks.getByRole('button', { name: 'Подтвердить: остановить всё' }).click();
await page.waitForTimeout(1500);
await ks.screenshot({ path: out + '40-kill-switch-on.png' });
console.log('kill switch:', (await ks.locator('h4').innerText()));
await panel.scrollIntoViewIfNeeded();
await plan('покажи состояние доски монтажа');
await page.waitForTimeout(2500);
await shot('41-planning-refused.png');
console.log('panel text under kill switch:', (await panel.innerText()).replace(/\n+/g, ' | ').slice(-600));
await ks.scrollIntoViewIfNeeded();
await ks.getByLabel('Основание аварийного стопа').fill('снято после проверки');
await ks.getByRole('button', { name: 'Снять аварийный стоп' }).click();
await page.waitForTimeout(1500);
console.log('kill switch after:', (await ks.locator('h4').innerText()));

// 7 history
await panel.scrollIntoViewIfNeeded();
const hist = page.getByTestId('action-history');
await hist.locator('summary').click();
await page.waitForTimeout(1500);
await hist.scrollIntoViewIfNeeded();
await hist.screenshot({ path: out + '42-history.png' });
console.log('history:', (await hist.innerText()).replace(/\n+/g, ' | ').slice(0, 900));
console.log('server errors/console:', JSON.stringify(errors));
await browser.close();
