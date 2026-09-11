---
name: enroll-myhermes
description: Prepare owner browser approval and register a MyHermes environment with native OS key storage.
---

# enroll-myhermes

Use when the user wants to enroll a configured MyHermes environment.

Inputs: the existing state directory and the approved company origin. Never ask for a token, private key or browser cookie.

1. Run `myhermes --state-dir STATE inspect` and `myhermes --state-dir STATE enroll --dry-run`. Explain the displayed registration fields, owner-only personality-sync paths and active public metadata monitoring. Use `myhermes --state-dir STATE monitoring manifest` for the exact collection fields and exclusions.
2. Give the owner the exact `myhermes --state-dir STATE enroll` command to run in their own terminal. **Do not execute real enrollment using a Codex tool or captured PTY**: `/dev/tty` can be captured there. The one-time code belongs only in the owner's native terminal and authenticated company browser. No token or code should be pasted into Codex.
3. The owner verifies the environment metadata and approves. After their completion, run `inspect` to check the resulting registration metadata.

Failure/recovery: a missing/unlocked native secure store must be configured by the owner; never fall back to plaintext. Pending enrollment is resumed by rerunning `enroll`; expired requests are replaced automatically. A lost installation key requires a new independent enrollment.

Completion: `inspect` reports enrolled with an installation ID. A pending browser request or dry run is not enrollment completion. Owner interaction is necessary because it is the authentication/consent step, not because ordinary local inspection needs extra approval.
