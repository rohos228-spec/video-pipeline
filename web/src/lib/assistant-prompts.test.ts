import assert from "node:assert/strict";
import { test } from "node:test";

import { isStyleGuideStub, pairAssistantPrompts } from "./gen-assistant-styles.ts";

test("a manual prompt may quote the style-guide phrase", () => {
  const manual =
    "A handmade poster of a fisherman. Notes: not example objects from the style guide. " +
    "Long composition with a boat, a net, evening light, and a quiet dock. ".repeat(8);
  assert.equal(isStyleGuideStub(manual), false);
  const stub =
    "приседания. Photorealistic magazine poster of this subject only, " +
    "not example objects from the style guide. Aspect ratio: 16:9.";
  assert.equal(isStyleGuideStub(stub), true);
});

test("ready prompts keep the draft id of their original slot", () => {
  const paired = pairAssistantPrompts(
    ["good one", "   ", "good two"],
    ["d0", "d1", "d2"],
    (text) => !text.trim(),
  );
  assert.deepEqual(
    paired.ready.map((item) => item.draftId),
    ["d0", "d2"],
  );
  assert.deepEqual(paired.unused, ["d1"]);
});
