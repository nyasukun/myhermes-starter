# MyHermes companion 0.3.0

M1 implements real enrollment, per-installation DPoP authentication, owner-scoped personality synchronization, a persistent offline outbox, revision history, retained conflicts, recovery, and a pinned official Hermes launcher. M2 adds versioned-template GitHub onboarding, environment-local native credentials, explicit multi-account reads and durable connection recovery; see [connection commands and acceptance tests](CONNECTIONS.md). Company/personal skill packages use complete-file validation, separate runtime names and recoverable activation; see [skill synchronization](SKILL_SYNC.md). `inspect` reports individual capabilities; connector and package availability do not imply that every later milestone is complete.

Managed terminal, code and ordinary file tools use the mandatory local Docker sandbox described in [SANDBOX.md](SANDBOX.md). Prepare Docker and the digest-pinned image before live start; host companion commands and native credentials remain outside Docker.

## Install and test without the private repository

The companion accepts Python >=3.11, including Python 3.14. The pinned Hermes runtime still requires Python 3.11–3.13, so install a supported interpreter alongside Python 3.14 and pass it to `install-runtime --python` and `upgrade --python`. A secure OS credential backend is required only for real enrollment/owner API calls. Unit tests use synthetic keys in memory and an explicit contract peer.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/ruff check src tests
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python -m compileall -q src tests
```

The Python runtime dependencies are pinned in `pyproject.toml`. No company configuration, private package or credential is required for these tests. The Worker integration runs separately in the application repository against actual local workerd/D1/R2/Durable Objects; the Python unit suite is not a claim of real cloud or real account verification.

The wheel includes the public monitoring manifest, runtime monitoring plugin, relay bridge and both Hermes authoring skills. After setup, `myhermes skills bootstrap` installs the reserved `myhermes-skills` and `myhermes-connections` directories into this Hermes home without a network call. Managed start checks the same assets before launching. `skills bootstrap --restore` first preserves modified reserved directories under `.myhermes-builtin-backups/ID/`; package updates never silently replace owner edits. See [DISTRIBUTION.md](DISTRIBUTION.md) for the installed-wheel verification and boundaries.

## Separate state and home for each environment

Choose sibling, independent local directories; never put these in Git or sync them via a file-sharing service. Repeat setup with new state/home paths on each additional runtime. `person_id` is returned by server authentication and remains the same across its owner's environments. An installation's private key is never copied to another environment.

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" setup \
  --server https://myhermes.example.org \
  --hermes-home "$HOME/.local/share/myhermes-main/home" \
  --upstream "$HOME/.local/share/myhermes-main/runtime" \
  --label 'Personal work environment' --dry-run

.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" setup \
  --server https://myhermes.example.org \
  --hermes-home "$HOME/.local/share/myhermes-main/home" \
  --upstream "$HOME/.local/share/myhermes-main/runtime" \
  --label 'Personal work environment'

.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" install-runtime --python python3.11
```

Replace the example origin with the approved company origin. For local development only, `--allow-local-http` permits `http://127.0.0.1:8787`, `localhost`, or IPv6 loopback. All other origins require HTTPS; credentials, paths, query strings and fragments in the configured origin are rejected. Redirects are refused so authenticated requests cannot follow to another origin.

`setup` creates a binding marker connecting one home to one state directory. Repeating the same setup is safe; a second state cannot reuse an existing bound home. This is environment state isolation. No company/client/personal account profile switching is introduced. A copied home must be restored into a newly registered environment using only the allowlisted personality content, not a copied state database, binding marker or installation key.

`install-runtime` clones the official public repository and checks out immutable commit `2237be355906fbe6065ce1815711eee52b2d646e` (`v2026.9.7`), creates an isolated venv, and installs the upstream CLI extra. The installer calls explicit argv arrays with no shell, refuses other commits or modified tracked upstream files, and never changes the company API or deploys anything. Repeating it repairs dependencies for this same supported pin. It is not an arbitrary upstream version selector. The actual macOS and Ubuntu runtime/native-store checks and their limits are recorded in [OS_CHECKS.md](OS_CHECKS.md). Ordinary installer tests use real local Git with a synthetic dependency-install boundary; the separately recorded Ubuntu check installed the real pinned upstream.

## Enroll in the owner's terminal

