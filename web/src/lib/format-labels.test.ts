import assert from "node:assert/strict";
import { test } from "node:test";

import {
  formatRunOverlayLabel,
  humanizeSlug,
  nodeRunsFromPayload,
} from "./format-labels.ts";

test("humanizeSlug tolerates undefined and null", () => {
  assert.equal(humanizeSlug(undefined), "");
  assert.equal(humanizeSlug(null), "");
});

test("humanizeSlug still rewrites a real slug", () => {
  assert.equal(humanizeSlug("n_img_pr"), "img pr");
});

test("empty or slug-less run is the empty overlay state", () => {
  const empty: unknown[] = [
    undefined,
    null,
    [],
    {},
    { id: 1 },
    { status: "running" },
    { id: 1, status: "" },
    { id: 1, status: "   " },
  ];
  for (const payload of empty) {
    assert.equal(formatRunOverlayLabel(payload), null);
    assert.deepEqual(nodeRunsFromPayload(payload), []);
  }
});

test("a run with a status slug keeps the overlay label", () => {
  const run = {
    id: 4,
    status: "running",
    node_runs: [{ node_key: "n1" }],
  };
  assert.equal(formatRunOverlayLabel(run), "#4 · работает");
  assert.equal(nodeRunsFromPayload(run).length, 1);
});

test("a run without node_runs does not throw", () => {
  const run = { id: 2, status: "paused" };
  assert.equal(formatRunOverlayLabel(run), "#2 · пауза");
  assert.deepEqual(nodeRunsFromPayload(run), []);
});
