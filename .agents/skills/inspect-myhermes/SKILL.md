---
name: inspect-myhermes
description: Inspect MyHermes enrollment, synchronization, conflicts and the actual enabled transmission fields without printing private content.
---

# inspect-myhermes

Use to diagnose a MyHermes environment or explain what it sends.

Input: state directory. Use only metadata commands by default.

1. Run `myhermes --state-dir STATE inspect` and `myhermes --state-dir STATE sync --dry-run`.
2. If enrolled and relevant to the requested diagnosis, run `history` or `conflicts`. Their structured output excludes personality candidates.
3. Explain the fields from `inspect.transmitted_fields`: enrollment metadata, allowlisted content through the separate owner-sync API, and public metadata-only audit/OTLP monitoring. Use `monitoring manifest` and `monitoring inspect` to examine the published schema and local queue; successful sync alone does not prove monitoring delivery. Relay accounting is a separate server-observed source.

Failure/recovery: preserve the outbox on exit 4; explain exit 6 and use metadata to locate the conflict. A recovery journal conflict requires the owner's deliberate `recover --choice local|target`; the command backs up both current content and journal before applying that choice. Never delete state to clear an error.

Completion: identify enrollment, revision, queued/conflicting state and enabled/unsupported capabilities from actual output. Do not export or print private content merely to improve a diagnostic report.