Run the following in your own terminal, **not in a Codex-captured terminal, tool or PTY**. A captured PTY can capture `/dev/tty`; bypassing stdout does not make such a surface private.

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" enroll
```

Enrollment generates an EC P-256 key in macOS Keychain or Ubuntu Secret Service, creates a short-lived request, opens the company's approval page, and displays a one-time code only on the owner's controlling terminal. The code is entered manually in the authenticated browser. Verify the environment metadata before approving. The browser launch URL contains only the public enrollment ID, so the code never enters a subprocess argument. JSON output contains enrollment status and installation ID only. `--no-browser` keeps the same terminal code flow and prints the public approval URL to that terminal.

Use `enroll --dry-run` safely from Codex to inspect prerequisites and the M1 transmission boundary. Real `enroll` requires a controlling terminal before it sends any enrollment request. An unfinished request is reused; after its ten-minute expiration, rerunning enrollment creates a fresh request. The pending code is kept in native secure storage and removed after successful completion. `--wait-seconds 0` performs one completion poll; rerun `enroll` after browser approval.

The exact native keyring backends are selected directly, never an environment-specified or plaintext fallback. Headless Ubuntu without an unlocked Secret Service fails with exit 3. macOS Keychain / Secret Service may require an OS prompt. Live keyring writes and real Access browser authorization have not been automated by the unit tests.

Device tokens use ES256 JWT bearer assertions (RFC 7523), expire after five minutes and are DPoP bound (RFC 9449). Every request uses a fresh signed method/URL/token-bound proof. Private keys stay in native secure storage, access tokens stay in memory, and request/response bodies and headers are never logged. The server checks membership and revocation on each request. Revocation stops new company API access; it cannot erase existing local content or prevent offline Hermes execution. After revocation, key loss or a recovery cutoff, use `re-enroll --dry-run` then `re-enroll` in the original owner's terminal to replace that installation identity while preserving its persona/skill outboxes. The replacement must have the same authenticated person; old monitoring and connection state are archived locally, with connection reauthorization required. See [same-home identity recovery](IDENTITY_RECOVERY.md) for resume, cancellation and explicit new-candidate procedures. Copied environments still require independent registration.

## Personality sync and private editing

Only these paths are accepted:

| Path | Character capacity |
| --- | ---: |
| `SOUL.md` | 65,536 (MyHermes transport policy) |
| `memories/MEMORY.md` | 2,200 (upstream default) |
| `memories/USER.md` | 1,375 (upstream default) |

Each UTF-8 file is additionally limited to 262,144 bytes. Content is preserved exactly, including upstream `\n§\n` separators; excess capacity fails explicitly. A custom upstream memory capacity is not automatically imported. This release conservatively uses the verified defaults. No memory text is merged by an LLM.

Symlinks in any path component, hardlinks, FIFOs, non-regular files, unpaired Unicode surrogates and unknown remote paths are rejected. `.env`, keys, OAuth stores, session databases, caches, logs, work documents and skills are outside M1's allowlist. There is no whole-home sync.

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" inspect
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" sync --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" sync
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" history
```

`inspect`, `history` and `conflicts` emit validated metadata only. Private content is written only through explicit file exports. `inspect` lists enrollment, sync and active monitoring field categories. Personality transfer goes to the separate owner API; audit/OTLP monitoring contains only the public metadata allowlist. Use `monitoring manifest` for the exact contract and `monitoring inspect` for local delivery state. Local config and home-binding files use bounded no-follow regular-file reads and typed schemas; corrupt metadata fails with fixed errors while preserving existing content and outboxes.

Managed start now checkpoints and synchronizes personality on session exit as well as before launch, including offline queueing and separate child/synchronization exit results. See [session completion and recovery semantics](SESSION_SYNC.md). The [applied-revision report](SYNC_STATUS.md) is a separate metadata-only acknowledgement, durably queued after complete local application and retried without changing synchronized content.

