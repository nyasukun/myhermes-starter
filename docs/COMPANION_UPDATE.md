# Companion update route — 0.4.0

Acceptance criteria (2026-09-24, before implementation):

- An administrator selects a released public companion wheel by stable version, exact public GitHub release URL and SHA-256. The server stores the selection with a revision and idempotent request ID. Clearing the selection disables update advice. No artifact is assumed to exist before a release is actually published.
- `myhermes --state-dir STATE self-update --check` fetches the company-selected version and compares it with the running package. `--dry-run` describes the operation. Only explicit `--apply` downloads and installs it; startup, the portal and the Hermes tool only offer guidance.
- Update refuses system Python, editable checkouts, downgrades and active managed processes sharing the companion virtual environment. It holds the installation's identity/state/home locks and a virtual-environment writer lock; ordinary CLI/managed sessions hold the corresponding reader lock.
- Download is HTTPS-only from the fixed public repository release, with redirects limited to GitHub release asset hosts, no authenticated headers, a timeout and a 50 MiB bound. Verify SHA-256 and wheel package/version before invoking pip. Preserve the verified wheel privately for explicit recovery.
- Invoke the current virtual environment's pip with isolated settings and no index. Resolve against existing dependencies before installation; missing/new dependencies stop the update. Inspect the installed package version in a fresh interpreter. Never edit owner config, state or native credentials.
- Portal and packaged skill explain the update command and the bootstrap step for pre-0.4.0 clients. Reported companion version is included in the environment connection snapshot. Tests use synthetic artifacts/processes, never update the user's installed companion.

## Owner commands

Close managed CLI, Desktop and MCP work before applying an update in the owner's host terminal:

```sh
myhermes --state-dir "$MYHERMES_STATE_DIR" self-update --check
myhermes --state-dir "$MYHERMES_STATE_DIR" self-update --dry-run
myhermes --state-dir "$MYHERMES_STATE_DIR" self-update --apply
```

Use the same explicit state directory and virtual-environment executable used for setup. Restart managed Hermes after the update; its packaged skill/plugin is activated on the next launch. `upgrade` still updates the pinned Hermes runtime. The companion updater changes only `myhermes-companion` in its current virtual environment and requires its dependencies to be available there already.

Older companions do not have `self-update`. Their bootstrap route is to download the administrator-designated public wheel, verify its SHA-256 against the release, and run the selected companion virtual environment's `python -m pip install --no-index /path/to/verified.whl`. Do not use system Python or another environment. Future updates can use `self-update`.

The administrator configures the target at `PUT /v1/admin/companion-release` with `{schema_version:"1",request_id,base_revision,release:{version,wheel_url,sha256}}`. `release:null` withdraws it. All enrolled members can read `GET /v1/companion-release`; selection is data until an owner invokes `--apply`. A wheel hash binds the selected bytes; administrators must select a release they have reviewed.

Installation is not an atomic virtual-environment replacement. If pip is interrupted, the error includes the retained verified wheel and selected Python path for explicit repair. Wheels are retained under `STATE/updates/SHA256/`. Retrying uses the selected release again; a failed update must not be reported as complete.

The packaged connections plugin has its own hash journal. A normal companion update replaces previously managed plugin files on the next Hermes launch; interruption between files can be resumed. Owner changes to that reserved plugin directory cause a refusal with recovery guidance, rather than being overwritten. Company catalog edits and uncertain portal saves retain their input and original request ID until their result is known.
