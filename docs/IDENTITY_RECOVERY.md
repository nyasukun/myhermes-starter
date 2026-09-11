# Re-enroll the same local Hermes home

This explicit recovery operation replaces one installation identity after revocation, key loss or an operator recovery cutoff. It preserves the same owner's personality and skill state. It is not a backup, an account transfer or a way to reactivate a revoked key.

## Acceptance before implementation

1. `re-enroll --dry-run` reads only bounded metadata: no files, native keys or network requests are created. Real operations require the owner's controlling terminal and explicit confirmation.
2. A fresh candidate key stays in the native OS store. A persisted candidate survives interruption; retry does not silently choose another key or owner. Enrollment codes use the existing native-store and controlling-terminal flow.
3. Candidate completion and authenticated `/v1/me` must both identify the original `person_id`. A different owner stops before any personality/skill request, file replacement or old-state archival. Explicit cancellation retains the candidate metadata; a subsequent `--new` starts another candidate.
4. Personality and skill databases, causal outboxes, retained conflicts and runtime files remain untouched. The candidate receives no old content during enrollment or verification.
5. Switching the home binding and active config uses a persistent forward-recovery journal. Every intermediate crash resumes the same candidate. Other companion commands fail closed while recovery is pending.
6. Old connection state and signed monitoring queues are checkpointed, closed and moved with all known SQLite sidecars into a private local archive. They never become the new installation's queue/bindings. Native signing keys and service credentials are neither copied nor deleted.
7. A command-wide shared identity lock permits concurrent normal Hermes helper commands; recovery needs its exclusive lock. Runtime/companion/skill/connection locks must also be free. Unsafe links, unexpected archive collisions and malformed journal/config metadata stop without replacing user data.
8. Enrollment completion validates both server UUIDs before changing pending state and stores them in canonical lowercase. Legacy local UUID casing remains readable without renaming native-key entries. A case variant of the original installation is not a replacement.
9. Every live command, including connections and monitoring, verifies the home marker against the selected state directory before API dispatch. A retained recovery candidate is not a live state directory; use the original directory to resume or cancel it.
10. Repeating a completed operation returns its receipt. A fresh operation requires `--new`; `--cancel` is permitted only before local publication begins. Recovery never revokes an installation by itself.

## Owner procedure

Run these in your own terminal, never through a captured Codex PTY:

```sh
myhermes --state-dir "$HOME/.local/state/myhermes-main" re-enroll --dry-run
myhermes --state-dir "$HOME/.local/state/myhermes-main" re-enroll
```

Approve the candidate in the company portal while signed in as the original owner. Retry the same command after approval, network failure or interruption. A completed replacement requires reauthorization for each connection on this installation. Existing logical connections and skill references remain available; their old local credentials/binding requests are not reused.

If the candidate was approved by another account, stop and cancel it locally:

```sh
myhermes --state-dir "$HOME/.local/state/myhermes-main" re-enroll --cancel
myhermes --state-dir "$HOME/.local/state/myhermes-main" re-enroll --new
```

Cancellation does not revoke an already approved candidate. Use the appropriate owner's portal to revoke unused candidates and the original installation. After a successful replacement, a future intentional key replacement also uses `--new`.

The archive is below the selected state directory at `identity-recovery/<operation_id>/archive/`, mode 0700 with mode-0600 files. It holds old local connection/monitoring database state; `original/` and `candidate/` beside it retain bounded installation metadata. Signing keys and PATs remain in their original native-store entries. Do not import archived signed batches into another installation or copy archived binding requests into its live database. No automatic archive purge is supplied. A normal command on an existing configured environment creates only the mode-0600 `identity.lock` if it is absent; dry runs do not create it.

## Limits

The identity lock coordinates current companion CLI processes. Stop older companion versions and unmanaged processes before recovery; it cannot quiesce arbitrary external SQLite writers. This operation preserves the local causal state but cannot repair missing server revisions, R2 objects or accounting history after an inconsistent server restore. Normal synchronization after replacement still performs existing conflict/revision checks. The server's authenticated person identity must remain stable across the recovery.

## Local evidence

The macOS Python 3.11 recovery suite exercises 13 synthetic groups, including all five archive/config/marker/receipt crash boundaries, another-owner refusal and explicit restart, pending-candidate reuse, missing-code cancellation, old signed-queue preservation, unsafe links and a live uncheckpointable SQLite writer. An independent reviewer ran the first 12 groups successfully. Five additional identity-validation groups reject untrusted enrollment IDs without poisoning pending state, normalize server UUIDs, allow cancellation/resume from legacy uppercase-owner state, reject a case variant of the original installation, and prevent candidate-directory API/monitoring dispatch. The Private `integration/identity-recovery.mjs` additionally runs the real Python CLI against local workerd: actual enrollment and device revocation, another-owner approval without any owner-content request, new-key same-owner recovery without loading the old key, and replay of the original persona and personal-skill lost-ACK receipts. Run it from the Private repository with `node integration/identity-recovery.mjs --starter /absolute/myhermes-starter --python /absolute/myhermes-starter/.venv/bin/python`. Both paths are explicit; this cross-repository test is excluded from the Private-only Node CI. Native credentials in that test are an in-memory fixture; this is not a new real-Keychain or Ubuntu recovery test.
