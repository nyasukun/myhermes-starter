# M4 operating-system acceptance

The checks below distinguish actual OS execution, native credential-store access, and fixture tests. They use public source and synthetic data only. A test against a contract peer does not establish production enrollment, Cloudflare deployment or external-account authorization.

## Acceptance criteria, recorded before execution

| ID | Required behavior |
| --- | --- |
| O01 | Record the actual host/guest OS, architecture, Python version and execution method. Do not infer Ubuntu support from a macOS unit test or a CI matrix. |
| O02 | Run the public companion suite on an already available local Ubuntu environment, with no private repository mounted/copied and no privileged container mount. If unavailable, state the concrete blocker. |
| O03 | On macOS, create a new random synthetic installation key through the exact native backend, read and verify that key, delete it, and verify removal. Never enumerate or read existing credentials. Locked/denied Keychain access must fail without plaintext fallback. |
| O04 | On Ubuntu, separately identify real Secret Service availability. In-memory keyring fixtures and a headless failure test must not be reported as a successful native Secret Service round trip. |
| O05 | Runtime install/repair checks use the fixed official origin and commit, reject unrelated/modified source, and preserve owner data and pre-existing directory permissions. No shell from templates or arbitrary remote is executed. |
| O06 | Companion installation uses an isolated Python environment and preserves user state. Reinstallation/repair must be explicit, and interrupted setup must have a documented recovery path. |
| O07 | Upgrade/restore/recovery preserve the allowlisted personality backup and durable outboxes; they never copy a session DB, `.env`, connection credential or installation signing key. |
| O08 | Commands and test output contain no credential values. OS-native checks are explicit opt-ins and are skipped in ordinary unit/CI runs. |
| O09 | Actual pinned Hermes must execute synthetic `memory` calls for MEMORY/USER, exit through managed start, and synchronize exact files to an independent environment. No synthetic child may substitute for official tool dispatch/storage. |
| O10 | The managed runtime must suppress inline-shell execution during skill preview and preserve the owner's environment hint while adding process-only current-installation guidance. The official prompt builder must retain remote-terminal authority; see the preimplementation acceptance in [UPSTREAM_COMPATIBILITY.md](UPSTREAM_COMPATIBILITY.md). |
| O11 | The pinned official resolver must route all 21 verified core/shipped-plugin auxiliary task slots and the side-question fork through the managed provider without network. Strict skill headers must reject hidden YAML fields and TAB-comment type changes before activation, while accepted descriptions remain discoverable by the actual upstream. |

## Environment inventory

Verified on 2026-09-11. The host reports macOS26.6.2/Darwin25.6.0, arm64; companion checks use Python3.11.15. Docker29.5.2 is available through an existing local Colima daemon. Actual Ubuntu tests ran as a new unprivileged UID10001 in a disposable Ubuntu24.04.4 LTS/aarch64 container with Python3.12.3. The official image is pinned at `ubuntu@sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254`.

The initial container was verified `privileged=false`, with zero host mounts and zero published ports. Subsequent runs use the same unprivileged/no-mount/no-published-port script. Only explicitly listed Public source directories/files and public monitoring manifests were archived and copied in; no private repo, host home, `.env`, native credential store or real user data was mounted/copied. Python, Git, D-Bus and GNOME Keyring packages were installed inside the disposable container only. The existing VM was reused; no new VM or VM tool was installed. Disposable acceptance containers were removed after testing; the latest successful container's absence was also checked through Docker. The public Ubuntu base-image cache remains in local Docker.

## Native credential checks

Native checks operate on one generated acceptance-test account only. They do not list Keychain/Secret Service entries or reuse a real installation identifier. A successful check includes deletion and a missing-value readback. If an OS prompt or locked store prevents completion, the result is recorded as blocked and no fallback file is written.

Actual results:

