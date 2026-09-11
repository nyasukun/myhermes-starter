# M4 operating-system acceptance

The checks below distinguish actual OS execution, native credential-store access, and fixture tests. They run Public product code and synthetic data only; the separately supplied generic process-crash review driver is identified below. A test against a contract peer does not establish production enrollment, Cloudflare deployment or external-account authorization.

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
| Earlier Ubuntu source snapshot, including identity validation, telemetry stream rotation, causal skill resolution and environment hints |268 Python tests ran:259 passed and9 explicit opt-in skips, no failures; Ruff passed |
| Earlier Ubuntu source snapshot, including connection validator parity, YAML header/comment rejection, 21-slot auxiliary routing and bundled state-directory guidance |278 Python tests ran:266 passed and12 explicit opt-in skips, no failures; Ruff passed |
| Earlier Ubuntu source snapshot, including enrollment-response validation, partial-venv repair and handled child termination |288 Python tests ran:276 passed and12 explicit opt-in skips, no failures; Ruff passed |
| Latest Ubuntu source snapshot, including native terminal input and internal relay-credential isolation |302 Python tests ran:289 passed and13 explicit opt-in skips, no failures; Ruff passed |
| Latest Ubuntu real controlling-terminal fixtures |12 credential-entry/identity-confirmation tests plus1 managed-runtime PTY test passed within the normal suite; the latter also passed separately. Synthetic input/child only, not real authorization |
| Latest actual pinned terminal credential isolation |1 explicit test passed: six child-environment variants, forced passthrough rejection, secret-free shell snapshots before/after a command, and retained state-directory metadata; zero skips |
| Additional actual Ubuntu process-death review |7 separate SIGKILL groups passed: partial persona replacement/deletion, skill directory moves, fresh-process managed-start recovery and post-crash edit refusal; no provider, zero skips |
| Ubuntu explicit native checks |2 passed separately, including missing-bus failure |
| Installer recovery tests using real local Git fixtures |8 passed on macOS and8 on Ubuntu; dependency install/failure is a synthetic command boundary in these tests |
| Latest Ubuntu interrupted-venv repair |3 added tests ran and passed within the normal suite using real Python venv/ensurepip: missing pip repairs without recreation, while selected-version or target-prefix mismatch rejects before pip writes; no bootstrap network access |
| Actual Ubuntu official Hermes installation |Git clone, pinned checkout, Python3.12 venv and official `.[cli]` dependency install completed successfully through `install_runtime` |
| Latest actual Ubuntu pinned config/provider/plugin/inline-shell probes |11 runtime tests ran and passed with `MYHERMES_TEST_UPSTREAM` set; no skips |
| Latest actual Ubuntu environment-hint probes |4 tests ran and passed; exact owner-hint precedence, process-only lifetime, config preservation and actual official prompt building with local/simulated-remote context; no skips or SSH connection |
| Latest actual Ubuntu auxiliary-provider routing |2 tests ran and passed; all21 verified task clients and the side-question fork selected the managed route, while DNS/socket access was blocked and the original owner config stayed unchanged; no skips |
| Latest actual Ubuntu managed-process termination |3 groups/5 scenarios ran and passed in both normal and explicit selections: SIGTERM/SIGHUP, ignored repeated signals and spawn races; child reaping and session finalization precede lock release; no skips |
| Latest actual Ubuntu strict skill header/discovery probes |5 tests ran and passed, including the actual pinned parser and skill-list behavior for bare CR/NEL/LS/PS and TAB comments; Japanese, normal TAB and quoted descriptions remain valid; no skips |
| Actual macOS Hermes CLI synthetic turn |1 passed: real pinned Hermes completed through the local DPoP bridge and synthetic company/provider; no paid provider call |
| Actual Ubuntu Hermes CLI synthetic turn |1 passed: real `hermes chat --query-file - --oneshot` completed through the local DPoP bridge and synthetic company/provider; no paid provider call |
| Actual macOS Hermes memory tools and managed exit synchronization |1 passed: real tool calls write MEMORY/USER, successful tool results return to the provider, managed exit publishes both files, and a second home receives exact bytes; existing oneshot/skill tests also passed (3 total) |
| Latest actual Ubuntu Hermes memory tools and managed exit synchronization |1 test ran and passed on Python3.12; the separate oneshot1 and native checks2 also ran and passed, with no skips |

