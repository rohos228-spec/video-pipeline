// Тесты чистой логики журнала. Запуск (из каталога web):
//   node --experimental-strip-types --test src/lib/journal-view.test.mjs
// Файл .mjs, поэтому tsc/next его не собирают.
import test from "node:test";
import assert from "node:assert/strict";
import * as v from "./journal-view.ts";

const NOW = new Date("2026-10-07T12:00:00Z");
const item = (id, ts, extra = {}) => ({
  id, ts, kind: "spend", source: "spend_ledger", severity: "info", title_ru: "t", detail_ru: "",
  project_id: 1, run_id: null, request_id: null, ref: null, amount_usd: null, ...extra,
});

test("пустые фильтры: только limit", () => {
  const u = v.buildTimelineUrl(7, v.EMPTY_FILTERS, { now: NOW });
  assert.equal(u, "/api/projects/7/timeline?limit=100");
});

test("все фильтры и курсор попадают в запрос, kinds отсортированы без дублей", () => {
  const f = { kinds: ["task", "spend", "spend"], severity: "error", period: "24h", q: "  ошибка 500 " };
  const u = new URL(v.buildTimelineUrl(3, f, { now: NOW, cursor: "abc=", limit: 50 }), "http://x");
  assert.equal(u.pathname, "/api/projects/3/timeline");
  assert.equal(u.searchParams.get("kinds"), "spend,task");
  assert.equal(u.searchParams.get("severity"), "error");
  assert.equal(u.searchParams.get("q"), "ошибка 500");
  assert.equal(u.searchParams.get("since"), "2026-10-06T12:00:00.000Z");
  assert.equal(u.searchParams.get("cursor"), "abc=");
  assert.equal(u.searchParams.get("limit"), "50");
});

test("limit ограничен 1..500", () => {
  assert.equal(v.clampLimit(0), 1);
  assert.equal(v.clampLimit(9999), 500);
  assert.equal(v.clampLimit(NaN), 100);
});

test("экспорт: те же фильтры, без limit и cursor, с форматом", () => {
  const u = new URL(v.buildExportUrl(3, { ...v.EMPTY_FILTERS, severity: "warn" }, "csv", NOW), "http://x");
  assert.equal(u.pathname, "/api/projects/3/timeline/export");
  assert.equal(u.searchParams.get("format"), "csv");
  assert.equal(u.searchParams.get("severity"), "warn");
  assert.equal(u.searchParams.has("limit"), false);
  assert.equal(u.searchParams.has("cursor"), false);
});

test("имя файла экспорта", () => {
  assert.equal(v.exportFileName(5, "json", NOW), "zhurnal-proekt-5-2026-10-07-12-00-00.json");
});

test("слияние страниц: без дублей, ts по убыванию", () => {
  const a = [item("spend:3", "2026-10-07T10:00:00Z"), item("spend:2", "2026-10-07T09:00:00Z")];
  const b = [item("spend:2", "2026-10-07T09:00:00Z"), item("spend:1", "2026-10-07T08:00:00Z")];
  assert.deepEqual(v.mergeItems(a, b).map((x) => x.id), ["spend:3", "spend:2", "spend:1"]);
});

test("слияние: одинаковый ts упорядочен по id по убыванию", () => {
  const m = v.mergeItems([item("a:1", "T"), item("b:1", "T")], []);
  assert.deepEqual(m.map((x) => x.id), ["b:1", "a:1"]);
});

test("быстрые наборы и определение активного", () => {
  const money = v.applyPreset(v.EMPTY_FILTERS, "money");
  assert.deepEqual(money.kinds, ["spend", "task"]);
  assert.equal(v.activePresetId(money), "money");
  assert.equal(v.activePresetId(v.EMPTY_FILTERS), "all");
  assert.equal(v.activePresetId(v.applyPreset(v.EMPTY_FILTERS, "errors")), "errors");
  assert.equal(v.activePresetId({ ...v.EMPTY_FILTERS, kinds: ["incident"] }), null);
  // период и поиск не мешают определить набор
  assert.equal(v.activePresetId({ ...money, period: "7d", q: "x" }), "money");
});

test("переключение вида", () => {
  const f1 = v.toggleKind(v.EMPTY_FILTERS, "spend");
  assert.deepEqual(f1.kinds, ["spend"]);
  assert.deepEqual(v.toggleKind(f1, "spend").kinds, []);
});

test("периоды", () => {
  assert.equal(v.periodSince("all", NOW), null);
  assert.equal(v.periodSince("7d", NOW), "2026-09-30T12:00:00.000Z");
  const today = new Date(v.periodSince("today", NOW));
  assert.ok(today <= NOW && NOW.getTime() - today.getTime() < 86_400_000 + 3_600_000);
});

test("итоги: деньги только по kind=spend, ошибки и предупреждения", () => {
  const t = v.summarize([
    item("spend:1", "1", { amount_usd: 0.1 }),
    item("spend:2", "2", { amount_usd: 0.2 }),
    item("task:1", "3", { kind: "task", amount_usd: 5 }),
    item("incident:1", "4", { kind: "incident", severity: "error" }),
    item("incident:2", "5", { kind: "incident", severity: "warn" }),
  ]);
  assert.deepEqual(t, { count: 5, errors: 1, warnings: 1, spentUsd: 0.3, spendRows: 2 });
});

test("подписи по-русски, неизвестные виды не ломаются", () => {
  assert.equal(v.kindLabel("spend"), "Трата");
  assert.equal(v.kindLabel("zzz"), "Событие «zzz»");
  assert.equal(v.severityLabel("error"), "Ошибка");
  assert.equal(v.severityLabel("???"), "Обычное");
  assert.match(v.severityClass("error").text, /red/);
  assert.match(v.severityClass("warn").text, /amber/);
});

test("деньги и время форматируются", () => {
  assert.equal(v.formatUsd(null), "—");
  assert.equal(v.formatUsd(1.234), "$1.23");
  assert.equal(v.formatUsd(0.0012), "$0.0012");
  assert.equal(v.formatTs("мусор"), "мусор");
  assert.match(v.formatTs("2026-10-07T12:00:00Z"), /^2026-10-07 \d\d:00:00$/);
});

test("нормализация ответа: пропуски, неизвестная важность, пустой курсор", () => {
  const p = v.normalizePage({ items: [{ id: "x:1", ts: "t", kind: "k", source: "s", severity: "weird" }, { nope: 1 }], next_cursor: "", sources_missing: ["publications"] });
  assert.equal(p.items.length, 1);
  assert.equal(p.items[0].severity, "info");
  assert.equal(p.items[0].amount_usd, null);
  assert.equal(p.next_cursor, null);
  assert.deepEqual(v.normalizePage(null), { items: [], next_cursor: null, total_hint: null, sources_missing: [] });
});

test("тексты про отсутствующие источники и ошибки", () => {
  assert.equal(v.missingSourcesText([]), "");
  assert.match(v.missingSourcesText(["publications", "foo"]), /публикации, foo/);
  assert.match(v.journalErrorText(500), /Сервер/);
  assert.match(v.journalErrorText(0), /не ответил/);
  assert.match(v.journalErrorText(422), /фильтры/);
});
