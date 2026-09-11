# M2 connection contract — version 1

This is the public source of truth for service templates, logical account connections, per-installation credential bindings and business-use grants. The first implemented connector is GitHub `1.0.0`, using a fine-grained PAT entered through a native terminal and stored only in the OS credential store. OAuth is not advertised without a registered client application. No template string is executed as a command.

## Acceptance criteria (defined before implementation)

| ID | Required behavior |
| --- | --- |
| M2-C01 | Only designated template editors or browser admins create/publish immutable template versions; normal members/devices cannot publish. |
| M2-C02 | Template validators reject unknown fields, arbitrary shell commands, private credentials, unrecognized connector/procedure IDs and non-GitHub hosts. |
| M2-C03 | A pinned published template is fetched with browser/device authentication; disabled/unpublished templates cannot create or activate connections. |
| M2-C04 | One person registers company, two client and two personal accounts without changing a global account/profile. |
| M2-C05 | Account kind, management, project and resource metadata remain independent; a personal account can be used for an explicitly granted client repository. |
| M2-C06 | Registration stores explicit accepted notice version/hash, server timestamp, operations and exact resources; personal account use does not add per-call approval. |
| M2-C07 | Connections and grants belong to the authenticated person. Other members/admins cannot get credential material or use another person's connection. |
| M2-C08 | A logical connection has independent installation bindings; another installation initially reports needs_auth; ready reports require matching provider account and current grant coverage. |
| M2-C09 | Duplicate request IDs replay the original result, changed payload reuse fails, and concurrent grant changes use an explicit revision check. |
| M2-C10 | Connection/binding/template/member/installation revocation prevents future registered API use; local downloaded credentials are not claimed remotely erased. |
| M2-C11 | Server records are strictly metadata-only and client-reported verification is labeled; PATs, test response bodies and errors never enter these schemas. |
| M2-C12 | Public parser tests and real local workerd/D1 tests exercise the normal flow, authorization failures, injection, replay, races and revocation. |

## Fixed GitHub template

All keys shown below are required; unknown keys are rejected. `description` and `usage_notice.text` are plaintext display content. They are never executable or a source of authentication endpoints.

```json
{
  "schema_version": "1",
  "template_id": "github-read",
  "version": "1.0.0",
  "display_name": "GitHub repository read access",
  "description": "Read repository metadata and issues using a selected account.",
  "connector_id": "github",
  "connector_version": "1.0.0",
  "service": {"base_url": "https://api.github.com", "api_version": "2026-03-10", "allowed_hosts": ["api.github.com"]},
  "auth": {"method": "github_fine_grained_pat", "required_permissions": ["metadata:read", "issues:read"]},
  "supported_os": ["ubuntu", "macos"],
  "input_fields": [{"name": "resources", "type": "github_repository_list", "required": true}],
  "setup": {"procedure_id": "github-fine-grained-pat-v1", "procedure_version": "1.0.0"},
  "connection_test": {"procedure_id": "github-read-test-v1", "required_capabilities": ["repository.read", "issues.read"]},
  "capabilities": ["repository.read", "issues.read"],
  "resource_rules": [{"owner": "*", "repository": "*"}],
  "related_skills": [],
  "usage_notice": {"version": "1", "text": "Registering this account expresses your intent to use the selected repositories for permitted business work. It does not authorize administrators or other users to read content or use this account."}
}
```

`template_id` and related `skill_id` are lowercase slug identifiers (1–64 characters). Versions are numeric SemVer `MAJOR.MINOR.PATCH`. Display name is at most 120 code points, description 2,000 and notice text 4,000. Notice version is a 1–32 character identifier. The only operations are `repository.read` and `issues.read`; required permissions are `metadata:read` and optionally `issues:read`, consistent with those operations. GitHub's header is `X-GitHub-Api-Version: 2026-03-10`. Unknown future connectors/procedures require a public contract/implementation update.

There are at most 50 resource rules. Each rule has a GitHub owner name or `*`, and repository name or `*`; these are allowlist matches, never regular expressions, shell globs or URLs. Actual connection/grant resources must name an exact owner/repository. At most 50 exact resources and 20 related skill references are permitted. `input_fields` is exactly the fixed resource-list field. `supported_os` is a unique nonempty subset of ubuntu/macos. Every published template version is immutable; republishing an older version changes the active pointer and provides rollback without rewriting history. Existing connections keep their pinned template version.

## Template and editor APIs