An earlier Ubuntu session/ACK attempt initially stopped because the container source allowlist omitted the newly added public `monitoring/sync-status-manifest.v1.json`; its artifact parity test failed before upstream installation. The allowlist now explicitly includes the public `monitoring/` directory. That corrected snapshot completed successfully. The later268-,278-,288- and latest302-test snapshots completed their explicit stages and removed their containers; Docker confirmed each container's absence. Ordinary-suite opt-in skips are reported separately from the explicit runs and are not counted as passed. The separate four-skill discovery/inference opt-in was not run in the latest Ubuntu invocation; its earlier macOS evidence remains distinct from the new header/discovery probe. The identity tests use the OS temporary directory instead of assuming macOS `/private/tmp` exists.

The earlier278-test run on 2026-09-11 used the same pinned Ubuntu image and copied102 explicitly allowlisted Public files, with no host/private/env mounts or published ports and `privileged=false`. An exact filename/size/SHA-256 ledger was read from the copied source and matched against the host Public snapshot before results were added to this document. Temporary evidence is under `/private/tmp/myhermes-ubuntu-latest-20260911/` (`source-ledger.json`, `report.json`, `run.log`, and isolation/cleanup metadata); ledger SHA-256 is `dad588ce84524bfe3c13a7132277b26a748438a7963c73477f9affc58dd6c836`. The script exited0. Separate explicit stages ran2 native checks,11 runtime relay checks,4 environment checks,2 auxiliary checks,5 header checks,1 oneshot and1 memory-exit check, all passed with zero skips. These stages overlap the normal suite and are not additional unique-test counts.

The subsequent288-test run copied104 Public files under the same isolation boundary and verified every copied source hash against the host snapshot. Its source ledger SHA-256 is `8af86e139af4ff4d5d77317e1c335b0c2be378c4fda67095443be541de0135aa`; evidence is under `/private/tmp/myhermes-ubuntu-lifecycle-20260911/`, including the source delta, per-stage report, raw synthetic test output and exact-container cleanup check. All explicit stages passed with zero skips: native2, runtime relay11, environment4, auxiliary2, signals3, header5, oneshot1 and memory-exit1. The fresh official installation verified the same commit. The container was removed and script exit was0. No Python module or packaged asset changed during this rerun; only this document and the script's explicit signal-test invocation changed. The previous cross-OS51-frame protocol run was not repeated, since that protocol implementation did not change.

The latest302-test run copied109 allowlisted Public files and verified every filename, size and SHA-256 against the host snapshot before testing. Its ledger SHA-256 is `d1fe1929cb3a9f1c08cc650609f678699717c27ef4af17ebd61719c63c141b14`; evidence is under `/private/tmp/myhermes-ubuntu-terminal-20260911/` (`source-ledger.json`, `source-delta.json`, `report.json`, `run.log` and cleanup metadata). New files include `terminal_input.py` and four terminal/scrub test modules. Normal execution ran302 tests:289 passed and13 explicit opt-in checks were skipped. Every separate explicit stage passed without skips: native2, runtime relay11, environment4, auxiliary2, credential scrub1, runtime PTY1, signals3, header5, oneshot1 and memory-exit1. The container was removed and the script exited0. This rerun changed only the script and OS documentation; it tested the already released34 Python modules and packaged assets. No external inference was used.

