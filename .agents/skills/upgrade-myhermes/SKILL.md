---
name: upgrade-myhermes
description: Back up MyHermes personality, repair the supported pinned Hermes runtime, and verify recovery without selecting unverified versions.
---

# upgrade-myhermes

Use when the user requests a supported MyHermes runtime update/repair or rollback.

Inputs: state directory and Python 3.11–3.13 executable. In this MVP, `upgrade` reinstalls the same reviewed Hermes pin; moving to a new upstream version requires a reviewed companion release.

1. Read the supported commit and operational boundary in `../../../docs/COMPANION.md` relative to this skill directory. Run `inspect`, then `upgrade --python python3.11 --dry-run` with the state directory, using the verified installed CLI executable (for example `.venv/bin/myhermes` in its repository).
2. Run `myhermes --state-dir STATE upgrade --python python3.11` within the user's authorized local-update scope. It creates an allowlisted private personality backup before repairing the pinned runtime; record the returned backup ID without reading its contents. If repair fails or is interrupted, run `myhermes --state-dir STATE backups` to recover the metadata-only backup list; the error also includes the backup ID when available.
3. Run `myhermes --state-dir STATE start --dry-run` and `inspect`. Real Hermes startup belongs in the owner's own terminal; never stream their conversation through a Codex-captured PTY.

Failure/recovery: a modified/unsupported upstream checkout requires review, not force-reset. If the owner chooses to restore personality, run `restore BACKUP_UUID --dry-run` then `restore BACKUP_UUID`. Restore creates a separate `safety_backup_id` for the current personality before applying the selected backup. It preserves owner configuration and existing skills in place; it does not restore a different runtime version or copy dotenv/credentials. This does not roll the server revision backward; synchronize the deliberate local restoration normally. Never back up or copy credential stores/session DB as personality.

Completion: report the exact supported commit, backup ID, verification result and whether a live startup was actually performed. Do not claim that repairing the current pin upgraded to a newer Hermes release or completed Ubuntu/macOS operational verification.
