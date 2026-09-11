# Managed session completion

`myhermes start` synchronizes personality and skills before launch and after the child process returns. The companion retains the managed session/state locks during finalization and uses the pinned Hermes memory-file locks while reading or applying personality. A second cooperating environment can retrieve the completed session's SOUL/memory changes after successful online finalization.

Before attempting a post-session request, the companion saves the latest allowlisted file snapshot and its causal revision as `session_checkpoint` in the owner-local `state.sqlite3`. With no older pending/conflicted update, the same transaction also queues its immutable sync update. With an existing update, its ID/payload remain unchanged; the checkpoint preserves the newer session intent separately. The existing exact-revision ACK and residual-change rules determine subsequent updates. A checkpoint never grants permission to overwrite a newly edited local file or silently rebase an unseen concurrent remote change.

Online boundaries drain pending updates and descendant local edits in bounded passes. Concurrent edits become retained conflicts. The checkpoint is cleared only when the durable apply leaves local files equal to the complete remote baseline with no pending/conflicted update. It contains private content, remains outside monitoring and Git, and is not a whole-home or session-database backup. `inspect.session_checkpoint_pending` reports its presence without printing its body.

`start --offline` skips sync network calls, uses the cached skill boundary rules and captures personality after the child returns. Successful durable queueing is a successful offline completion. Repeated offline sessions preserve the original pending payload and update the separate latest-session checkpoint. The local LLM bridge still enforces company relay authentication; offline mode does not enable an unapproved direct provider. Cached authorization/publication information cannot be claimed current.

Both persona and skill finalizers are attempted even when one fails or the child returns nonzero. Handled keyboard interrupts, SIGTERM and SIGHUP also attempt finalization after the child has stopped and been reaped. The managed launcher forwards SIGTERM/SIGHUP only to its own child and escalates after a fixed grace period; [COMPANION.md](COMPANION.md) describes the signal and process boundaries. SIGKILL, machine failure and an uncooperative external writer cannot be promised a finalization callback; the next normal sync scans the allowlisted local files and retains existing durable state. Do not delete state to clear a post-session error.

## Structured result and exits

The result separates `runtime_exit_code`, `persona_after_session`, `skills_after_session` and `post_session_exit_code`. Default output contains status/revision/error identifiers only. A runtime launch failure uses a fixed `runtime_error` projection and still attempts finalization.

| Condition | Companion exit |
| --- | --- |
| Child succeeded and both online finalizers succeeded | `0` |
| Explicit offline run; child succeeded and local checkpoint/skill boundary succeeded | `0` |
| Child returned nonzero | `3`; preserve the actual code in `runtime_exit_code` and report any finalizer error separately |
| Runtime was interrupted | `130`; report finalization outcomes separately |
| Child succeeded, but a finalizer failed | First failing finalizer's code, e.g. offline `4`, access denied `5`, conflict `6` |
| Launch failed before an exit result | The launch error's fixed exit code |

Applied-revision ACK delivery is separate from content synchronization. Only a complete clean apply queues a report, and a delivery failure remains a nonfatal `acknowledgement.status:deferred`; already synchronized content is not described as unsaved. `inspect.application_reports` shows the bundled public manifest, pending report revision and the latest validated receipt for the current installation. See [SYNC_STATUS.md](SYNC_STATUS.md).

## Executed acceptance

`tests/test_session_sync.py` drives the actual CLI start → child process → local authenticated relay bridge → synthetic HTTP provider path. It verifies cross-environment visibility after exit, repeated offline checkpoint preservation, immutable retries, runtime/nonfatal-report exit precedence, interrupts, concurrent remote conflict retention and independent skill finalization. Its child is a synthetic runtime fixture that writes the allowlisted files; it does not claim a live-account authorization or a new official-Hermes memory-tool acceptance. Separate pinned official-Hermes startup/discovery tests remain documented in [DISTRIBUTION.md](DISTRIBUTION.md).

`tests/test_sync_ack.py` verifies complete/empty apply eligibility, suppression for partial/pending/conflicted states, failed download/apply, SQLite restart after lost response, monotonic stale/future handling, newer-pending CAS preservation, per-installation receipt inspection and the packaged public manifest.