After the Public suite, one standalone generic synthetic process-crash review driver was copied separately; no company application, settings, data, secrets or other Private files accompanied it. Its7 groups passed on Linux/Python3.12.3 and matched the earlier macOS run's nine Public source hashes. Each writer was killed at a real rename/deletion boundary, another process was refused while its lock remained held, and fresh-process recovery completed before a synthetic launch observer. Repeated recovery kept immutable outbox bytes and revisions; third-state owner edits blocked instead of being overwritten. This driver is additional review evidence and is not included in the Public Ubuntu script or its302-test total. Power loss, arbitrary unmanaged writers and Ubuntu desktop authentication remain outside these claims.

The installed Ubuntu Git `HEAD` is exactly `2237be355906fbe6065ce1815711eee52b2d646e`; the CLI reports Hermes0.21.1/release2026.9.7, Python3.12.3 and OpenAI SDK2.24.0. Use Git `HEAD` for the pin check; the CLI's update-related `upstream` display is not that immutable pin. Test totals describe the executed source snapshot; later added tests require a new run before updating totals.

## Install and recovery changes

The installer now preserves an existing owned parent directory's permissions and creates only missing directories with mode0700. It clones and verifies the supported commit in a private sibling staging directory, then publishes that checkout before creating its venv. A failed clone leaves no final runtime directory, and retry succeeds. A venv is created only at its final absolute path because its scripts cannot safely be relocated.

If dependency installation fails, the verified checkout remains and rerunning `install-runtime` repairs dependencies at the same supported pin. Existing modified/wrong-pin checkouts are rejected without replacement. A destination that appears during staging is preserved. Tests verify that untracked local files, sibling owner files, personality backups and pending outbox entries survive relevant repair/backup flows. Staging is cleaned on ordinary exceptions; a hard process kill can leave a `.myhermes-install-*` temporary directory containing public source only. Inspect and remove that orphan separately; it is not a personality backup.

An interruption after creating the venv interpreter but before installing pip is also recoverable: the installer verifies the exact target prefix and matching supported Python version, then uses that interpreter's bundled `ensurepip` only if pip is missing. It does not delete or recreate the existing venv or alter owner files. The actual Ubuntu tests cover that partial state and rejection of an unrelated/base interpreter before writes. Missing distributor venv prerequisites still need local operator installation.

The upgrade backup contains only `SOUL.md`, `memories/MEMORY.md`, and `memories/USER.md` plus revision metadata. Session DBs, dotenv values and environment keys are not copied. See [COMPANION.md](COMPANION.md) for owner recovery commands and [RELAY_BRIDGE.md](RELAY_BRIDGE.md) for managed-launch restrictions.

## Reproduce

From the Public repository on a host with an already working local Docker daemon:

```sh
scripts/check-ubuntu-container.sh
MYHERMES_CHECK_UPSTREAM=1 scripts/check-ubuntu-container.sh
```

The second command additionally installs the fixed official upstream and runs actual runtime/config/plugin/inline-shell, environment-hint, auxiliary-provider, terminal-credential isolation, real runtime PTY, managed-signal, strict-header/discovery, one-turn and memory-tool/session-exit probes. Both commands create and remove disposable containers with no host mounts. They download public OS/Python packages and retain only the official base-image cache on the host. Optional local validation reports contain source hashes and synthetic results only. The script refuses a non-local Docker endpoint.

For the owner's explicit native macOS check:

```sh
MYHERMES_NATIVE_KEYRING=1 .venv/bin/python -m unittest discover -s tests -p test_native_keyring.py -v
```

For existing installed pinned upstream and installer recovery fixtures:

