import assert from "node:assert/strict";
import { test } from "node:test";

import { clearNii67ProjectViews } from "./nii67-project-state.ts";

test("project change clears frames and revokes preview urls", () => {
  const revoked: string[] = [];
  const next = clearNii67ProjectViews(
    { door: "blob:door", empty: "" },
    (url) => revoked.push(url),
  );
  assert.deepEqual(revoked, ["blob:door"]);
  assert.deepEqual(next.frames, []);
  assert.deepEqual(next.previews, {});
});
