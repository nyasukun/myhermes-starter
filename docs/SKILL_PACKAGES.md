# Skill package contract — version 1

This is the public, company-independent contract. A package is a JSON file list, not an executable installer or an archive parser. Every entry represents a regular file; symlinks, hardlinks, device nodes and archive metadata have no representation. A producer must reject them before reading a directory. This preserves scripts, references and binary assets without extracting untrusted tar/zip entries.

The package envelope has `schema_version`, `skill_id`, `version`, `description`, `files`, `requires` and optional `derived_from`. IDs are lowercase ASCII slugs of at most 40 characters. Versions use three nonnegative decimal components. `files` holds sorted unique `{path,content_base64,sha256,executable}` entries. Paths are portable relative ASCII paths without hidden components, reserved Windows names, traversal, backslashes, case collisions or a file/directory collision. A root `SKILL.md` is mandatory and valid UTF-8. Its frontmatter is exactly two lines, `name: <skill_id>` followed by a single-line `description: <plain text or JSON-quoted text>`. Header lines use LF or CRLF; residual bare CR, NEL (U+0085), line separator (U+2028) and paragraph separator (U+2029) are rejected inside the two fields because YAML treats them as additional lines. Ordinary Unicode body text is preserved. Other upstream frontmatter, including credential and environment hooks, is rejected rather than silently stripped. At most 100 files, 512 KiB per file and 2 MiB decoded total are permitted. File SHA-256 and a SHA-256 of canonical envelope JSON identify the exact bytes. Base64 must use the canonical padded alphabet. Unknown fields are rejected.

JSON-quote descriptions containing newly introduced Unicode letters for interoperability with Python 3.11–3.13 and JavaScript engines that use different Unicode-category versions. Existing Japanese and other supported plain descriptions remain valid; no ASCII-only restriction is imposed.

Unquoted descriptions cannot contain YAML comments introduced by a space or TAB followed by `#`; quote the description when that text is intentional. This also prevents YAML from turning values such as `true` followed by a TAB/comment into a boolean that disappears from the pinned Hermes skill list. Ordinary TAB text, adjacent `#`, Japanese descriptions and JSON-quoted comments remain valid.

`requires` lists pinned connector IDs/versions and logical connection IDs. It never embeds credentials. Installation checks requirements locally and reports missing bindings; a requirement does not grant permission. `derived_from` records scope, skill ID, version and envelope SHA-256 when the owner explicitly creates a derivative.

The receiver verifies the entire package before writing any file, stages a complete directory and activates it only outside a managed Hermes session. Scripts are installed as data and are not run during validation or extraction. Package preview is plain text. File permissions help prevent accidental edits; they do not attest integrity against the machine owner.

## Personal library

All routes use verified owner identity, never a caller-supplied person selector. Ordinary administrator credentials only access the administrator's own library.

- `GET /v1/skills/personal`: current library revision and metadata entries, including deletion tombstones.
- `GET /v1/skills/personal/:skill_id`: current `{revision,package,sha256}`; package is null for a tombstone.
- `GET /v1/skills/personal/history`: revision metadata, paginated with `before`.
- `GET /v1/skills/personal/history/:revision`: the one immutable change at that library revision.
- `POST /v1/skills/personal`: `{schema_version:"1",update_id,base_revision,skill_id,package,resolves_update_id?}`. Package can be null to delete.
- `GET /v1/skills/personal/conflicts`: paginated candidate metadata; `GET .../conflicts/:update_id` includes both candidates.

The library revision is global within one owner. A skill changed after the caller's base conflicts; independent skills can merge. A version string cannot later name different bytes, even after deletion. A repeat update ID requires an identical payload and returns its original receipt. A conflict preserves the incoming full package and current version. Explicit resolution is a new update ID based on the current library revision and links to the original candidate. Tombstones and historical packages remain accessible until documented retention applies. An R2 write must succeed before publication; an R2 error cannot look like an empty package.

## Company library

The same immutable package and draft revision model applies to the company library. Only a browser member assigned the `skill` editor permission, or an administrator, can access drafts or publish. Published selections are separate from drafts: a draft edit never changes distribution automatically. Publication chooses an existing nondeleted revision and all active members, explicit active person IDs, or no recipients for an explicit withdrawal. Rollback is a new publication selecting a historical revision. Every publication is retained in history. Devices can retrieve only currently published packages addressed to their verified owner.

Company routes are `/v1/skills/company` (recipient list), `/v1/skills/company/:skill_id` (recipient package) and editor routes under `/v1/editor/skills/company` for draft changes/history/conflicts, plus `/:skill_id/publish` and `/:skill_id/publications`. Publication is `{publication_id,revision,audience:{kind:"all"}|{kind:"none"}|{kind:"people",person_ids:[...]}}`; publication ID provides idempotency. `kind:"none"` retains an auditable publication pointing at the withdrawn historical revision. Recipient list/get no longer return that package; later republication uses a new ID and an existing exact revision. This does not delete personal derivatives or remotely erase previously received copies. This additive audience option ships in the unreleased 0.3.0 module; older consumers must upgrade before using withdrawal.

Company and personal packages remain distinct stores. Derivation is explicit and copies a selected received company package into the owner's personal library. It never writes back to the company store. Runtime collision handling and exact companion commands are documented in [skill synchronization](SKILL_SYNC.md).

## Companion acceptance before implementation

These criteria were recorded before companion implementation; the implemented CLI and current verification boundary are documented in [skill synchronization](SKILL_SYNC.md). Acceptance tests demonstrate:

1. Explicit import validates a complete package, records a versioned personal working copy and durable outbox before networking, and projects all resources/scripts without executing them. Offline imports survive restart.
2. Personal and company packages with the same logical ID are simultaneously discoverable under `mh-personal-ID` and `mh-company-ID`; company packages are never sent to a personal API unless the owner explicitly derives them, and personal packages are never published to the company library.
3. Response loss retries an identical update ID and payload. Per-skill causal revisions preserve later local imports, concurrent remote changes, portal conflict decisions and tombstones. An immutable version cannot acquire different bytes. Conflict resolution and version choice are explicit.
4. Directory activation is journaled, staged and performed under the managed Hermes session lock. A crash between old-directory removal and new-directory activation can recover. Post-crash edits, symlinks, untracked destination collisions and direct edits to managed directories are preserved and reported rather than silently overwritten.
5. Company recipient changes remove an unchanged managed package at the next successful sync. Modified local company files remain available for explicit recovery/derivation, with normal managed start blocked until resolved. An offline start cannot claim that publication or membership is current.
6. Missing connector/connection requirements are reported and do not grant service access. Metadata/status/history output excludes package bodies; explicit exports write private artifacts. Ordinary read APIs cannot access another person's skill content.
7. A public Hermes authoring skill documents creation/update followed by the verified CLI import/derive operation. Upstream `skill_manage` directories not registered with the companion remain outside sync. No whole skills tree or Hermes home upload is inferred.