For content authored by the owner in a private local UTF-8 file:

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" edit \
  --path SOUL.md --source "$HOME/private-draft.md" --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" edit \
  --path SOUL.md --source "$HOME/private-draft.md"
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" sync
```

`edit --path memories/MEMORY.md --delete` records a local deletion; the following `sync` uploads a tombstone. No private content is accepted inline as a command argument. Codex skills must not read/export private contents into a conversation without the owner's specific request to review those contents.

Every changed batch gets a UUID and a durable SQLite outbox entry **before** its network request. The original payload and base revision are immutable across retries. If the connection fails after the server commits, the client resends the same update ID and retrieves the exact acknowledged history snapshot. Changes made after queuing, including an intentional revert to the old text or a new deletion, remain local for a subsequent update. Unsubmitted local edits that have not seen a concurrent remote value keep their old causal revision in a durable residual update; they cannot silently overwrite that unseen remote value. Residual outbox insertion, baseline publication and receipt completion commit together with the apply journal. A synchronization pass sends at most one queued batch.

The server merges only disjoint changed paths. Competing edits to the same SOUL or memory file create a retained conflict, including stale edits against a tombstone. An old device cannot silently resurrect a deleted file. Revision rollback is rejected. Local outbox and journal contain private content, live outside Git, and use owner-only directory/file permissions. They are local persistence, not an encrypted portable secret vault.

## Conflicts and history

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" conflicts
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" conflicts \
  --update-id REPLACE_WITH_CONFLICT_UUID --export-dir "$HOME/private-conflict-review"
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" resolve \
  REPLACE_WITH_CONFLICT_UUID --choice local --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" resolve \
  REPLACE_WITH_CONFLICT_UUID --choice local
```

The export creates mode-0600 baseline/local/remote/submitted JSON files. Open and compare them privately. `--choice local` resolves the original batch paths using the current local files, so the owner can edit those files to a deliberate merged version first. `--choice remote` selects current remote values for those paths. Both create a fresh revision with `resolves_update_id`; unrelated local edits remain intact. If the owner resolves in the portal, the original device checks its exact retained conflict receipt and catches up. Edits made locally after the original conflicting submission remain preserved with their original causal base; if they differ from the portal decision, the server retains a new explicit conflict rather than silently overwriting that decision.

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" history \
  --revision 1 --export-dir "$HOME/private-history"
```

History and conflict metadata pages return `next_cursor`; pass it to `history --before CURSOR` or `conflicts --before CURSOR` to inspect older records. Exact revision and conflict-ID exports do not depend on the first page. History exports are explicit local private artifacts. Revision history responses contain provenance from server records, including installation IDs; the client does not infer OS-specific settings or apply them as configuration.

## Managed startup, interruption recovery and upgrades

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" start --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" start
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" start --offline
```

Both online and offline managed starts require the local Docker preflight; `start --dry-run` reports the sandbox policy without running that check. See [sandbox preparation, files and boundaries](SANDBOX.md).

Real `start` must also be run in the owner's own terminal, not through a Codex-captured PTY: Hermes conversation I/O goes directly to that terminal. Normal startup completes personality and skill synchronization before launching the pinned runtime with the independent `HERMES_HOME`; skill changes imported during that conversation activate after it exits. An offline startup skips company synchronization and uses locally cached files. It cannot establish current company-publication authorization, and it does not bypass the company LLM relay when inference is requested.

During normal operation and handled termination, a managed home holds a session lock for the entire Hermes child lifetime. Another companion sync/edit/restore/start for that home fails while it is active. Skill import/delete/derive can record durable personal changes while the session runs; they defer runtime activation to the session boundary. Memory replacement additionally takes the exact upstream `MEMORY.md.lock` / `USER.md.lock` flock locks. Files are written to mode-0600 temporary files, fsynced, atomically replaced and parent directories fsynced. A durable apply journal recovers partial multi-file replacement before the next managed session starts. Multi-file application is not claimed to be a single filesystem transaction.

Handled-termination acceptance: SIGTERM/SIGHUP received while a managed child starts or runs must stop and reap that child before session finalization and lock release. The managed child has a 30-second grace period to let upstream Docker stop/remove and its 15-second cleanup drain complete. Repeated signals must not extend the initial grace period. A child ignoring the signal must receive bounded escalation targeting only that child PID; the wrapper must retain its handlers and lock until reaping finishes. Normal terminal I/O and KeyboardInterrupt behavior must remain intact. This does not promise cleanup after the parent receives uncatchable SIGKILL or control over unmanaged descendants.

The CLI temporarily forwards SIGTERM/SIGHUP to its managed child only, waits up to 10 seconds from the first signal, then kills that child PID if necessary and waits for the OS to reap it. It restores previous handlers after reaping and takes the existing cancelled-session path (exit 130), which still runs both session finalizers. It does not signal the terminal's process group or register a background service. After parent SIGKILL or other unhandled termination, an orphan runtime can remain outside the lock; stop it before resuming this home. Arbitrary background descendants also require separate owner management.

