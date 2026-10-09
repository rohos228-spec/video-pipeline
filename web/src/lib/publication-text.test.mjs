// Тесты текстов панели «Публикации». Запуск (из каталога web):
//   node --experimental-strip-types --test src/lib/publication-text.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import * as t from "./publication-text.ts";

test("коды причин по-русски, незнакомый код не показывается латиницей", () => {
  for (const c of ["confirm_timeout", "not_logged_in", "publish_disabled", "captcha", "no_success_marker"]) {
    assert.match(t.publicationCodeText(c), /[а-яё]/i, c);
    assert.doesNotMatch(t.publicationCodeText(c), new RegExp(c));
  }
  assert.equal(t.publicationCodeText(null), "");
  assert.doesNotMatch(t.publicationCodeText("zz_new_code"), /zz_new_code/);
});

test("ручные решения: русское действие, исполнитель без выдуманного имени", () => {
  assert.match(t.manualActionText("confirm"), /подтверждено вручную/);
  assert.match(t.manualActionText("mark_failed"), /не опубликовано/);
  assert.match(t.manualActionText("mark_failed_republish"), /повторная публикация/);
  assert.doesNotMatch(t.manualActionText("mystery"), /mystery/);
  assert.equal(t.actorText("operator"), "оператор");
  assert.equal(t.actorText(null), "оператор");
  assert.equal(t.actorText("operator:Анна"), "оператор Анна");
  assert.match(t.actorText("telegram:4242"), /Telegram.*4242/);
  assert.equal(t.isManualDecisionCode("manual_confirmed"), true);
  assert.equal(t.isManualDecisionCode("not_logged_in"), false);
});

test("«разрешено» при выключенной автопубликации не называется «публикация идёт»", () => {
  const d = { state: "publishing", busy: [], retry_approved: ["vk_clips"], needs_human: [] };
  const off = t.projectStateLines(d, false);
  assert.equal(off.length, 1);
  assert.match(off[0], /выключена настройкой/);
  assert.doesNotMatch(off[0], /Публикация идёт/);
  const on = t.projectStateLines(d, true);
  assert.match(on[0], /поставлена в очередь/);
  assert.match(t.projectStateLines({ ...d, busy: ["tiktok"] }, false)[0], /Публикация идёт/);
});

test("«ждёт проверки человека» не прячется за разрешённой площадкой", () => {
  const lines = t.projectStateLines({ state: "publishing", busy: [], retry_approved: ["a"], needs_human: ["b"] }, false);
  assert.equal(lines.length, 2);
  assert.match(lines[1], /Ждёт проверки человека/);
  assert.deepEqual(t.projectStateLines(null, true), []);
  assert.match(t.projectStateLines({ state: "published" }, true)[0], /опубликован/);
});
