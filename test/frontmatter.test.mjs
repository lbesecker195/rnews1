import assert from "node:assert/strict";
import test from "node:test";
import {
  collectTranslatableStrings,
  splitFrontMatter,
  stringifyFrontMatter,
} from "../src/frontmatter.mjs";

test("quotes description values that contain colons and inner quotes", () => {
  const raw = `---
title: Chipotle prank: influencer pleads
description: He said: do it "now" and left
date: 2026-08-29 18:11:00-07:00
draft: false
tags:
- Chipotle
---

# Hello

Body text.
`;
  const parsed = splitFrontMatter(raw);
  assert.equal(parsed.data.title, "Chipotle prank: influencer pleads");
  assert.match(parsed.data.description, /now/);
  const out = stringifyFrontMatter(parsed.data, parsed.body);
  assert.match(out, /^---\n/m);
  assert.match(out, /title: "Chipotle prank: influencer pleads"/);
  assert.match(out, /description: "He said: do it \\"now\\" and left"/);
  assert.match(out, /date: 2026-08-29 18:11:00-07:00/);
  assert.match(out, /draft: false/);
  assert.match(out, /# Hello/);
});

test("round-trips already quoted English articles", () => {
  const raw = `---
categories:
- Business
date: 2026-08-29 18:11:00-07:00
description: "Heston said \\"clean house\\" then left."
draft: false
translationKey: "chipotle-prank-influencer-pleads-guilty"
title: "Chipotle prank influencer pleads guilty"
---

# Chipotle

Prose.
`;
  const parsed = splitFrontMatter(raw);
  const out = stringifyFrontMatter(parsed.data, parsed.body);
  const again = splitFrontMatter(out);
  assert.equal(again.data.translationKey, "chipotle-prank-influencer-pleads-guilty");
  assert.equal(again.data.title, "Chipotle prank influencer pleads guilty");
  assert.match(out, /date: 2026-08-29 18:11:00-07:00/);
});

test("collects only prose front-matter fields", () => {
  const strings = collectTranslatableStrings({
    title: "Hello",
    description: "World",
    translationKey: "hello",
    draft: false,
  });
  assert.deepEqual(strings, { title: "Hello", description: "World" });
});