| OS/path | Result | Limit |
| --- | --- | --- |
| macOS native Keychain, exact `SecureKeyStore` backend | Passed synthetic EC-key creation, readback, matching public key, deletion, and missing-value readback | Existing real credentials were not queried; a locked/denied real Keychain was not forced or tested |
| Ubuntu GNOME Keyring Secret Service over a real private D-Bus session | Passed the same create/read/delete check | New synthetic container keyring; not a deployed employee desktop session |
| Ubuntu missing D-Bus socket | Passed fail-closed check: `secure_store_unavailable`, no plaintext fallback | Does not simulate every desktop prompt/lock failure |

The temporary Ubuntu keyring password was generated in memory and passed directly through a pipe to `gnome-keyring-daemon --unlock`. It was not placed in arguments, output, repository files or host keychains.

## Executed acceptance

| Check | Actual result |
| --- | --- |
| Initial Ubuntu container snapshot |180 Python tests ran, with4 intentional skips (2 native opt-ins and2 explicit-upstream probes), no failures; Ruff passed |
| Earlier Ubuntu session exit/ACK/identity snapshot |246 Python tests ran:239 passed and7 explicit opt-in skips, no failures; Ruff passed |
| Latest Ubuntu source snapshot, including identity validation, telemetry stream rotation, causal skill resolution and environment hints |268 Python tests ran:259 passed and9 explicit opt-in skips, no failures; Ruff passed |
| Ubuntu explicit native checks |2 passed separately, including missing-bus failure |
| Installer recovery tests using real local Git fixtures |8 passed on macOS and8 on Ubuntu; dependency install/failure is a synthetic command boundary in these tests |
| Actual Ubuntu official Hermes installation |Git clone, pinned checkout, Python3.12 venv and official `.[cli]` dependency install completed successfully through `install_runtime` |
| Latest actual Ubuntu pinned config/provider/plugin/inline-shell probes |11 runtime tests ran and passed with `MYHERMES_TEST_UPSTREAM` set; no skips |
| Latest actual Ubuntu environment-hint probes |4 tests ran and passed; exact owner-hint precedence, process-only lifetime, config preservation and actual official prompt building with local/simulated-remote context; no skips or SSH connection |
| Actual macOS Hermes CLI synthetic turn |1 passed: real pinned Hermes completed through the local DPoP bridge and synthetic company/provider; no paid provider call |
| Actual Ubuntu Hermes CLI synthetic turn |1 passed: real `hermes chat --query-file - --oneshot` completed through the local DPoP bridge and synthetic company/provider; no paid provider call |
| Actual macOS Hermes memory tools and managed exit synchronization |1 passed: real tool calls write MEMORY/USER, successful tool results return to the provider, managed exit publishes both files, and a second home receives exact bytes; existing oneshot/skill tests also passed (3 total) |
| Latest actual Ubuntu Hermes memory tools and managed exit synchronization |1 test ran and passed on Python3.12; the separate oneshot1 and native checks2 also ran and passed, with no skips |

An earlier Ubuntu session/ACK attempt initially stopped because the container source allowlist omitted the newly added public `monitoring/sync-status-manifest.v1.json`; its artifact parity test failed before upstream installation. The allowlist now explicitly includes the public `monitoring/` directory. That corrected snapshot completed successfully. The latest268-test snapshot subsequently completed all stages above and removed its container; Docker confirmed its absence. Ordinary-suite opt-in skips are reported separately from the explicit runs and are not counted as passed. The separate skill-discovery opt-in was not run in this latest Ubuntu invocation; its earlier macOS evidence remains distinct. The new identity tests also use the OS temporary directory instead of assuming macOS `/private/tmp` exists.

The installed Ubuntu Git `HEAD` is exactly `2237be355906fbe6065ce1815711eee52b2d646e`; the CLI reports Hermes0.21.1/release2026.9.7, Python3.12.3 and OpenAI SDK2.24.0. Use Git `HEAD` for the pin check; the CLI's update-related `upstream` display is not that immutable pin. Test totals describe the executed source snapshot; later added tests require a new run before updating totals.

## Install and recovery changes

The installer now preserves an existing owned parent directory's permissions and creates only missing directories with mode0700. It clones and verifies the supported commit in a private sibling staging directory, then publishes that checkout before creating its venv. A failed clone leaves no final runtime directory, and retry succeeds. A venv is created only at its final absolute path because its scripts cannot safely be relocated.

