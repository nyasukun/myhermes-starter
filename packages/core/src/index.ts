/** Public M1 contract. Deliberately has no organization-specific settings. */
export * from "./skills.ts";
export const CONTRACT_VERSION = "1";
export const PATH_LIMITS = {"SOUL.md": 65536, "memories/MEMORY.md": 2200, "memories/USER.md": 1375} as const;
export type SyncPath = keyof typeof PATH_LIMITS;
export type Change = {path: SyncPath; content: string | null};
export type SyncUpdate = {schema_version: "1"; update_id: string; base_revision: number; changes: Change[]; resolves_update_id?: string};
export type FileVersion = {content: string | null; revision: number; installation_id: string | null; updated_at: string};
export type Snapshot = {revision: number; files: Partial<Record<SyncPath, FileVersion>>};
export type SyncResult = {status: "applied" | "conflict"; revision: number; update_id: string; conflict_paths?: SyncPath[]};
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export class ContractError extends Error { constructor(message: string) {super(message); this.name = "ContractError";} }
export function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ContractError("object_required");
  return value as Record<string, unknown>;
}
export function exact(value: unknown, allowed: string[], required = allowed): Record<string, unknown> {
  const obj = record(value);
  if (Object.keys(obj).some(k => !allowed.includes(k)) || required.some(k => !(k in obj))) throw new ContractError("invalid_fields");
  return obj;
}
export function boundedString(value: unknown, max: number, min = 1): string {
  if (typeof value !== "string" || [...value].length < min || [...value].length > max || value.includes("\u0000") || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) throw new ContractError("invalid_string");
  return value;
}
export function parseSyncUpdate(value: unknown): SyncUpdate {
  const obj = exact(value, ["schema_version", "update_id", "base_revision", "changes", "resolves_update_id"], ["schema_version", "update_id", "base_revision", "changes"]);
  if (obj.schema_version !== "1" || typeof obj.update_id !== "string" || !UUID.test(obj.update_id) || !Number.isSafeInteger(obj.base_revision) || Number(obj.base_revision) < 0) throw new ContractError("invalid_update");
  if (obj.resolves_update_id !== undefined && (typeof obj.resolves_update_id !== "string" || !UUID.test(obj.resolves_update_id))) throw new ContractError("invalid_resolution");
  if (!Array.isArray(obj.changes) || obj.changes.length < 1 || obj.changes.length > 3) throw new ContractError("invalid_changes");
  const seen = new Set<string>();
  for (const item of obj.changes) {
    const change = exact(item, ["path", "content"]);
    if (typeof change.path !== "string" || !Object.hasOwn(PATH_LIMITS, change.path) || seen.has(change.path)) throw new ContractError("invalid_path");
    seen.add(change.path);
    if (change.content !== null) boundedString(change.content, PATH_LIMITS[change.path as SyncPath], 0);
  }
  return obj as SyncUpdate;
}
export function conflictPaths(snapshot: Snapshot, update: SyncUpdate): SyncPath[] {
  if (update.base_revision > snapshot.revision) throw new ContractError("future_revision");
  return update.changes.filter(c => (snapshot.files[c.path]?.revision ?? 0) > update.base_revision).map(c => c.path);
}
export function applyChanges(snapshot: Snapshot, update: SyncUpdate, installation_id: string | null, updated_at: string): Snapshot {
  if (conflictPaths(snapshot, update).length) throw new ContractError("conflict");
  const revision = snapshot.revision + 1;
  const files = {...snapshot.files};
  for (const change of update.changes) files[change.path] = {content: change.content, revision, installation_id, updated_at};
  return {revision, files};
}
export function canonicalJSON(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonicalJSON).join(",") + "]";
  return "{" + Object.keys(value).sort().map(k => JSON.stringify(k) + ":" + canonicalJSON((value as Record<string, unknown>)[k])).join(",") + "}";
}
export * from './connections.ts';
export * from './relay.ts';
export * from './relay-ledger.ts';
export * from './relay-forward.ts';

export * from './monitoring.ts';
export * from './monitoring-otlp.ts';
export * from './monitoring-audit.ts';
export * from './monitoring-store.ts';

export * from './members.ts';
export * from './relay-reconcile.ts';
export * from './sync-status.ts';
export * from './pagination.ts';
