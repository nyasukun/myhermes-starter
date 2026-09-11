# Personal and company skill synchronization

Implemented against [public package/API contract](SKILL_PACKAGES.md), with 27 focused companion tests. Tests cover full package projection, offline imports, immutable retries, concurrent environments, portal decisions, tombstones, directory crash recovery, direct edits, explicit derivation, live-session queueing and bounded transport. A separate Private integration also passed against the real local Worker/D1/R2/Durable Objects, using generated ES256 enrollment keys, DPoP, two homes, large binary packages, large conflict candidates, company audience changes and independent GitHub bindings. Native Hermes discovery and real OS credential prompts remain separate checks; local integration is not a deployment or live-account claim.

## Authored packages and explicit import

The personal library is owner-scoped and shared across that person's environments. A company library contains administrator/editor-published selections addressed to the owner. Devices never publish company packages through these commands. Every synchronized package contains `SKILL.md` plus its references, scripts and binary assets, within the public path/type/hash/size allowlist. No archive extraction, script execution, automatic shell setup, environment hooks, whole-home scan or session DB synchronization occurs.

Author files in a private working directory. A skill ID is a lowercase slug of at most 40 characters. Root frontmatter has exactly two lines:

```markdown
---
name: example-skill
description: "A concise description of its task and trigger"
---

Skill instructions referring to references/ and scripts/ as needed.
```

Choose an explicit version and import:

```sh
myhermes --state-dir STATE skills import --source PRIVATE_DIRECTORY \
  --skill-id example-skill --version 1.0.0 \
  --description 'A concise description' --dry-run
myhermes --state-dir STATE skills import --source PRIVATE_DIRECTORY \
  --skill-id example-skill --version 1.0.0 \
  --description 'A concise description'
myhermes --state-dir STATE skills list
myhermes --state-dir STATE skills sync
```

Import copies and validates all package bytes, records its local working copy and immutable outbox before networking, and activates the managed projection when no managed session is active. A later change requires a new version; the server will never attach different bytes to a reused version string. Same-byte historical versions can be selected again without changing their identity. Importing can work offline. `skills sync --dry-run` reports metadata without networking, sending or activating packages.

Use `--connector github@1.0.0` and repeat `--connection UUID` for declared dependencies. These are pinned connector versions and logical account IDs, never tokens. Missing locally registered dependencies prevent activation while retaining the personal package and synchronization state. Local dependency checks do not replace current authorization: actual GitHub reads still refresh owner membership, grant and binding status before using native credentials.

The managed runtime directories and names are `HERMES_HOME/skills/mh-personal-ID` and `.../mh-company-ID`. Both versions of the same logical ID can coexist and be discovered. Only the frontmatter name is projected; original package hashes and projected installed-file hashes are recorded separately. All references and executable modes remain present. Package scripts are not executed during import, extraction or sync.

## Same conversation authoring

The bundled `hermes-skills/myhermes-skills/SKILL.md` teaches Hermes to author/update a private source directory and invoke the public import command. During a managed conversation, import/delete/derive acquire the skill state lock, copy the complete source package to durable state and return `activation: after_session`; they do not replace the active runtime directory. The launcher applies those queued changes after the child exits and before a later session starts. `skills list` reports `activation_pending`. This lets the owner and their Hermes manage skills in the same conversation without changing profiles or executing package installers.

Unregistered directories created directly by upstream `skill_manage` remain outside MyHermes synchronization. Explicit import is required. The companion does not silently invent version bumps from file timestamps. Direct changes to managed personal directories are preserved but block ordinary sync/start. Import the exact directory with `--projected`, its logical skill ID and a new explicit version to adopt them. If another edit happens after a deferred import, activation stops until the latest content is explicitly imported again.

Company files also remain available when directly changed, but distribution does not accept those changes. Derive a personal copy explicitly:

```sh
myhermes --state-dir STATE skills derive SOURCE_ID --from-scope company \
  --as PERSONAL_ID --version 1.0.0
```

This includes valid local modifications and records `derived_from` with the received original version/hash. It leaves the company library unchanged. `--from-scope personal` creates a distinct personal derivative. The target ID must be unused in local personal records; derivation refuses an existing ID instead of replacing that skill. A target first created on another environment remains protected by the normal server revision conflict check. Restoring the managed company tree after derivation preserves its modified files in a local backup:

```sh
myhermes --state-dir STATE skills restore SOURCE_ID --scope company
myhermes --state-dir STATE skills sync
```

## Revisions, deletions and conflicts

Each package has its own last-observed causal revision within the owner's global library revision. An ACK for another skill cannot silently advance an unsent skill's base. A request and payload remain immutable through offline/restart/response-loss retries. Later imports remain separate from the pending submitted package; an ACK commits its receipt, baseline, local selection and residual intent atomically through the directory journal. Independent skills can merge, while concurrent changes to one skill become retained full-package conflicts.

