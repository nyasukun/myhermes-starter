# Applied personality revision reports

`POST /v1/sync/ack` accepts installation-bound DPoP authentication and strict JSON of at most 1024 bytes:

```json
{"schema_version":"1","revision":3}
```

Revision is a nonnegative safe integer, including zero for an applied empty initial snapshot. The companion enqueues this only after atomic journal application leaves the exact full remote baseline in local files, the baseline is durably committed, and no pending or conflicted content updates remain. A partial application that preserves residual local edits does **not** advance the report, even if its internal baseline revision advances. Downloading a snapshot, accepting an upload or starting an application is insufficient. Reports describe the revision applied at that point; later local edits, conflicts or a running Hermes session can differ. An already queued report may retry as historical evidence after later changes. Explicit sync success/failure/conflict outcomes use the separate published audit/OTLP activity contract; this latest-only ACK is not a current clean-state signal or a record of every attempt.

The authenticated installation and owner are implicit. Unknown fields, contents, client time, identifiers or owner selectors are rejected. Browser sessions, including administrators, cannot submit reports. The owner's current server revision must be at least the submitted revision. This checks plausibility, not whether the device's assertion is true.

A first or advancing report returns HTTP 200:

```json
{"status":"accepted","source":"client_reported","applied_revision":3,"received_at":"2026-09-11T03:00:00.000Z"}
```

The revision is the idempotency key; no request UUID or unbounded receipt log is needed. An equal retry returns `status:"duplicate"` with the original timestamp. A lower report returns HTTP 409 with `{error:"sync_ack_regression",source:"client_reported",applied_revision,received_at}` describing the retained higher report. It does not change the report, timestamp or local content. A future revision returns HTTP 409 `{error:"sync_ack_future_revision"}`. Malformed payloads return 400, browsers 403 `device_required`; ordinary authentication, revocation, recovery and service errors apply. Lost acknowledgements may retry the same revision. A stale report must not be retried indefinitely or cause the client to adopt a server value without applying its content.

`GET /v1/installations` and the metadata-only administrator equivalent retain legacy `sync_revision`/`last_sync_at`, and add an explicit `sync_status`:

```json
{"server_observed":{"source":"server_observed","accepted_revision":2,"received_at":"2026-09-11T02:00:00.000Z"},"client_reported":{"source":"client_reported","applied_revision":3,"received_at":"2026-09-11T03:00:00.000Z"}}
```

The legacy revision is the highest successfully accepted upload revision from that environment. Its timestamp is the latest successful upload receipt observation, including a retry of an older upload, and is not necessarily that highest revision's original commit time. A browser upload has no installation. These fields never assert local application. `accepted_revision` is `null` when no upload was observed; legacy `sync_revision` remains zero for compatibility. The application revision and timestamp are both `null` before any ACK. Seeing revision zero with a timestamp means an explicit initial application report. The existing `last_seen_at` is a separate server observation of successful device API authentication, including requests whose later operation fails; it is neither a heartbeat nor an application acknowledgement. Admin APIs still cannot retrieve or select another person's content.

The separately versioned `SYNC_STATUS_MANIFEST` in `packages/core/src/sync-status.ts` and [manifest JSON](../monitoring/sync-status-manifest.v1.json) declare this collection, storage and readers. Authenticated `GET /v1/sync/status-manifest` returns the exact public manifest. It is operational sync metadata, kept separately from persona objects and audit/OTLP event storage. Only the latest revision and its server receipt time per installation are retained with the existing installation metadata; no history of ACK payloads, IP addresses, client timestamps or bodies is collected. There is no automatic expiry; retaining installation and revocation history prevents key-state reuse. This does not change audit/OTLP manifest version 1.0.0 or imply administrator permission to inspect the local filesystem.
