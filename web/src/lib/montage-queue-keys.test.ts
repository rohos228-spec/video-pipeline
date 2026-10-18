import assert from "node:assert/strict";
import { test } from "node:test";

import { montageOpSelectKey, reconcileIdleQueue, serverQueueReplacesLocal } from "./montage-apply-queue.ts";

test("two image edits on one frame do not share a key", () => {
  const a = montageOpSelectKey({
    type: "image_ai_change",
    frame_number: 4,
    shot: 1,
    instruction: "светлее",
  });
  const b = montageOpSelectKey({
    type: "image_ai_change",
    frame_number: 4,
    shot: 1,
    slot: "parent",
    instruction: "темнее",
  });
  assert.notEqual(a, b);
  assert.match(a, /светлее$/);
  assert.match(b, /parent/);
});

test("shorter server queue replaces local only when it is the remaining suffix", () => {
  const key = (op: { id: string }) => op.id;
  const local = [{ id: "done" }, { id: "left" }];
  assert.equal(serverQueueReplacesLocal(local, [{ id: "left" }], key), true);
  assert.equal(serverQueueReplacesLocal(local, [{ id: "stale" }], key), false);
  assert.equal(serverQueueReplacesLocal(local, [], key), false);
});

test("empty server snapshot does not wipe a local queue or its data", () => {
  const key = (op: { id: string; instruction: string }) =>
    `${op.id}:${op.instruction}`;
  const local = [
    { id: "keep", instruction: "светлее фон" },
    { id: "also", instruction: "темнее лицо" },
  ];
  const snapshot = local.map((op) => ({ ...op }));
  assert.equal(serverQueueReplacesLocal(local, [], key), false);
  assert.deepEqual(local, snapshot);
});

test("foreign tab apply done clears the same snapshot and keeps dirty ops", () => {
  const key = (op: { id: string; instruction: string }) =>
    `${op.id}:${op.instruction}`;
  const applied = { id: "same", instruction: "светлее" };
  const dirty = { id: "dirty", instruction: "новое" };
  const local = [applied, dirty];
  const appliedKeys = new Set([key(applied)]);
  assert.deepEqual(reconcileIdleQueue(local, [], key, appliedKeys), [dirty]);
  assert.deepEqual(reconcileIdleQueue(local, [], key, new Set()), local);
});