The macOS synthetic-process regression covers graceful SIGTERM/SIGHUP, repeated ignored signals with a shortened test grace period, and termination immediately before/after child creation. All three groups passed after failing five pre-fix scenarios. Each verifies child reaping, handler restoration and completed session checkpoint before another session can acquire the lock. The full local suite then reported 288 tests with 276 passed and 12 opt-in skips; the pinned-upstream runtime family (42) and official oneshot/skill-discovery/memory tests (3) passed separately with no skips. These are overlapping selections, not additional unique-test counts; current Ubuntu and other interpreter results are recorded separately in [OS_CHECKS.md](OS_CHECKS.md).

**Boundary:** upstream launched outside this wrapper, background gateways, and arbitrary editors do not honor the managed session lock. SOUL has no upstream cooperative writer lock. Stop unmanaged runtime writers before using this home. The wrapper cannot guarantee isolation from an uncooperative writer. Upstream can refresh memory during context compression; we do not claim it always reads personality only once at startup. See [upstream compatibility](UPSTREAM_COMPATIBILITY.md).

If an owner edit intervenes after a crash, recovery stops instead of overwriting it. Explicit recovery first stores both the current files and interrupted journal in a private backup:

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" recover --choice local --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" recover --choice local
```

`--choice local` preserves current files; `--choice target` completes the interrupted target. Both keep the backup ID for review and restore. `backup` saves the allowlisted personality locally without a network call. `backups` lists only backup IDs, dates, purpose, revisions and supported-pin metadata, with `--limit` and `--before` pagination. `upgrade` backs up the allowlisted personality and repairs the current pinned runtime; a future supported version requires a reviewed companion release. It neither copies secrets nor pretends to update to an unverified upstream HEAD.

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" backup --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" backup
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" upgrade --python python3.11
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" backups
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" restore REPLACE_WITH_BACKUP_UUID --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-main" restore REPLACE_WITH_BACKUP_UUID
```

Restore validates an owned, single-link regular backup below 4 MiB, saves the current personality as `safety_backup_id`, and applies the selected personality through the durable journal. It keeps the current server revision, baseline, pending updates and conflicts. Review and synchronize the deliberate restoration normally afterward. Outbox/history are not purged. A failed repair includes the backup ID in its sanitized error; after interruption, use `backups` to find it. If restore itself is interrupted, normal apply-journal recovery completes it and the safety backup remains listed.

These commands do not restore an older Hermes executable, recreate the exact transitive dependency environment, or archive owner configuration. The supported repair pin stays unchanged. Owner `config.yaml`, skills and session files remain in place; keys, dotenv files and the full home are never copied into a personality backup. A new supported upstream version or dependency rollback requires a reviewed companion release. A pending apply is recovered under memory locks before backup; an intervening owner edit requires explicit `recover` before repair proceeds.

Installer-interruption acceptance: SIGTERM, SIGHUP or SIGINT during a Git/venv/pip operation must stop that operation's newly created process group before the wrapper releases its locks. This includes build subprocesses remaining after the direct child exits. Remember signals received during child creation; repeated signals must not restart the grace period. Command timeout, creation failure and interruption must preserve private output suppression, restore signal handlers and retain the discoverable personality backup. Reject calls outside the main CLI thread before creating a child.

Each installer command runs without interactive input in its own process group, with stdout/stderr captured in memory. The command timeout remains 1,200 seconds. On an interrupt or timeout the wrapper forwards the signal to that group, allows at most 10 seconds from the first request, sends SIGKILL to remaining group members and ensures its direct child is reaped before releasing locks. No caller terminal group or unrelated process is signalled. An interrupted `upgrade` reports `runtime_command_interrupted` (exit 130) and its backup ID; a command timeout reports `runtime_command_timeout` (exit 3) with that ID. Use `backups`, then retry the same reviewed pin after checking the interruption's cause. Output and raw subprocess exceptions remain suppressed.

This follows the OS guarantees of group signalling and direct-child reaping; it does not reap orphaned grandchildren, constrain a subprocess that deliberately escapes its group, or guarantee cleanup after wrapper SIGKILL, power loss or uninterruptible kernel I/O. Stop any remaining unmanaged installer before retrying after those exceptional failures. Production does not depend on `ps`. The focused synthetic tests cover all three handled signals, repeated ignored signals, an early-exiting child with an ignoring grandchild, the command timeout, creation races, non-main-thread rejection and spawn failure; they do not run a real package installation.