If dependency installation fails, the verified checkout remains and rerunning `install-runtime` repairs dependencies at the same supported pin. Existing modified/wrong-pin checkouts are rejected without replacement. A destination that appears during staging is preserved. Tests verify that untracked local files, sibling owner files, personality backups and pending outbox entries survive relevant repair/backup flows. Staging is cleaned on ordinary exceptions; a hard process kill can leave a `.myhermes-install-*` temporary directory containing public source only. Inspect and remove that orphan separately; it is not a personality backup.

The upgrade backup contains only `SOUL.md`, `memories/MEMORY.md`, and `memories/USER.md` plus revision metadata. Session DBs, dotenv values and environment keys are not copied. See [COMPANION.md](COMPANION.md) for owner recovery commands and [RELAY_BRIDGE.md](RELAY_BRIDGE.md) for managed-launch restrictions.

## Reproduce

From the Public repository on a host with an already working local Docker daemon:

```sh
scripts/check-ubuntu-container.sh
MYHERMES_CHECK_UPSTREAM=1 scripts/check-ubuntu-container.sh
```

The second command additionally installs the fixed official upstream and runs actual runtime/config/plugin/inline-shell, environment-hint, one-turn and memory-tool/session-exit probes. Both commands create and remove disposable containers with no host mounts. They download public OS/Python packages and retain only the official base-image cache on the host. The script refuses a non-local Docker endpoint.

For the owner's explicit native macOS check:

```sh
MYHERMES_NATIVE_KEYRING=1 .venv/bin/python -m unittest discover -s tests -p test_native_keyring.py -v
```

For existing installed pinned upstream and installer recovery fixtures:

```sh
MYHERMES_TEST_UPSTREAM=/absolute/path/to/hermes-agent .venv/bin/python -m unittest discover -s tests -p test_hermes_oneshot.py -v
MYHERMES_TEST_UPSTREAM=/absolute/path/to/hermes-agent .venv/bin/python -m unittest discover -s tests -p test_hermes_memory_sync.py -v
MYHERMES_TEST_UPSTREAM=/absolute/path/to/hermes-agent .venv/bin/python -m unittest discover -s tests -p test_runtime_environment.py -v
.venv/bin/python -m unittest discover -s tests -p test_runtime_install.py -v
```

## Current validation boundary

The Ubuntu checks establish execution on a real Ubuntu userspace/kernel environment in a local container, including actual Secret Service and Hermes code paths. They do not establish Ubuntu desktop UI behavior, arbitrary CPU architectures, production Access enrollment, service-manager autostart, live external account grants, or Cloudflare deployment. Native macOS round-trip success does not prove behavior when the user's Keychain is later locked or prompts are denied. No container/native test authorized a real external account or incurred a model charge.

## Repair and personality rollback acceptance

Recorded before the additional recovery implementation/tests:

- R01: A failed dependency repair leaves a discoverable personality backup ID in sanitized CLI output; listing backups exposes metadata only.
- R02: Before backup/repair, a pending file-apply journal is recovered under upstream memory locks, or an intervening owner edit blocks the operation without changing files.
- R03: Restore accepts only an owned, single-link, bounded regular JSON backup with the exact personality allowlist; FIFOs, symlinks, hardlinks, malformed schemas and invalid content are rejected promptly.
- R04: Restore first saves current personality as a separate safety backup, applies the selected files through the durable journal, and preserves current server revision, baseline, outbox and conflicts.
- R05: Runtime repair and personality rollback preserve owner configuration, skills, sessions and credentials in place. Backup means the three published personality paths and metadata; it is not a runtime-version rollback or a config/secrets archive.
- R06: Dry-run validates the intended backup/operation without creating backup files or changing personality, runtime or outbox state.

Earlier additional macOS results: the repair/rollback acceptance suite passed11 tests. The combined installer/recovery/runtime selection ran29 tests, with27 passed and2 explicit-upstream probes skipped; the separate relay fixture regression passed20 tests. The latest Ubuntu268-test snapshot now also runs these repair/rollback and same-home identity-recovery suites; the prior results remain associated with their recorded snapshots.