| Method / path | Contract |
| --- | --- |
| GET `/v1/templates` | `{templates:[{template_id,version,display_name,connector_id,connector_version,published_at}],next_cursor}`; active published heads, pages of100 using `?after=<template_id>` |
| GET `/v1/templates/:id/versions/:version` | `{template,sha256}` for a published version of an enabled template |
| GET `/v1/admin/templates` | Browser editor/admin list including draft versions and enabled/head state |
| GET `/v1/admin/templates/:id/versions/:version` | Browser editor/admin inspection of a draft or published version: `{template,sha256,status}` |
| PUT `/v1/admin/templates/:id/versions/:version` | `{schema_version:"1",request_id,template}`; create an immutable draft, return `{template_id,version,status:"draft",sha256}` |
| POST `/v1/admin/templates/:id/versions/:version/publish` | `{schema_version:"1",request_id}`; publish/move active head, return `{template_id,version,status:"published"}` |
| POST `/v1/admin/templates/:id/disable` | `{schema_version:"1",request_id}`; disable future template use, return `{template_id,enabled:false}`; publishing explicitly re-enables |
| GET `/v1/admin/content-editors` | Browser admin only; `{editors:[{person_id,kind}]}` |
| PUT `/v1/admin/content-editors/:person_id/:kind` | Browser admin only, `{enabled:boolean}`; kind `template` or `skill`, active member required |

Membership roles remain `member`/`admin`. Editor permissions are independent `content_editors(person_id,kind)` assignments, so granting template editing does not grant member administration or personal-content access. Devices cannot exercise editor privileges, even when their owner is an administrator. Publishing is an explicit browser action with CSRF protection.

## Logical connections and usage grants

`POST /v1/connections` takes:

```json
{
  "schema_version": "1",
  "request_id": "24a7bf0a-c81e-4e80-84cb-1d50de7b8970",
  "template_id": "github-read",
  "template_version": "1.0.0",
  "account_kind": "personal",
  "display_name": "Personal GitHub for Example Client",
  "account": {"provider_account_id": "12345", "login": "example-user"},
  "management": {"kind": "individual", "label": "Account owner"},
  "project": {"id": "client-example", "label": "Example client project"},
  "resources": [{"kind": "github_repository", "owner": "example-client", "repository": "project"}],
  "usage_grant": {
    "notice_version": "1",
    "accepted": true,
    "operations": ["repository.read", "issues.read"],
    "resources": [{"kind": "github_repository", "owner": "example-client", "repository": "project"}]
  }
}
```

`account_kind` is company/client/personal. `management.kind` is company/client/individual and is independent of account kind and project/resource ownership. `project` may be null. Provider account ID is a positive numeric string (max20 digits); login/owner use GitHub name validation. A provider account ID identifies a logical account; multiple connections to the same provider account are allowed when grants/projects differ. Display/management/project labels are at most120 characters; project ID is a slug. No person/owner/installation ID is accepted in this body.

The response is `{connection_id,grant_revision:1}`. The server resolves ownership from authentication, stores the exact versioned notice hash and server `accepted_at`, and labels provider metadata as client-reported. The accepted grant must be a subset of the template capabilities/resource rules and the connection's exact resources. Notice version must equal the pinned template notice. `accepted:false` does not create a grant. Initial resources/capabilities must be nonempty and unique. No credential is sent to this API.

| Method / path | Contract |
| --- | --- |
| GET `/v1/connections` | `{connections:[ConnectionMetadata],next_cursor}` for this person only, pages100 `?after=<connection_id>` |
| GET `/v1/connections/:id` | Exact owner. Device `{connection:ConnectionMetadata,usage_grant:Grant,bindings:[Binding]}` includes only its authenticated installation; browser includes first100 bindings and `bindings_next_cursor` |
| GET `/v1/connections/:id/bindings` | Owner browser `{bindings:[Binding],next_cursor}`, pages100 by `?after=<installation UUID>`; administrator equivalent uses `/v1/admin/connections/:id/bindings` |
| GET `/v1/connections/:id/grants` | `{grants:[Grant],next_cursor}`, newest100 with `?before=<revision>` |
| POST `/v1/connections/:id/grants` | `{schema_version:"1",request_id,base_revision,notice_version,accepted:true,operations,resources}`; append a new grant with CAS; `{connection_id,grant_revision}` |
| POST `/v1/connections/:id/revoke` | `{schema_version:"1",request_id}`; owner disables the connection |
| POST `/v1/admin/connections/:id/revoke` | Same body; browser admin may disable registered metadata without reading/using contents |
| GET `/v1/admin/connections` | Browser admin metadata projection, no grants' notice text, credential reference or account content |

