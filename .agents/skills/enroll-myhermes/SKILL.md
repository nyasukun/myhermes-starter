---
name: enroll-myhermes
description: Prepare owner browser approval and register a MyHermes environment with native OS key storage.
---

# enroll-myhermes

Use when the user wants to enroll a configured MyHermes environment.

Inputs: the existing state directory and the approved company origin. Use the verified installed executable from setup (for example `.venv/bin/myhermes`) and the same explicit `--state-dir` in every command below. Never ask for a token, private key or browser cookie.

1. Run `myhermes --state-dir STATE inspect` and `myhermes --state-dir STATE enroll --dry-run`. Explain the displayed registration fields, owner-only personality-sync paths and active public metadata monitoring. Use `myhermes --state-dir STATE monitoring manifest` for the exact collection fields and exclusions.
2. Give the owner the exact `myhermes --state-dir STATE enroll` command to run in their own terminal. **Do not execute real enrollment using a Codex tool or captured PTY**: `/dev/tty` can be captured there. The one-time code belongs only in the owner's native terminal and authenticated company browser. No token or code should be pasted into Codex.
3. The owner verifies the environment metadata and approves. After their completion, run `inspect` to check the resulting registration metadata.

Failure/recovery: a missing or locked native secure store must be configured or unlocked by the owner; never fall back to plaintext. Pending initial enrollment is resumed by rerunning `enroll`; expired requests are replaced automatically.

For a lost installation key or revoked installation with the original home and state still intact, prepare `myhermes --state-dir STATE re-enroll --dry-run`, then have the owner run `myhermes --state-dir STATE re-enroll` in their own native terminal. They must approve as the same person. This preserves personality/skill outboxes and conflicts; connection credentials require new authorization, and the old installation's monitoring queue is archived locally. Resume the same command after interruption. Do not delete state, copy keys, or create another home merely to repair this installation. A different copied or newly created environment does require independent setup and enrollment. For explicit candidate cancellation or a later replacement, read `../../../docs/IDENTITY_RECOVERY.md` before using `--cancel` or `--new`.

Completion: initial enrollment returns `enrolled`, and `inspect` reports its installation ID. After same-home recovery, require the completed `re-enroll` result. `inspect.enrolled` and `enroll` returning `already_enrolled` only confirm stored registration metadata; they do not prove that a missing key or revoked installation works. A pending browser request or dry run is not enrollment completion. Owner interaction is necessary because it is the authentication/consent step, not because ordinary local inspection needs extra approval.
