import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { PATH_LIMITS, parseSyncUpdate } from "../packages/core/src/index.ts";
const schema = JSON.parse(readFileSync(new URL("../schemas/sync-update.v1.schema.json", import.meta.url)));
const options = schema.properties.changes.items.oneOf;
assert.deepEqual(Object.fromEntries(options.map(item => [item.properties.path.const, item.properties.content.maxLength])), PATH_LIMITS);
for (const [path, maximum] of Object.entries(PATH_LIMITS)) {
  const envelope = {schema_version: "1", update_id: "6ccba594-d328-46c9-b8c7-d04889861544", base_revision: 0, changes: [{path, content: "🙂".repeat(maximum)}]};
  assert.equal(parseSyncUpdate(envelope).changes[0].content.length, maximum * 2);
  assert.throws(() => parseSyncUpdate({...envelope, changes: [{path, content: "🙂".repeat(maximum + 1)}]}));
}
console.log("Public schema limits and Unicode contract match the runtime.");
