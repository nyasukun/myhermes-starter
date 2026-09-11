# Public API contracts — companion/core 0.3.0

Public implementation: `@myhermes/core` 0.3.0 in `packages/core`. JSON wire schemas live in `schemas/`. Persona synchronization, templates/connections, skill packages, relay and monitoring each have explicit contracts. An incompatible wire change needs a new schema version; adding an allowlisted sync path or monitoring field is a public contract change, not a private configuration override.

[Applied revision status](SYNC_STATUS.md) adds device-only `POST /v1/sync/ack` and authenticated `GET /v1/sync/status-manifest`. The explicit client report and server receipt time are separate from upload receipt metadata and do not assert the current local filesystem state. The versioned public sync-status manifest declares the metadata collection and its readers.

All private content routes are relative to the configured HTTPS application origin. They return `Cache-Control: no-store`; there is no public object download route, arbitrary owner selector or admin content route. Browser content writes require a matching Origin and JSON content type. Standard error responses contain only an error code, never submitted body or credentials.

## Authentication and enrollment

Browser requests use the validated Cloudflare Access assertion header or, when the header is absent on an Access-bypassed device API path, the signed `CF_Authorization` cookie. Both paths check signature (RS256), issuer, application audience and expiry, then resolve an active application member by the verified subject. An invalid header is not ignored in favor of a cookie. Administrative membership does not permit impersonation for sync content.

The device generates a P-256 key in its OS secure credential store, never in CLI arguments or stdout. Public JWK contains only `kty`, `crv`, `x`, `y`; coordinates must use canonical unpadded base64url encoding of 32 bytes. Alternate unused padding-bit spellings are rejected. Supported proofs use existing JOSE standards:

1. `POST /v1/enrollments`: `{schema_version:"1",label,os:"ubuntu"|"macos",arch,client_version,public_jwk}`. Response includes `enrollment_id`, `user_code`, `verification_uri`, `expires_at`. Do not send private key material. The request expires after a short registration window.
2. Browser `GET /v1/enrollments/:id` shows label, OS, architecture, version, expiration and status. It never reveals the confirmation code.
3. Browser `POST /v1/enrollments/:id/approve`: `{user_code}`. The active authenticated browser member becomes owner. A body-supplied owner cannot override this.
4. Device `POST /v1/enrollments/:id/complete`: `{client_assertion}`. RFC 7523 JWT: ES256, `iss=sub=enrollment_id`, audience is the exact complete URL, fresh `jti`, `iat`, `exp` (maximum 60 seconds). The bound public key verifies proof of possession. Approved completion returns the stable `installation_id`; fresh assertions can recover a lost completion response within the enrollment lifetime.
5. `POST /v1/auth/token`: `{grant_type:"urn:ietf:params:oauth:grant-type:jwt-bearer",assertion}`. The RFC 7523 assertion uses `iss=sub=installation_id`, audience the exact token endpoint and fresh short-lived claims. Response `access_token`, `token_type:"DPoP"`, `expires_in`. The short-lived token is environment-specific and key-bound.
6. Device requests use `Authorization: DPoP <access_token>` and a `DPoP` RFC 9449 proof: ES256 public `jwk`, `typ:"dpop+jwt"`, `htm`, absolute `htu` without query/fragment, fresh `jti`/`iat`, and `ath` (base64url SHA-256 of the access token). Server validates replay, method, URI, key binding, expiration, active member and environment revocation.

Enrollment OS and JWK coordinates must be JSON strings; arrays are not converted. Enrollment records expire after 600 seconds and have an atomic 1,000-row capacity (`429 enrollment_capacity`). Proof identifiers remain for 360 seconds; capacity is 100,000 globally and 4,096 per proof namespace (`429 proof_capacity`). Every new enrollment or verified-proof admission sweeps at most 1,000 expired rows from each temporary table. Live replay IDs are never evicted, and an existing replay remains `401 proof_replay` at capacity. Cleanup is request driven; registered installations and revocations are retained separately. Clients preserve pending state and use bounded backoff with fresh proofs. These storage bounds do not replace deployment-level WAF/rate limits or collect IP-level tracking data.

An operator may explicitly close the application for recovery (`503 recovery_closed`); malformed gate configuration also fails closed. An optional external installation cutoff rejects older registrations/tokens/assertions (`401 installation_before_cutoff`) and older pending enrollments (`410 enrollment_before_cutoff`). A key already present on a retained pre-cutoff installation cannot be reused for another enrollment (`409 retired_installation_key`). A replacement environment must use a fresh key and owner approval; do not clear or silently retry an uncertain request under a new identity. These barriers do not reconstruct lost accounting or certify backup consistency.

An environment can request another short-lived token using its registered key. Key replacement uses a fresh key and installation ID with explicit owner approval; old-key revocation remains a separate portal operation. The companion's [same-home re-enrollment](IDENTITY_RECOVERY.md) preserves the original owner's persona/skill causal state, verifies the replacement owner before switching identity, and archives old signed monitoring and connection-binding state locally. A copied home still requires an independent environment; copying a state directory does not create one. Restore and repair never copy signing keys or connection credentials. See [COMPANION.md](COMPANION.md) and the actual native-store checks in [OS_CHECKS.md](OS_CHECKS.md).

## Identity and metadata

| Method and path | Result / action |
| --- | --- |
| GET `/v1/me` | `{person_id,role,installation_id}`; browser installation ID is null |
| GET `/v1/installations` | `{installations:[...],next_cursor}` for the authenticated person, 100 per page with `?after=<installation UUID>` |
| POST `/v1/installations/:id/revoke` | Revoke own environment |
| POST `/v1/admin/installations/:id/revoke` | Administrator revokes a registered environment |
| GET `/v1/admin/installations` | Same pagination; admin metadata projection only, no persona files, history or conflict body |

