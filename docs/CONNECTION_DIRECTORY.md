# Company connections and environment reports — contract 1.0.0

Acceptance criteria (2026-09-24, recorded before implementation):

1. A template editor can publish up to 100 company connection requirements, with a display name, service, required/enabled flags, an MCP server name or GitHub template ID, and a text setup guide. Updates use a base revision and an idempotent request ID. The public starter contains no company-specific catalog.
2. An authenticated installation reports a bounded snapshot of MCP connection names and normalized states. The server derives installation/owner from device authentication. Tokens, OAuth files, endpoints, environment, arguments, tool names/results and raw errors are never transmitted. Unknown fields and duplicate names are rejected.
3. Reports replace the previous snapshot atomically. Compare-and-swap revisions reject delayed writes; an identical latest request can recover a lost acknowledgement. A failed collector reports unavailable, not an empty successful inventory. Reports older than five minutes are stale.
4. Owners can inspect their environments; administrators can inspect environment connection metadata. Devices cannot inspect another installation. Views include required connections even when no account is registered, distinguish missing reports from missing connections, and include existing GitHub grant/binding status. Revoked installations are never shown as connected.
5. A managed Hermes plugin reports cached MCP runtime state at startup and every minute. It uses the pinned runtime's read-only status API and does not connect to services for observation. Its dedicated tool obtains current company guides and this installation's status through the authenticated companion bridge, including in Docker-managed sessions. The bridge exposes no arbitrary URL, command or credential access.
6. The packaged connection skill uses that tool to explain missing company connections and their setup steps. CLI users can retrieve the same catalog/status. Company text is data, not automatic shell execution or authorization. Existing owner config and credentials remain under their current storage rules.
7. Synthetic tests cover source/owner boundaries, stale and duplicate reports, concurrent catalog edits, secret-field rejection, report projection, the actual local HTTP bridge, packaged skill parity and the pinned plugin API. No real third-party authorization or Cloudflare resource changes are needed.

## Wire API

- `GET /v1/integrations`: enabled company catalog (`integrations`), bounded to 100.
- `GET /v1/admin/integrations`: editor catalog including disabled entries.
- `PUT /v1/admin/integrations/:integration_id`: `{schema_version:"1",request_id,base_revision,integration}`; zero creates, current revision updates.
- `GET /v1/connections/report`: current authenticated installation report revision.
- `POST /v1/connections/report`: `{schema_version:"1",report_id,base_revision,companion_version,collection_status:"ok"|"unavailable",connections:[{server_name,status}]}`. At most 100 names and 16,384 request bytes, with no owner/installation selector.
- `GET /v1/installations/:id/connections` and administrator `/v1/admin/installations/:id/connections`: company requirements with effective status, latest MCP snapshot and existing GitHub bindings. GitHub records use `?after=<connection UUID>` pagination, 100 per page. Company requirement summaries cover all bindings independently of the displayed page.
- `GET /v1/connections/directory-manifest`: public contract metadata describing fields, readers and freshness.

The latest MCP snapshot is retained per installation until replaced; there is no report history. Server receipt time establishes freshness, not device integrity. MCP connected means the runtime has a live session, not that every operation/scope will succeed. GitHub ready means a saved client-reported authorization test for the current grant; it is not a fresh remote probe.

Each integration's compact UTF-8 JSON is bounded to 16,000 bytes, including its guide. The full catalog is bounded to 100 entries so the client can receive all requirements within its 2 MB response limit. This aggregate byte bound and URL parsing are enforced by the public executable contract in addition to the JSON schema.

The guide tool also retrieves companion update advice; a failed release lookup leaves the connection guide available. Reporting failures do not block Hermes inference. The next scheduled report uses a new observation and revision; the client does not replay an old snapshot later as a fresh observation.