Partial-venv repair acceptance: interruption after the interpreter exists but before pip is installed must recover on rerun without deleting/recreating the existing environment or changing owner files. Before bootstrapping, verify that the interpreter runs inside this exact `.venv` and uses the same supported major/minor version as the selected `--python`. A mismatched environment must stop before pip writes; an environment that already has pip must not be recreated or bootstrapped again.

Different homes may share a runtime checkout. The managed CLI now acquires a checkout lock after its identity, state and home locks: `install-runtime` and `upgrade` are exclusive writers; `start` is a shared reader for the complete managed session and its persona/skill finalization. Other managed readers may run concurrently, but a writer/read conflict returns `runtime_busy` (exit 7) before runtime work. A different checkout remains independent. Low-level Python runtime functions do not acquire this lock automatically; callers outside the CLI must hold `runtime_target_lock` for the same operation lifetime.

Persistent, empty lock files live below the checkout parent's private `.myhermes-runtime-locks` directory, so first installation does not create the destination before Git clone. The filename hashes the basename after Unicode NFC normalization and case folding; the actual parent directory supplies the namespace. This keeps macOS case/Unicode aliases and a not-yet-created checkout on the same lock. On a case-sensitive filesystem, distinct names differing only by that normalization conservatively share a lock. Existing parent permissions are preserved; unsafe symlinks, FIFOs, hard links, owner/mode mismatches and replaced lock inodes fail closed. Never remove or replace lock files while any managed command may run. Dry runs create no checkout lock and do not prove that the runtime is currently idle. Direct unmanaged pip/Git/Hermes processes and deliberate filesystem replacement do not cooperate with these locks; stop such processes before repair. Process locks are not a dependency rollback or power-loss recovery mechanism.

The installer checks these interpreter properties in isolated mode and runs its bundled `python -I -m ensurepip --default-pip` only when pip is absent, then resumes the normal pinned dependency installation. `ensurepip` itself uses no network. If this Python distribution lacks `ensurepip`, install its supported venv prerequisites and retry with the matching interpreter; the existing environment is preserved. A `runtime_venv_mismatch` requires inspecting the selected interpreter/environment rather than deleting files automatically. See Python's [ensurepip documentation](https://docs.python.org/3/library/ensurepip.html) and [venv identity rules](https://docs.python.org/3/library/venv.html#how-venvs-work). Regression tests use an actual `venv --without-pip` and bundled bootstrap against a synthetic local Git pin; the later external dependency-install step remains a fixture.

## Exit codes and verification boundary

| Exit | Meaning |
| --- | --- |
| 0 | Command succeeded, including a reported pending enrollment or dry run |
| 2 | Invalid input/schema/path/capacity or local state error |
| 3 | Setup/runtime/native secure store/interactive terminal unavailable |
| 4 | Connection unavailable; durable changes remain queued |
| 5 | Authentication, authorization or server rejection |
| 6 | Conflict, concurrent edit or explicit recovery required |
| 7 | Managed session or writer lock busy |
| 130 | Interrupted |

`--dry-run` performs no network request, changes no personality content and queues no update. Existing local state/lock infrastructure may be opened or initialized. Errors are stable JSON metadata; raw subprocess output and server bodies are suppressed. Logs are not a fallback diagnostic channel for secrets.

Automated acceptance covers two independent homes, conservative stale/disjoint changes, conflicting content, tombstones, offline restart, lost acknowledgements, post-queue reverts/deletions, explicit and portal resolution, crash journaling, malicious filesystem objects, metadata-only output, standard ES256 signatures and DPoP binding. macOS Python unit tests ran locally; Ubuntu, actual native credential-store authorization, the complete upstream install/start, live Cloudflare Access, production deployment and real external account grants must be reported separately and must not be inferred from local contract tests.

## Native Desktop startup

On macOS, `myhermes prepare-desktop` builds the managed native GUI once; `myhermes desktop` / `myhermes start --desktop` use the existing enrolled start/session synchronization boundary. Run from the owner's terminal and quit the application with Command+Q before waiting for the final sync result. Both the GUI and its backend must stop before that snapshot. The managed picker exposes only MyHermes/economy. See [DESKTOP.md](DESKTOP.md) for Node requirements, the pinned adapter copy, build verification, restrictions and recovery scope.
