import assert from "node:assert/strict";
import { test } from "node:test";
import { reconcileNodeRunStatus } from "../src/lib/node-run-status.ts";

test("waiting_approval сохраняется и не переписывается в running/done", () => {
  assert.equal(
    reconcileNodeRunStatus("images", "waiting_approval", "paused"),
    "waiting_approval",
  );
  assert.equal(
    reconcileNodeRunStatus("images", "waiting_approval", "images_ready"),
    "waiting_approval",
  );
});

test("неизвестный статус ноды падает в нейтральный pending", () => {
  assert.equal(
    reconcileNodeRunStatus("plan", "some_future_status", "paused"),
    "pending",
  );
});