`ConnectionMetadata` contains connection/person IDs, pinned template/connector version, account kind/display/account/management/project/resources metadata, created/updated/revoked timestamps, current `grant_revision`, `evidence_source:"client_reported"`, and `effective_status:"active"|"revoked"|"template_disabled"`. Metadata is not account content and does not attest server-side verification of GitHub. The server never receives a PAT, email, issue body or repository document.

[LIST_PAGINATION.md](LIST_PAGINATION.md) defines the bounded installation, editor and template-version inventories, cursor encodings and binding pages. Admin template lists contain only metadata; version bodies require an exact authorized GET. A partial page never means that an omitted environment, template or editor grant is absent. Existing revoked rows remain stored and discoverable.

`Grant` contains connection ID, revision, operations/resources, notice_version, notice_sha256, accepted_at and created_by_installation_id (null for browser). Scope changes append an immutable grant. They do not mutate another environment's local credential. A grant revision change makes prior ready reports stale until the environment rechecks its current granted resources/operations.

## Per-installation credential binding

The device registers a binding only after securely saving a credential and testing the selected account/resources. A ready request to `PUT /v1/connections/:id/binding` is:

```json
{
  "schema_version": "1",
  "request_id": "40d88ec1-ae6b-4650-8ead-3bed921fbb02",
  "grant_revision": 1,
  "status": "ready",
  "verified_account_id": "12345",
  "requested_permissions": ["metadata:read", "issues:read"],
  "tested_capabilities": ["repository.read", "issues.read"],
  "tested_resources": [{"kind": "github_repository", "owner": "example-client", "repository": "project"}],
  "error_code": null
}
```

Installation ID is derived from DPoP authentication; browser calls to this endpoint are rejected. `verified_account_id` must match the logical connection. For ready, tested capabilities/resources cover the current grant and requested permissions match the pinned template. GitHub fine-grained PATs cannot generally be introspected for their full granted permissions; `requested_permissions` is therefore a requested list, not a claim of OAuth-scope introspection. All verification evidence is labeled `client_reported` by the server.

For `needs_auth` or `error`, `verified_account_id` is null and tested capabilities/resources are empty. Fixed error codes are null, `auth_rejected`, `resource_forbidden`, `network_error`, `account_mismatch`, `credential_unavailable`; no exception or service response text is allowed. Response: `{connection_id,installation_id,status,grant_revision}`. `Binding` additionally includes updated_at, error_code, reported test metadata and effective status. Own installations with no binding are synthesized as `needs_auth`, with no copied credential.

Browser owner `POST /v1/connections/:id/bindings/:installation_id/revoke` takes `{schema_version:"1",request_id}` and revokes only that local binding. A revoked binding cannot be overwritten into ready; revoke and register a new installation to recover that binding in this initial contract. Logical connection revocation disables every binding. No remote API promises to delete a locally held PAT.

Each connector operation must select an explicit `connection_id` and exact repository, fetch/check current connection/grant/binding state, and use only the corresponding local credential. Do not change a global GitHub login, profile, environment variable or working credential between parallel tasks. Personal account classification alone does not add another approval step; ambiguity, scope expansion or future write operations require their own explicit handling.

## Idempotency, limits and authorization

Mutation request IDs are UUIDs scoped to the authenticated person, with the operation recorded in their receipt; a repeated identical request returns its saved response without a second connection/grant/version. Reusing the same ID for another payload or operation returns409. Grant updates require current `base_revision`; stale writes return409 without consuming a request ID. All JSON objects reject unknown fields. Mutations accept at most64 KiB of UTF-8 JSON. Owner/admin metadata lists are paginated and never return service response bodies.

The application admits at most 100,000 connection/template mutation receipts atomically. At capacity, previously accepted identical requests still return their original receipt and conflicting reuse remains rejected. A new mutation returns `429 connection_capacity` before any template, grant, binding or account change. This includes new logical-connection/binding revocations; independent browser installation/member revocation remains available to disable access. Receipts, historical grants and existing revocations are not purged to create room. Clients preserve their pending request ID and data; capacity exhaustion requires an operator-reviewed retention/migration decision, not automatic resubmission with new IDs or database deletion.

All M1 authentication, Origin/CSRF, token/key replay, membership and installation revocation rules continue to apply. An administrator cannot supply an owner selector to another person's connection. Admins may see the defined metadata and revoke registered access; admin role cannot produce a ready binding or fetch stored credentials because no such server credential store exists.