```sh
myhermes --state-dir STATE skills delete SKILL_ID
myhermes --state-dir STATE skills sync
myhermes --state-dir STATE skills history
myhermes --state-dir STATE skills history --revision REVISION --export-dir PRIVATE_DIRECTORY
myhermes --state-dir STATE skills conflicts
myhermes --state-dir STATE skills conflicts --update-id UPDATE_ID --export-dir PRIVATE_DIRECTORY
myhermes --state-dir STATE skills resolve UPDATE_ID --choice local --version NEW_VERSION
myhermes --state-dir STATE skills resolve UPDATE_ID --choice remote
```

Delete creates a revisioned tombstone for a known existing package; an offline older package cannot resurrect it silently. Deleting an unknown or already deleted package with no pending edit is a local no-op. A conflict resolution is a fresh update and an explicit selection. Local resolution of a nondeleted package requires an explicit version, so the CLI never guesses which version label the owner intends. If that label was already used for different bytes, choose another version and resolve again. A concurrent unrelated library commit can invalidate an offline resolution; its candidates remain available and can be explicitly resolved again without a permanently stuck pending request.

An explicit `skill_capacity` rejection or immutable-version rejection retains its original candidate as `rejected` and lets other queued skills and downloads continue. `skills rejected` lists safe metadata; `skills rejected --update-id UPDATE_ID --export-dir DIRECTORY` exports the complete retained candidate only to an explicit private directory. Importing again is an owner-selected new request, and deleting a never-admitted rejected import cancels it locally. Authentication failures, ordinary rate limits and uncertain network failures never receive this terminal classification. The initial library cap includes historical/tombstone IDs so the bounded complete listing can preserve deletion history; deleting a published skill does not free its ID slot.

Both online and offline managed boundaries recheck locally known requirements. Removing a local connection binding deactivates dependent cached packages and reports missing requirements. Modified runtime trees are preserved and block that automatic replacement until the owner imports, derives or restores them explicitly.

Portal resolution receipts are reconciled by exact update ID. If the portal wins against a pending local choice, the local selection/newer import remains a causal conflict instead of silently overwriting the portal decision at a new revision. A later local import during a lost resolution ACK is also preserved. Normal history/conflict/list output contains metadata only. Explicit revision/candidate exports write mode-0600 files and preserve existing directory permissions. Each history revision is one immutable skill change, not a whole-library snapshot. History/conflict lists paginate through `--before` using the returned cursor.

## Activation, withdrawal and recovery

Complete verified directories are staged under `.myhermes-skill-stage/OPERATION_ID`. A durable SQLite journal records the expected and target packages before runtime paths change. Replacement first moves the old directory to `.myhermes-skill-backups/OPERATION_ID`, then activates the staged directory, then commits the installed metadata, causal baseline, outbox receipts and any residual queued updates in one SQLite transaction. Managed-session and skill-state locks prevent cooperating processes from changing files during activation.

```sh
myhermes --state-dir STATE skills recover
myhermes --state-dir STATE skills recover --choice target
myhermes --state-dir STATE skills restore SKILL_ID --scope personal
```

Normal recovery recognizes the expected or fully activated target tree. Unexpected post-crash edits block automatic recovery. An explicit target choice first moves those trees to private backup directories outside runtime discovery, including malformed frontmatter, and then completes the recorded target. Returned `operation_id` and `preserved_id` identify both locations. Backups are not automatically removed in this MVP. `restore` similarly preserves current modified files before reinstating the recorded working/company package; it does not roll back server history. It checks the same connector/connection requirements as normal activation and keeps an unavailable package in private working state without putting it into the runtime directory.

On successful company sync, recipient metadata controls the current managed set. An unchanged package withdrawn from the recipient set is removed from runtime discovery and retained in local backup. Modified company files stop the managed operation and remain intact for derivation/restoration; normal managed startup stays blocked until the owner handles that drift. A publication change between list and fetch returns a retryable conflict rather than applying a mismatched version. Offline startup uses cached packages and cannot claim to have learned a new withdrawal. Company revocation cannot erase copies an authorized owner already possesses.

Unmanaged upstream gateways, direct Hermes launches and arbitrary editors do not cooperate with these locks. Stop such writers before using the same home. Directory rename and multi-package application are not presented as one filesystem transaction; durable journals recover the supported managed lifecycle. Symlink paths, hardlinks and special files are rejected rather than followed. Untracked destination-name collisions are preserved and reported.

## Test commands

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_skill_packages.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_skill_sync.py' -v
.venv/bin/ruff check src tests
```

Transport stays capped at 2,000,000 response bytes for other APIs, 3,000,000 for owner skill routes, and 6,000,000 for exact skill conflict details containing two candidates. The public decoded-content limit remains 2 MiB per package. The tests use synthetic account/library metadata and private temporary homes; no real user package or credential belongs in either Git repository.
