import assert from "node:assert/strict";
import { test } from "node:test";

import {
  montageOpsToApply,
  pendingApplyFrameNumbers,
  queueAfterApply,
  queueAfterCoverageApply,
} from "./montage-apply-queue.ts";

test("apply queue counts video and other non-coverage ops", () => {
  const ops = [
    { type: "image_ai_change", frame_number: 2, shot: 1 },
    { type: "video_ai_change", frame_number: 4, shot: 1 },
    { type: "coverage_delete", frame_number: 2, shot: 1 },
    { type: "trim", frame_number: 9 },
  ];
  assert.deepEqual(pendingApplyFrameNumbers(ops), [2, 4, 9]);
  const sent = montageOpsToApply(ops, [2]);
  assert.deepEqual(
    sent.map((op) => op.type),
    ["image_ai_change"],
  );
  const sentKeys = new Set(sent.map((op) => `${op.type}:${op.frame_number}:${op.shot}`));
  const left = queueAfterApply(
    ops,
    sentKeys,
    (op) => `${op.type}:${op.frame_number}:${op.shot}`,
  );
  assert.deepEqual(
    left.map((op) => op.type),
    ["video_ai_change", "coverage_delete", "trim"],
  );
});

test("apply all keeps every coverage op", () => {
  const ops = [
    { type: "video_regen", frame_number: 1, shot: 2 },
    { type: "coverage_plan", frame_number: 1, shot: 1 },
  ];
  const sent = montageOpsToApply(ops);
  assert.deepEqual(sent.map((op) => op.type), ["video_regen"]);
  const left = queueAfterApply(
    ops,
    new Set(sent.map((op) => op.type)),
    (op) => op.type,
  );
  assert.deepEqual(left.map((op) => op.type), ["coverage_plan"]);
});

test("coverage apply drops only the applied frame and type", () => {
  const prev = [
    { type: "coverage_plan", frame_number: 2 },
    { type: "coverage_place", frame_number: 2 },
    { type: "coverage_plan", frame_number: 5 },
    { type: "image_ai_change", frame_number: 2 },
  ];
  const left = queueAfterCoverageApply(prev, [2], new Set(["coverage_plan"]));
  assert.deepEqual(
    left.map((op) => `${op.type}:${op.frame_number}`),
    ["coverage_place:2", "coverage_plan:5", "image_ai_change:2"],
  );
});
