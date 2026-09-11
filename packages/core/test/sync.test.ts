import {test} from "node:test";
import assert from "node:assert/strict";
import {parseSyncUpdate, applyChanges, conflictPaths, canonicalJSON} from "../src/index.ts";
const update = (changes: unknown, base_revision = 0) => parseSyncUpdate({schema_version: "1", update_id: crypto.randomUUID(), base_revision, changes});
test("allowlist and upstream capacities reject whole-home upload and unknown fields", () => {
  for (const path of [".env", "../SOUL.md", "state.db", "skills/a/SKILL.md"]) assert.throws(() => update([{path, content: "secret"}]));
  assert.throws(() => update([{path: "memories/MEMORY.md", content: "x".repeat(2201)}]));
  assert.throws(() => update([{path: "SOUL.md", content: "\ud800"}]));
  assert.doesNotThrow(() => update([{path: "memories/USER.md", content: "🌸".repeat(1375)}]));
  assert.throws(() => parseSyncUpdate({...update([{path: "SOUL.md", content: "a"}]), person_id: "other"}));
});
test("disjoint paths merge; tombstones prevent stale resurrection", () => {
  const initial = applyChanges({revision: 0, files: {}}, update([{path: "SOUL.md", content: "a"}]), "first", "now");
  const merged = applyChanges(initial, update([{path: "memories/USER.md", content: "b"}]), "second", "then");
  assert.equal(merged.files["SOUL.md"]?.content, "a");
  const deleted = applyChanges(merged, update([{path: "SOUL.md", content: null}], 2), "first", "later");
  assert.deepEqual(conflictPaths(deleted, update([{path: "SOUL.md", content: "old"}], 1)), ["SOUL.md"]);
  assert.equal(deleted.files["SOUL.md"]?.content, null);
});
test("canonical digest is independent of JSON property insertion order", () => {
  assert.equal(canonicalJSON({b: 2, a: {y: 2, x: 1}}), canonicalJSON({a: {x: 1, y: 2}, b: 2}));
});
