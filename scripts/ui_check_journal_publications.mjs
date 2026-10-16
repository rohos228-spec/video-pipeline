// Визуальная проверка панелей «Публикации» и «Журнал» в Studio (Playwright, системный Chrome, headless).
// Сначала поднимите сервер: .venv\Scripts\python.exe scripts\ui_check_journal_publications_server.py (порт 18999, UICHECK_PORT).
// Запуск: node scripts/ui_check_journal_publications.mjs   (из любой папки; нужен web/node_modules)
// Скриншоты и файлы скачивания: UICHECK_OUT (по умолчанию C:/video-pipeline/_ui/run). Сеть наружу не используется,
// платных вызовов нет. Кнопки панели «Публикации» бьют только в локальный сервер и пишут решение человека в БД стенда.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(repoRoot, 'web', 'package.json'));
const { chromium } = require('@playwright/test');
const PORT = process.env.UICHECK_PORT || '18999';
const BASE = `http://127.0.0.1:${PORT}`;
const OUT = (process.env.UICHECK_OUT || 'C:/video-pipeline/_ui/run') + '/';
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok: !!ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' :: ' + detail : ''}`); };
const note = (s) => console.log('NOTE ' + s);

const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
const ctx = await browser.newContext({ viewport: { width: 1500, height: 1100 }, acceptDownloads: true });
const page = await ctx.newPage();

const consoleErrors = [], badResponses = [], pageErrors = [], reqs = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300) + ' @' + (m.location().url || '').replace(BASE, '')); });
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 300)));
page.on('response', (r) => { if (r.status() >= 400) badResponses.push(`${r.status()} ${r.request().method()} ${r.url().replace(BASE, '')}`); });
page.on('request', (r) => {
  const u = r.url().replace(BASE, '');
  if (u.startsWith('/api/projects/') && (u.includes('/timeline') || u.includes('/publications'))) {
    reqs.push({ method: r.method(), url: u, headers: r.headers(), body: r.postData() });
  }
});
const since = (n) => reqs.slice(n);
const mark = () => reqs.length;

try {
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  const row1 = page.locator('[data-project-id="1"]');
  await row1.waitFor({ timeout: 30000 });
  await row1.click();
  const pub = page.getByTestId('publications-panel');
  const jr = page.getByTestId('journal-panel');
  await pub.waitFor({ timeout: 20000 });
  await jr.waitFor({ timeout: 20000 });
  await page.getByTestId('journal-row').first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(1200);
  const shotEl = async (loc, name) => { await loc.scrollIntoViewIfNeeded(); await page.waitForTimeout(250); await loc.screenshot({ path: OUT + name }); };

  // ---------- 1. Публикации: рендер ----------
  const pubText = await pub.innerText();
  note('publications text: ' + pubText.replace(/\n+/g, ' | ').slice(0, 900));
  await shotEl(pub, '10-publications-initial.png');
  check('pub: панель отрисована', pubText.includes('Публикации'));
  check('pub: заголовок говорит, что автопубликация выключена', pubText.includes('автопубликация выключена'));
  check('pub: общее состояние по-русски («Ждёт проверки человека»)', /Ждёт проверки человека/.test(pubText));
  check('pub: сырые коды состояний не показаны (unconfirmed/failed_before_click/not_started)', !/unconfirmed|failed_before_click|failed_after_click|not_started|publish_skipped/.test(pubText), pubText.match(/unconfirmed|failed_before_click|not_started|publish_skipped/g)?.join(','));
  const li = (p) => pub.locator('li', { hasText: new RegExp('^\\s*' + p) }).first();
  for (const [p, label] of [['tiktok', 'опубликовано'], ['yt_shorts', 'ждёт проверки человека'], ['ig_reels', 'сбой до клика'], ['vk_clips', 'пропущено'], ['likee', 'ещё не начиналось']]) {
    const t = await li(p).innerText().catch(() => '');
    check(`pub: площадка ${p} показывает «${label}»`, t.includes(label), t.replace(/\n+/g, ' | ').slice(0, 160));
  }
  const btn = (p, name) => li(p).getByRole('button', { name });
  check('pub: tiktok (подтверждена) без кнопок', (await li('tiktok').getByRole('button').count()) === 0);
  check('pub: yt_shorts есть «Подтвердить вручную» и «Не опубликовано»', (await btn('yt_shorts', 'Подтвердить вручную').count()) === 1 && (await btn('yt_shorts', 'Не опубликовано').count()) === 1);
  check('pub: vk_clips и likee есть «Разрешить публикацию»', (await btn('vk_clips', 'Разрешить публикацию').count()) === 1 && (await btn('likee', 'Разрешить публикацию').count()) === 1);
  check('pub: кнопки ручных действий выключены, пока нет текста', await btn('yt_shorts', 'Подтвердить вручную').isDisabled() && await btn('yt_shorts', 'Не опубликовано').isDisabled());
  await li('yt_shorts').getByRole('textbox').fill('abc');
  check('pub: 3 символа всё ещё держат кнопки выключенными (минимум 5)', await btn('yt_shorts', 'Подтвердить вручную').isDisabled());
  await li('yt_shorts').getByRole('textbox').fill('');

  // ---------- 2. Публикации: действия ----------
  // 2a. «Подтвердить вручную» со ссылкой
  let m = mark();
  await li('yt_shorts').getByRole('textbox').fill('https://www.youtube.com/shorts/uicheck123');
  await shotEl(li('yt_shorts'), '11-pub-yt-filled.png');
  await btn('yt_shorts', 'Подтвердить вручную').click();
  await page.waitForTimeout(1800);
  let r = since(m).find((x) => x.method === 'POST');
  check('pub: confirm-manual ушёл POST на /publications/yt_shorts/confirm-manual', r && /\/publications\/yt_shorts\/confirm-manual$/.test(r.url), r?.url);
  check('pub: confirm-manual несёт заголовок X-VP-Operator', !!(r && r.headers['x-vp-operator'] && r.headers['x-vp-operator'].length > 20), r ? 'header len ' + (r.headers['x-vp-operator'] || '').length : 'нет запроса');
  const b1 = r?.body ? JSON.parse(r.body) : {};
  check('pub: тело confirm-manual = {sha256, post_url} без reason', !!b1.sha256 && b1.post_url === 'https://www.youtube.com/shorts/uicheck123' && b1.reason === undefined, r?.body);
  const noteTxt = await pub.locator('[role=status]').allInnerTexts();
  note('status lines after confirm: ' + JSON.stringify(noteTxt));
  check('pub: после confirm-manual показано сообщение результата', noteTxt.some((t) => /yt_shorts|подтвержд|Готово|вруч/i.test(t) && !/Ждёт проверки/.test(t)), JSON.stringify(noteTxt));
  await shotEl(pub, '12-pub-after-confirm.png');
  const ytAfter = await li('yt_shorts').innerText();
  check('pub: yt_shorts стала «опубликовано» со ссылкой и пометкой «Вручную»', /опубликовано/.test(ytAfter) && ytAfter.includes('uicheck123') && /Вручную/.test(ytAfter), ytAfter.replace(/\n+/g, ' | '));
  check('pub: «Вручную: …» показывает русское действие, а не сырой id (confirm / mark_failed)', !/Вручную: (confirm|mark_failed)/.test(ytAfter) && !/mark_failed/.test(await li('ig_reels').innerText()), ytAfter.replace(/\n+/g, ' | ').slice(0, 200));
  check('pub: у подтверждённой площадки нет служебного «Код: manual_confirmed»', !/Код: manual_confirmed/.test(ytAfter), ytAfter.replace(/\n+/g, ' | ').slice(0, 200));
  check('pub: коды ошибок (not_logged_in/confirm_timeout/publish_disabled) показаны по-русски, не латиницей', !/Код: [a-z_]+/.test(pubText), (pubText.match(/Код: [a-z_]+/g) || []).join(' ; '));

  // 2b. «Не опубликовано» с причиной
  m = mark();
  await li('ig_reels').getByRole('textbox').fill('Площадка просит войти в аккаунт, пост не вышел');
  await btn('ig_reels', 'Не опубликовано').click();
  await page.waitForTimeout(1800);
  r = since(m).find((x) => x.method === 'POST');
  check('pub: mark-failed ушёл POST на /publications/ig_reels/mark-failed', r && /\/publications\/ig_reels\/mark-failed$/.test(r.url), r?.url);
  check('pub: mark-failed несёт X-VP-Operator', !!(r && r.headers['x-vp-operator']));
  const b2 = r?.body ? JSON.parse(r.body) : {};
  check('pub: тело mark-failed = {sha256, reason}', !!b2.sha256 && /Площадка просит/.test(b2.reason || ''), r?.body);
  await shotEl(pub, '13-pub-after-markfailed.png');
  const igAfter = await li('ig_reels').innerText();
  note('ig_reels after mark-failed: ' + igAfter.replace(/\n+/g, ' | '));
  check('pub: после «Не опубликовано» у ig_reels видна ручная отметка', /Вручную/.test(igAfter), igAfter.replace(/\n+/g, ' | ').slice(0, 200));

  // 2c. «Разрешить публикацию»
  m = mark();
  await btn('vk_clips', 'Разрешить публикацию').click();
  await page.waitForTimeout(1800);
  r = since(m).find((x) => x.method === 'POST');
  check('pub: approve ушёл POST на /publications/approve', r && /\/publications\/approve$/.test(r.url), r?.url);
  check('pub: approve несёт X-VP-Operator', !!(r && r.headers['x-vp-operator']));
  const b3 = r?.body ? JSON.parse(r.body) : {};
  check('pub: тело approve = {platforms:[vk_clips], sha256, caption_sha256}', JSON.stringify(b3.platforms) === '["vk_clips"]' && !!b3.sha256 && !!b3.caption_sha256, r?.body);
  const vkAfter = await li('vk_clips').innerText();
  check('pub: vk_clips показывает «разрешено вами» и кнопка «Разрешить публикацию» исчезла', /разрешено вами/.test(vkAfter) && (await btn('vk_clips', 'Разрешить публикацию').count()) === 0, vkAfter.replace(/\n+/g, ' | '));
  const noteTxt3 = await pub.locator('[role=status]').allInnerTexts();
  note('status lines after approve: ' + JSON.stringify(noteTxt3));
  check('pub: после approve показано сообщение «Подтверждение записано… ничего не публикуется»', noteTxt3.some((t) => /ничего не публикуется|Подтверждение записано/.test(t)), JSON.stringify(noteTxt3));
  await shotEl(pub, '14-pub-after-approve.png');

  // 2d. отказ сервера: показываем русское сообщение
  await page.route('**/api/projects/1/publications/approve', (route) => route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'stale_artifact', message: 'Видео было пересобрано. Обновите список.' } }) }));
  await btn('likee', 'Разрешить публикацию').click();
  await page.waitForTimeout(800);
  const alertTxt = await pub.getByRole('alert').allInnerTexts();
  check('pub: отказ 409 показывает русское сообщение сервера в alert', alertTxt.some((t) => t.includes('Видео было пересобрано')), JSON.stringify(alertTxt));
  await shotEl(pub, '15-pub-server-refusal.png');
  await page.unroute('**/api/projects/1/publications/approve');

  // ---------- 3. Журнал: рендер ----------
  await page.getByRole('button', { name: 'Обновить' }).click();
  await page.waitForTimeout(1500);
  await shotEl(jr, '20-journal-initial.png');
  const rows = page.getByTestId('journal-row');
  const n0 = await rows.count();
  const totals0 = await page.getByTestId('journal-totals').innerText();
  note('journal totals: ' + totals0);
  check('journal: отрисованы строки (ожидаем 100 на первой странице)', n0 === 100, 'строк ' + n0);
  check('journal: есть «Показать ещё»', (await jr.getByRole('button', { name: 'Показать ещё' }).count()) === 1);
  const badge = async () => rows.evaluateAll((els) => els.map((e) => (e.querySelector('div > span:nth-of-type(4)') || {}).textContent || ''));
  const kindsShown = [...new Set(await badge())];
  note('kind badges: ' + JSON.stringify(kindsShown));
  const rawBadges = kindsShown.filter((k) => /^[A-Za-z_]+$/.test(k) || /^Событие «/.test(k));
  check('journal: подписи видов записи по-русски (нет сырых node_error/retry_scheduled в бейджах)', rawBadges.length === 0, 'сырые бейджи: ' + JSON.stringify(rawBadges));
  const allText = await jr.innerText();
  const rawTitles = (allText.match(/(node_start|node_end|node_error|retry_scheduled)/g) || []);
  check('journal: сырые виды событий не торчат в тексте строк', rawTitles.length === 0, [...new Set(rawTitles)].join(','));
  const rawTables = (allText.match(/источник (spend_ledger|operator_audit|notification_log|publications|observation_events|node_runs)/g) || []);
  check('journal: служебные имена таблиц («источник spend_ledger») не показаны человеку', rawTables.length === 0, [...new Set(rawTables)].join(' ; '));
  const firstReq = reqs.find((x) => x.url.includes('/projects/1/timeline?'));
  check('journal: первая страница уходит на /api/projects/1/timeline?limit=100', firstReq && /\/api\/projects\/1\/timeline\?.*limit=100/.test(firstReq.url), firstReq?.url);

  // ---------- 4. Журнал: «Показать ещё» ----------
  m = mark();
  await jr.getByRole('button', { name: 'Показать ещё' }).click();
  await page.waitForTimeout(1500);
  const n1 = await rows.count();
  r = since(m).find((x) => x.url.includes('/timeline?'));
  check('journal: «Показать ещё» шлёт cursor и дозагружает строки', r && /cursor=/.test(r.url) && n1 > n0, `строк ${n0} -> ${n1}; ${r?.url.slice(0, 140)}`);
  check('journal: после полной загрузки показано «Это все записи» и кнопки нет', (await jr.getByText('Это все записи').count()) === 1 && (await jr.getByRole('button', { name: 'Показать ещё' }).count()) === 0);
  await shotEl(jr, '21-journal-all-loaded.png');
  const totals1 = await page.getByTestId('journal-totals').innerText();
  note('journal totals after more: ' + totals1);

  // ---------- 5. Журнал: фильтры ----------
  m = mark();
  await jr.getByRole('button', { name: 'Деньги', exact: true }).click();
  await page.waitForTimeout(1500);
  r = since(m).find((x) => x.url.includes('/timeline?'));
  check('journal: пресет «Деньги» шлёт kinds=spend,task', r && /kinds=spend%2Ctask|kinds=spend,task/.test(r.url), r?.url);
  const bMoney = [...new Set(await badge())];
  check('journal: после «Деньги» остаются только траты/задачи', bMoney.every((k) => /Трата|Задача провайдера/.test(k)), JSON.stringify(bMoney));
  await shotEl(jr, '22-journal-money.png');

  m = mark();
  await jr.getByRole('button', { name: 'Ошибки', exact: true }).click();
  await page.waitForTimeout(1500);
  r = since(m).find((x) => x.url.includes('/timeline?'));
  check('journal: пресет «Ошибки» шлёт severity=error', r && /severity=error/.test(r.url), r?.url);
  const sevAttrs = await rows.evaluateAll((els) => [...new Set(els.map((e) => e.getAttribute('data-severity')))]);
  check('journal: после «Ошибки» все строки с важностью error', sevAttrs.length === 1 && sevAttrs[0] === 'error', JSON.stringify(sevAttrs) + ' строк ' + (await rows.count()));
  await shotEl(jr, '23-journal-errors.png');

  m = mark();
  await jr.getByRole('button', { name: 'Сбросить фильтры' }).click();
  await page.waitForTimeout(1200);
  m = mark();
  await jr.getByLabel('Период').selectOption('24h');
  await page.waitForTimeout(1200);
  r = since(m).find((x) => x.url.includes('/timeline?'));
  check('journal: период «Последние 24 часа» шлёт since=…', r && /since=/.test(r.url), r?.url);

  m = mark();
  await jr.getByLabel('Важность').selectOption('warn');
  await page.waitForTimeout(1200);
  r = since(m).find((x) => x.url.includes('/timeline?'));
  check('journal: важность «внимание» шлёт severity=warn', r && /severity=warn/.test(r.url), r?.url);
  const sevWarn = await rows.evaluateAll((els) => [...new Set(els.map((e) => e.getAttribute('data-severity')))]);
  check('journal: после «внимание» остаются только warn', sevWarn.every((s) => s === 'warn'), JSON.stringify(sevWarn));
  await jr.getByRole('button', { name: 'Сбросить фильтры' }).click();
  await page.waitForTimeout(1000);

  // поиск
  m = mark();
  await jr.getByLabel('Поиск по журналу').fill('мелкая трата 7');
  await page.waitForTimeout(400 + 1200);
  const searchReqs = since(m).filter((x) => x.url.includes('/timeline?'));
  check('journal: поиск уходит одним запросом с q= (с задержкой)', searchReqs.length === 1 && /q=/.test(searchReqs[0].url), searchReqs.map((x) => x.url).join(' ; ').slice(0, 220));
  const nSearch = await rows.count();
  const sTxt = (await rows.allInnerTexts()).join('\n');
  check('journal: поиск «мелкая трата 7» оставляет подходящие строки', nSearch >= 1 && /мелкая трата 7/.test(sTxt), 'строк ' + nSearch);
  await shotEl(jr, '24-journal-search.png');
  await jr.getByLabel('Поиск по журналу').fill('такого-текста-нет-нигде-xyz');
  await page.waitForTimeout(1800);
  check('journal: пустой результат поиска даёт понятный текст «По этим фильтрам записей нет»', (await jr.getByTestId('journal-empty').count()) === 1, (await jr.getByTestId('journal-empty').allInnerTexts()).join(''));
  await shotEl(jr, '25-journal-empty-search.png');
  await jr.getByRole('button', { name: 'Сбросить фильтры' }).click();
  await page.waitForTimeout(1200);

  // действия оператора: наши POST уже должны быть в ленте
  m = mark();
  await jr.getByRole('button', { name: 'Действия оператора', exact: true }).click();
  await page.waitForTimeout(1500);
  const opTexts = (await rows.allInnerTexts()).slice(0, 8);
  note('operator rows (first 8): ' + JSON.stringify(opTexts.map((t) => t.replace(/\n+/g, ' | ').slice(0, 170))));
  const opJoined = opTexts.join('\n');
  check('journal: только что сделанные действия оператора видны в ленте', /publications\/approve/.test(opJoined) && /mark-failed/.test(opJoined) && /confirm-manual/.test(opJoined));
  check('journal: в строке оператора нет сырого шаблона маршрута с {project_id}', !/\{project_id\}|\{platform\}/.test(opJoined), (opJoined.match(/\{[a-z_]+\}/g) || []).slice(0, 3).join(','));
  await shotEl(jr, '26-journal-operator.png');
  await jr.getByRole('button', { name: 'Сбросить фильтры' }).click();
  await page.waitForTimeout(1000);

  // ---------- 6. Журнал: экспорт ----------
  m = mark();
  const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 20000 }), jr.getByRole('button', { name: 'Скачать CSV' }).click()]);
  const csvPath = OUT + 'export-' + dl.suggestedFilename();
  await dl.saveAs(csvPath);
  r = since(m).find((x) => x.url.includes('/timeline/export'));
  check('journal: «Скачать CSV» бьёт в /timeline/export?format=csv', r && /format=csv/.test(r.url), r?.url);
  const csv = fs.readFileSync(csvPath);
  const csvText = csv.toString('utf8');
  const lines = csvText.replace(/^\uFEFF/, '').split(/\r?\n/).filter(Boolean);
  note('csv file: ' + path.basename(csvPath) + ' bytes ' + csv.length + ' lines ' + lines.length + ' header: ' + lines[0]);
  check('journal: CSV с BOM, заголовком и строками на русском', csv[0] === 0xEF && lines.length > 100 && /Публикац|Деньги|Оператор/.test(csvText), `lines ${lines.length}`);
  check('journal: имя файла CSV осмысленное (zhurnal-proekt-1-…csv)', /^zhurnal-proekt-1-.*\.csv$/.test(dl.suggestedFilename()), dl.suggestedFilename());
  // экспорт с фильтром
  await jr.getByRole('button', { name: 'Ошибки', exact: true }).click();
  await page.waitForTimeout(1200);
  m = mark();
  const [dl2] = await Promise.all([page.waitForEvent('download', { timeout: 20000 }), jr.getByRole('button', { name: 'Скачать CSV' }).click()]);
  await dl2.saveAs(OUT + 'export-errors-' + dl2.suggestedFilename());
  r = since(m).find((x) => x.url.includes('/timeline/export'));
  check('journal: экспорт учитывает фильтр «Ошибки» (severity=error в запросе)', r && /severity=error/.test(r.url), r?.url);
  const csv2 = fs.readFileSync(OUT + 'export-errors-' + dl2.suggestedFilename(), 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/).filter(Boolean);
  check('journal: CSV с фильтром короче полного', csv2.length < lines.length && csv2.length >= 2, `${csv2.length} против ${lines.length}`);
  await jr.getByRole('button', { name: 'Сбросить фильтры' }).click();
  await page.waitForTimeout(800);
  const [dl3] = await Promise.all([page.waitForEvent('download', { timeout: 20000 }), jr.getByRole('button', { name: 'Скачать для разработчика' }).click()]);
  await dl3.saveAs(OUT + 'export-' + dl3.suggestedFilename());
  check('journal: «Скачать для разработчика» отдаёт .json', /\.json$/.test(dl3.suggestedFilename()), dl3.suggestedFilename());

  // ---------- 7. Журнал: ошибка сервера ----------
  await page.route('**/api/projects/1/timeline?**', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' }));
  await jr.getByRole('button', { name: 'Обновить' }).click();
  await page.waitForTimeout(1200);
  const errTxt = (await jr.getByRole('alert').allInnerTexts()).join(' ');
  check('journal: при 500 показана русская ошибка и «Повторить»', /Сервер не смог собрать журнал/.test(errTxt) && /Повторить/.test(errTxt), errTxt);
  await shotEl(jr, '27-journal-error-500.png');
  await page.unroute('**/api/projects/1/timeline?**');
  await jr.getByRole('button', { name: 'Повторить' }).click();
  await page.waitForTimeout(1500);
  check('journal: «Повторить» после сбоя возвращает записи', (await rows.count()) > 0);

  // ---------- 8. Пустой проект ----------
  await page.locator('[data-project-id="2"]').click();
  await page.waitForTimeout(2500);
  const pubE = await page.getByTestId('publications-panel').innerText();
  const jrE = await page.getByTestId('journal-panel').innerText();
  note('empty project pub: ' + pubE.replace(/\n+/g, ' | ') + ' // journal: ' + jrE.replace(/\n+/g, ' | ').slice(0, 300));
  check('empty: публикации говорят «Финального видео пока нет»', /Финального видео пока нет/.test(pubE));
  check('empty: журнал пустого проекта не показывает строки проекта 1', !/мелкая трата|Проверка журнала|yt_shorts/.test(jrE));
  check('empty: сообщение результата из проекта 1 не переехало в публикации проекта 2', !/Подтверждение записано|подтверждено вручную/.test(pubE), pubE.replace(/\n+/g, ' | ').slice(-160));
  await shotEl(page.getByTestId('publications-panel'), '30-empty-publications.png');
  await shotEl(page.getByTestId('journal-panel'), '31-empty-journal.png');
  await page.locator('[data-project-id="1"]').click();
  await page.waitForTimeout(2500);

  // ---------- 9. Телефон 390px ----------
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: OUT + '40-mobile-full-page.png' });
  const geo = async (sel) => page.evaluate((s) => {
    const de = document.documentElement;
    const out = { docScroll: de.scrollWidth, docClient: de.clientWidth };
    const el = document.querySelector(s);
    if (!el) return out;
    const rc = el.getBoundingClientRect();
    out.el = { scroll: el.scrollWidth, client: el.clientWidth, left: Math.round(rc.left), right: Math.round(rc.right), visibleWidth: Math.max(0, Math.round(Math.min(rc.right, de.clientWidth) - Math.max(rc.left, 0))) };
    const bad = [];
    el.querySelectorAll('*').forEach((c) => { const r2 = c.getBoundingClientRect(); if (r2.width > 0 && (r2.right > rc.right + 1 || r2.left < rc.left - 1)) bad.push(c.tagName + '.' + String(c.className).slice(0, 40) + ' right=' + Math.round(r2.right)); });
    out.overflowing = bad.slice(0, 6);
    return out;
  }, sel);
  const gPub = await geo('[data-testid=publications-panel]');
  const gJr = await geo('[data-testid=journal-panel]');
  note('mobile 390 real layout, publications: ' + JSON.stringify(gPub));
  note('mobile 390 real layout, journal: ' + JSON.stringify(gJr));
  check('mobile 390: у страницы нет горизонтального скролла (document)', gPub.docScroll <= gPub.docClient + 1, `scrollWidth ${gPub.docScroll} / clientWidth ${gPub.docClient}`);
  check('mobile 390: панель «Журнал» в настоящей раскладке Studio читаема (видимая ширина >= 300px)', gJr.el && gJr.el.visibleWidth >= 300, JSON.stringify(gJr.el));
  check('mobile 390: панель «Публикации» в настоящей раскладке Studio читаема (видимая ширина >= 300px)', gPub.el && gPub.el.visibleWidth >= 300, JSON.stringify(gPub.el));
  // боковые колонки на телефоне: скрыты, открываются кнопками «Проекты» / «Инспектор» поверх центра
  const bar = page.getByTestId('mobile-panels-bar');
  check('mobile 390: есть полоса кнопок «Проекты» и «Инспектор»', await bar.isVisible() && await page.getByTestId('open-projects-drawer').isVisible() && await page.getByTestId('open-inspector-drawer').isVisible());
  check('mobile 390: боковые колонки по умолчанию скрыты (проекты и инспектор не делят экран)', !(await page.locator('[data-project-id="1"]').isVisible()) && (await page.locator('aside').filter({ hasText: 'Инспектор' }).count() === 0 || !(await page.locator('aside').filter({ hasText: 'Инспектор' }).first().isVisible())));
  await page.getByTestId('open-projects-drawer').click();
  await page.waitForTimeout(400);
  const drawerBox = await page.locator('[data-project-id="1"]').locator('xpath=ancestor::aside[1]').boundingBox();
  note('mobile drawer projects: ' + JSON.stringify(drawerBox));
  check('mobile 390: «Проекты» выезжает поверх центра шириной 240..340px и влезает в экран', drawerBox && drawerBox.width >= 240 && drawerBox.width <= 340 && drawerBox.x + drawerBox.width <= 391, JSON.stringify(drawerBox));
  await page.screenshot({ path: OUT + '44-mobile-projects-drawer.png' });
  await page.locator('[data-project-id="2"]').click();
  await page.waitForTimeout(800);
  check('mobile 390: выбор проекта закрывает выезжающую колонку', !(await page.locator('[data-project-id="1"]').isVisible()));
  check('mobile 390: после выбора проекта 2 панели переключились на пустой проект', /Финального видео пока нет/.test(await page.getByTestId('publications-panel').innerText()));
  await page.getByTestId('open-inspector-drawer').click();
  await page.waitForTimeout(400);
  const insp = page.locator('aside').filter({ hasText: 'Инспектор' }).first();
  const inspBox = await insp.boundingBox();
  check('mobile 390: «Инспектор» выезжает поверх центра и влезает в экран', inspBox && inspBox.width >= 240 && inspBox.width <= 340 && inspBox.x + inspBox.width <= 391, JSON.stringify(inspBox));
  await page.screenshot({ path: OUT + '45-mobile-inspector-drawer.png' });
  await page.getByRole('button', { name: 'Закрыть боковую панель' }).click({ position: { x: 25, y: 300 } });
  await page.waitForTimeout(300);
  check('mobile 390: подложка закрывает «Инспектор»', !(await insp.isVisible()));
  await page.getByTestId('open-projects-drawer').click();
  await page.waitForTimeout(300);
  await page.locator('[data-project-id="1"]').click();
  await page.waitForTimeout(1500);

  // та же панель, но в колонке ровно 390px (как на телефоне, если бы Studio убирала боковые колонки): проверяем сами компоненты
  await page.setViewportSize({ width: 1500, height: 1100 });
  await page.waitForTimeout(800);
  await page.evaluate(() => {
    const j = document.querySelector('[data-testid=journal-panel]');
    const box = j && j.closest('.overflow-y-auto');
    if (box) { box.style.width = '390px'; box.style.flex = 'none'; box.style.maxWidth = '390px'; box.parentElement.style.width = '390px'; box.parentElement.style.maxWidth = '390px'; box.parentElement.parentElement.style.width = '390px'; box.parentElement.parentElement.style.maxWidth = '390px'; box.parentElement.parentElement.style.overflow = 'hidden'; }
  });
  await page.waitForTimeout(500);
  const e1 = await geo('[data-testid=publications-panel]');
  const e2 = await geo('[data-testid=journal-panel]');
  note('emulated 390px column, publications: ' + JSON.stringify(e1));
  note('emulated 390px column, journal: ' + JSON.stringify(e2));
  await shotEl(page.getByTestId('publications-panel'), '41-column390-publications.png');
  await shotEl(page.getByTestId('journal-panel'), '42-column390-journal.png');
  check('column 390px: «Публикации» без выхода за границы панели', e1.el && e1.el.scroll <= e1.el.client + 1 && !(e1.overflowing || []).length, JSON.stringify(e1.el) + ' ' + JSON.stringify(e1.overflowing));
  check('column 390px: «Журнал» без выхода за границы панели', e2.el && e2.el.scroll <= e2.el.client + 1 && !(e2.overflowing || []).length, JSON.stringify(e2.el) + ' ' + JSON.stringify(e2.overflowing));
  await page.getByTestId('journal-panel').getByRole('button', { name: 'Ошибки', exact: true }).click();
  await page.waitForTimeout(1200);
  await shotEl(page.getByTestId('journal-panel'), '43-column390-journal-errors.png');
  // десктоп: раскладка прежняя (колонка проектов и инспектор рядом с центром), мобильной полосы нет
  await page.setViewportSize({ width: 1500, height: 1100 });
  await page.waitForTimeout(800);
  await page.evaluate(() => { document.querySelectorAll('[style]').forEach((e) => { if (e.style && e.style.maxWidth === '390px') { e.style.width = ''; e.style.maxWidth = ''; e.style.flex = ''; e.style.overflow = ''; } }); });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('[data-project-id="1"]').waitFor({ timeout: 30000 });
  await page.waitForTimeout(1500);
  const dSide = await page.locator('[data-project-id="1"]').locator('xpath=ancestor::aside[1]').boundingBox();
  const dInsp = await page.locator('aside').filter({ hasText: 'Инспектор' }).first().boundingBox();
  const dMain = await page.locator('main').boundingBox();
  note('desktop 1500: sidebar ' + JSON.stringify(dSide) + ' inspector ' + JSON.stringify(dInsp) + ' main ' + JSON.stringify(dMain));
  check('desktop 1500: мобильной полосы нет', !(await page.getByTestId('mobile-panels-bar').isVisible()));
  check('desktop 1500: колонка проектов слева 240..560px, инспектор справа 320px, центр шире 600px', dSide && dInsp && dMain && dSide.x === 0 && dSide.width >= 240 && dSide.width <= 560 && Math.round(dInsp.width) === 320 && dMain.width > 600, JSON.stringify({ dSide, dInsp, dMain }));
  await page.screenshot({ path: OUT + '46-desktop-full-page.png' });
} catch (e) {
  check('сценарий дошёл до конца без исключения', false, String(e).slice(0, 400));
  try { await page.screenshot({ path: OUT + '99-failure.png' }); } catch {}
} finally {
  const uniq = (a) => [...new Set(a)];
  console.log('CONSOLE_ERRORS ' + JSON.stringify(uniq(consoleErrors)));
  console.log('PAGE_ERRORS ' + JSON.stringify(uniq(pageErrors)));
  console.log('BAD_RESPONSES ' + JSON.stringify(uniq(badResponses)));
  check('браузер: нет pageerror', pageErrors.length === 0, uniq(pageErrors).join(' ; '));
  await ctx.close();
  await browser.close();
  const failed = results.filter((x) => !x.ok);
  console.log(`ИТОГО: ${results.length - failed.length} из ${results.length} проверок прошли`);
  fs.writeFileSync(OUT + 'results.json', JSON.stringify({ results, consoleErrors: uniq(consoleErrors), pageErrors: uniq(pageErrors), badResponses: uniq(badResponses) }, null, 1));
}
