# Managed Hermes Desktop

On macOS, run `myhermes prepare-desktop` once after installing the pinned runtime, then `myhermes desktop` (equivalent to `myhermes start --desktop`). Both commands accept the usual global `--state-dir`. Preparation needs Node.js 24.11+ in the 24.x line, npm supported by the pinned upstream, Git and the installed Hermes runtime. The companion itself can use Python 3.14; the pinned runtime still needs Python 3.11–3.13.

The public companion owns this integration; there is no private portal API change. `desktop` enters the existing `start` implementation, so it uses enrollment, owner authentication, pre/post persona and skill synchronization, the home session lock, shared runtime lock, Docker policy, relay bridge and existing metadata-only monitoring. `prepare-desktop` takes the exclusive runtime lock, never loads enrollment credentials and never calls the production application. `desktop --dry-run` verifies the prepared source/build without starting Docker or making owner API calls.

## Source and build ownership

The original official checkout remains clean at `2237be355906fbe6065ce1815711eee52b2d646e`. Preparation clones that local commit into `<upstream>/.myhermes-desktop/source`. The reviewable transformations in `desktop_adapter.py` apply only to this separate copy. `desktop-sources.json` pins every adapted file's original SHA-256; an unknown source fails before editing. The standalone `desktop_policy.py` is copied alongside the adapted source. Original license notices remain in the copy.

Preparation runs the pinned npm lockfile install and macOS packaging scripts with a temporary build home. It stores a manifest for the packaged executable, app archive and unpacked application code. A failed build can be retried; it never publishes a successful manifest. Startup checks the original pin, the adapted source, the copied policy and the built artifact manifest. Unexpected source changes are preserved and rejected. This verifies local consistency, not a remotely attested or independently signed distribution.

Desktop starts its Python backend with the original pinned venv interpreter and the adapted source on `PYTHONPATH`. Its process receives the enrolled `HERMES_HOME`, temporary `HERMES_MANAGED_DIR` and session-only loopback relay credential. Electron's persistent UI data lives under the same home in `.myhermes-desktop`, separate from other Desktop installations. Ambient Desktop boot/remote/developer hooks, Electron/Node hooks and Python path overrides are cleared. The bundle requires a starter session at startup; Finder/Dock launches do not create a managed session.

## Inference and home policy

All model inventory callers return one provider, **MyHermes**, with model **economy**. Main provider resolution rejects other providers, models, endpoints or keys before credential lookup. Auxiliary task routing and client construction use the same relay; explicit alternate routes are rejected. Model settings accept the managed choice without writing an ephemeral key or endpoint to owner configuration. Local-model UI is hidden. The Desktop connection registry is local-only, the active profile is the enrolled default home, cross-home overrides fail, and Desktop self-updates are disabled. Update through the companion and repeat `prepare-desktop`.

These restrictions apply to the managed Desktop copy. The original `hermes` executable and the existing TUI adapter retain their documented behavior. This is an application integration, not an OS network firewall or a sandbox for arbitrary owner-installed plugins that create their own network clients.

## Exit and sync boundary

The companion launches the GUI directly as a new owned process group, rather than using macOS `open` or a detached launcher. The relay and home lock remain active while the GUI/backend run. On normal GUI exit or handled interruption, remaining processes in that group are stopped before the companion snapshots memory and finalizes synchronization. The upstream parent watchdog additionally handles an unclean Electron death. A hard kill of the companion cannot promise final synchronization; the durable outbox and next explicit sync/start provide recovery.

Use Command+Q to quit; closing the last window can leave Electron running. The terminal must stay open until `persona_after_session` and `skills_after_session` report their results. Changes are synchronized at the session boundaries, not continuously while the GUI is open. Conversations themselves are not synchronized: the model must successfully save user facts with `memory` target `user`, which writes `memories/USER.md`. Upstream memory-write approvals remain in effect.

## Verification

`tests/test_desktop.py` covers picker inventory, explicit alternate routes, missing starter credentials, different-home refusal, environment cleanup, command aliases, actual child/backend termination and session-exit sync with synthetic data. With `MYHERMES_TEST_UPSTREAM` pointing to the public pinned source it also compiles every adapted Python module and exercises the inserted guards in the pinned functions. Ordinary runtime/sync tests remain applicable. Native visual operation and production portal verification must be reported separately from these checks.

On 2026-09-24, the native macOS package built in a temporary directory and passed source/artifact verification. The nine Desktop tests passed with companion Python 3.11 and 3.14.6. Setting `MYHERMES_TEST_DESKTOP_SOURCE` to the prepared copy additionally exercised the real pinned backend's model-options route, main/auxiliary policy and official on-disk user-memory save, without any network connection. The six related runtime/session suites passed 37 tests with three optional checks skipped; public Core checks passed all 70 tests. Ruff, source syntax and wheel contents passed. Native GUI operation and production synchronization were not exercised; the full Python regression suite was not run.