```sh
MYHERMES_TEST_UPSTREAM=/absolute/path/to/hermes-agent .venv/bin/python -m unittest discover -s tests -p test_hermes_oneshot.py -v
MYHERMES_TEST_UPSTREAM=/absolute/path/to/hermes-agent .venv/bin/python -m unittest discover -s tests -p test_hermes_memory_sync.py -v
MYHERMES_TEST_UPSTREAM=/absolute/path/to/hermes-agent .venv/bin/python -m unittest discover -s tests -p test_runtime_environment.py -v
MYHERMES_TEST_UPSTREAM=/absolute/path/to/hermes-agent .venv/bin/python -m unittest discover -s tests -p test_runtime_auxiliary.py -v
MYHERMES_TEST_UPSTREAM=/absolute/path/to/hermes-agent .venv/bin/python -m unittest discover -s tests -p test_runtime_credential_scrub.py -v
.venv/bin/python -m unittest discover -s tests -p test_runtime_terminal.py -v
.venv/bin/python -m unittest discover -s tests -p test_runtime_signals.py -v
MYHERMES_TEST_UPSTREAM=/absolute/path/to/hermes-agent .venv/bin/python -m unittest discover -s tests -p test_skill_frontmatter.py -v
.venv/bin/python -m unittest discover -s tests -p test_runtime_install.py -v
```

## Current validation boundary

An earlier isolated macOS ARM64 run used portable CPython 3.13.15 from the official Astral python-build-standalone release `20260901`, without changing the system Python or an existing Hermes runtime. Fresh public dependencies and the installed companion wheel passed all 288 tests: 276 passed with the 12 optional checks disabled. A separate fresh checkout of the exact supported Hermes commit was then installed under Python 3.13.15; enabling its real-code checks yielded 286 passed and only the two native credential-store checks skipped. This included official interactive-runtime components exercised in oneshot mode, the memory tool and exit synchronization, skill discovery, auxiliary routing, environment hints and the real synthetic-child signal tests. The two environments passed `pip check`; Ruff checked 66 files. This additional run did not exercise real account authorization or call a paid inference provider.

The verified Python 3.13 build produced a universal wheel of 112,836 bytes, SHA-256 `8a8a70456dd98453cccf418ffc8fdf02a84049c718d26a285d87ddc82a05e138`. All 33 Python modules, two public manifests and two built-in skill files matched exported source, wheel and installed bytes. These measurements apply to that exact artifact, rather than a future rebuild of the same unreleased version number.

The Ubuntu checks establish execution on a real Ubuntu userspace/kernel environment in a local container, including actual Secret Service and Hermes code paths. They do not establish Ubuntu desktop UI behavior, arbitrary CPU architectures, production Access enrollment, service-manager autostart, live external account grants, or Cloudflare deployment. Native macOS round-trip success does not prove behavior when the user's Keychain is later locked or prompts are denied. No container/native test authorized a real external account or incurred a model charge.

## Repair and personality rollback acceptance

Recorded before the additional recovery implementation/tests:

- R01: A failed dependency repair leaves a discoverable personality backup ID in sanitized CLI output; listing backups exposes metadata only.
- R02: Before backup/repair, a pending file-apply journal is recovered under upstream memory locks, or an intervening owner edit blocks the operation without changing files.
- R03: Restore accepts only an owned, single-link, bounded regular JSON backup with the exact personality allowlist; FIFOs, symlinks, hardlinks, malformed schemas and invalid content are rejected promptly.
- R04: Restore first saves current personality as a separate safety backup, applies the selected files through the durable journal, and preserves current server revision, baseline, outbox and conflicts.
- R05: Runtime repair and personality rollback preserve owner configuration, skills, sessions and credentials in place. Backup means the three published personality paths and metadata; it is not a runtime-version rollback or a config/secrets archive.
- R06: Dry-run validates the intended backup/operation without creating backup files or changing personality, runtime or outbox state.

Earlier additional macOS results: the repair/rollback acceptance suite passed11 tests. The combined installer/recovery/runtime selection ran29 tests, with27 passed and2 explicit-upstream probes skipped; the separate relay fixture regression passed20 tests. The subsequent Ubuntu288- and302-test snapshots also run these repair/rollback and same-home identity-recovery suites; the prior results remain associated with their recorded snapshots.