Installation metadata includes `installation_id`, `person_id`, label, OS, architecture, client version, created/last seen/revoked timestamps and sync revision/time. Label/OS/version are client-reported. Receipt timestamps and committed revisions are server-observed. Metadata does not attest the integrity of a device.

## Persona synchronization

`GET /v1/sync` returns:

```json
{"revision":0,"files":{}}
```

Once present, a file entry is `{content:string|null,revision,installation_id,updated_at}`. `null` is a retained tombstone, distinct from an absent path. Paths are exactly `SOUL.md`, `memories/MEMORY.md`, `memories/USER.md`. Default limits are 65,536 / 2,200 / 1,375 Unicode code points respectively. No arbitrary paths, unknown fields, invalid UTF-8 scalar strings, session databases or credentials. Skill packages use the separate [skill library contract](SKILL_PACKAGES.md); they are never added to the persona snapshot implicitly.

`POST /v1/sync`:

```json
{
  "schema_version":"1",
  "update_id":"6ccba594-d328-46c9-b8c7-d04889861544",
  "base_revision":0,
  "changes":[{"path":"SOUL.md","content":"A fictional assistant identity."}]
}
```

- Include one to three distinct allowlisted paths. `content:null` deletes.
- A path modified after `base_revision` conflicts, including a tombstone. Nonoverlapping file updates can be accepted without replacing other files. Future base revisions are invalid.
- Success: HTTP 200 `{status:"applied",revision,update_id}`. Conflict: HTTP 409 `{status:"conflict",revision,update_id,conflict_paths}`; both proposed and server content are retained in the person's history area.
- Retransmit the *identical* persisted update with the same `update_id`. The response receipt is idempotent. A different payload under an existing update ID is rejected. Never rebase an unacknowledged payload implicitly.
- Store local updates in a persistent outbox before sending; keep local modifications made after the queued snapshot. Do not advance the client revision or overwrite local content on an uncertain network result.
- The server writes immutable R2 content before publishing a revision. Revision publication and receipts are coordinated by the person's Durable Object; no last-writer whole-home overwrite.

`GET /v1/sync/history` returns `{history:[{revision,update_id,installation_id,created_at,paths}],next_cursor}`. `GET /v1/sync/history/:revision` returns that revision's snapshot. `GET /v1/sync/conflicts` returns metadata only: `{conflicts:[{update_id,base_revision,revision,conflict_paths,installation_id,created_at,resolved_by,resolved_revision}],next_cursor}`. The next page is `?before=<next_cursor>`; null means the end. No other query selectors are permitted. Each page contains at most 100 records.

`GET /v1/sync/conflicts/:update_id` returns the complete owner-only candidate including `changes` and `current` snapshot. This endpoint remains available for resolved conflicts and permits a previously offline environment to reconcile an owner's portal decision without downloading every conflict body.

To resolve, fetch the current snapshot, have the owner choose content, then submit a new update ID and current `base_revision`, plus `resolves_update_id` referring to the unresolved conflict. Resolution is subject to the same concurrency checks. The old conflict remains in history with `resolved_by`; it is not silently discarded.

## Failure handling

Treat 401/403 as authentication or authorization failure, not a retry loop. Enrollment-pending status waits for the user. A sync 409 requires explicit conflict resolution. On network failure preserve the outbox and retry with a fresh authentication proof but unchanged sync update. Transient storage errors must never be interpreted as an empty remote snapshot. The companion should not overwrite running-session files; use its coordinated launch/sync flow and upstream memory locks.

## Other implemented contracts

| Feature | Routes and detailed contract |
| --- | --- |
| Versioned templates and explicit multi-account connections | `/v1/templates`, `/v1/connections`, and per-installation binding routes. [M2_CONNECTION_CONTRACT.md](M2_CONNECTION_CONTRACT.md) defines exact template, notice, grant, metadata and binding shapes; [CONNECTIONS.md](CONNECTIONS.md) documents the fixed read-only GitHub connector. |
| Personal and company skill packages | `/v1/skills/personal`, `/v1/skills/company`, and browser editor company-draft/publication routes. Separate revisions, immutable package versions, conflict/history and recipient checks are in [SKILL_PACKAGES.md](SKILL_PACKAGES.md); causal outbox and activation behavior are in [SKILL_SYNC.md](SKILL_SYNC.md). |
| LLM relay | Device-only `/llm/v1/models` and `/llm/v1/chat/completions`, owner receipts and admin usage metadata. [RELAY.md](RELAY.md) defines model policy, SSE/tools/structured-output handling, idempotency, internal keyed fingerprints and unknown usage; [RELAY_BRIDGE.md](RELAY_BRIDGE.md) covers the local adapter used by the official runtime. |
| OTel and audit | Device-only `/v1/monitoring/audit` and `/v1/monitoring/traces`, published `/v1/monitoring/manifest`, owner/admin metadata views. [MONITORING.md](MONITORING.md) specifies signatures, fixed fields, source trust, storage, retention and retries. No conversation or tool-body endpoint is part of monitoring. |
| Member administration | Browser administrator metadata and explicit Access-subject binding. [MEMBER_ADMINISTRATION.md](MEMBER_ADMINISTRATION.md) defines the public validation contract. Admin rights do not authorize reading or using another person's content/accounts. |

Company-specific endpoints and UI consume the pinned public implementations. Runtime maintenance is local: installation, fixed-pin repair, allowlisted personality backup/restore and managed boundaries are documented in [COMPANION.md](COMPANION.md). Anomaly detection, automatic sanctions, profile splitting by account kind and arbitrary template shell execution are outside this implementation.
