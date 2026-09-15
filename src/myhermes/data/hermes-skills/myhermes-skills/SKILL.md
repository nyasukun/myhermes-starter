---
name: myhermes-skills
description: Create, update, inspect, or explicitly derive the owner's MyHermes skills and synchronize complete versioned packages across their environments.
---

# MyHermes skill authoring

Use this skill when the owner asks to create or change reusable Hermes skills. Managed MyHermes runs terminal and execute_code in Docker by default. Author files under the sandbox's `/workspace`; installed skills are read-only there. The host companion CLI, state directory and native credential store are intentionally unavailable inside Docker. Do not run the companion commands below in Hermes terminal/execute_code, install the companion inside Docker, mount the host state/keychain, switch to local execution, or construct a host-execution workaround. Prepare the package and exact import command for the owner. They copy the package from the dedicated sandbox workspace and execute the companion command in their own native host terminal. There is no sandbox-to-host companion bridge in this release.

The commands below are **owner native-terminal instructions**. Use the installed `myhermes` CLI with `MYHERMES_STATE_DIR` explicitly set by the owner to the same state directory used for their managed setup. Before invoking it, check only whether this variable is set and nonempty with `test -n "${MYHERMES_STATE_DIR:-}"`; do not print its value or dump the environment. If the check fails, stop and have the owner confirm the current managed setup. Do not search for, guess, or fall back to another state directory or the CLI default. Always pass `--state-dir "$MYHERMES_STATE_DIR"`, quoted as shown.

Personal and company skills remain available together; the runtime names are `mh-personal-ID` and `mh-company-ID`. A personal derivative is never automatically published to the company.

Create or update authored files in an owner-private working directory outside Git unless the user has explicitly chosen a repository for nonprivate source. Include the root `SKILL.md`, needed references, scripts and assets. Never include credentials, `.env`, sessions, logs, another person's content or the whole Hermes home. Scripts are packaged as files; importing does not run them. Preserve executable bits only for scripts that need them.

The portable package requires a root frontmatter with exactly these two fields and a logical lowercase slug ID of at most 40 characters:

```markdown
---
name: example-skill
description: "What this skill does and when Hermes should use it"
---

Task-specific instructions and references.
```

Use a new explicit three-component version when changing any file or package metadata. An existing version is immutable. Filenames are portable relative ASCII paths, without hidden components, traversal, case collisions or symlinks. Limits are 100 files, 512 KiB per file, and 2 MiB total decoded content. Reference files and binary assets are supported. Upstream environment/credential frontmatter hooks are not accepted by this package format.

Import an authored package with real identifiers and a concise description:

```sh
myhermes --state-dir "$MYHERMES_STATE_DIR" skills import --source PRIVATE_DIRECTORY --skill-id example-skill --version 1.0.0 --description 'Purpose of this skill' --dry-run
myhermes --state-dir "$MYHERMES_STATE_DIR" skills import --source PRIVATE_DIRECTORY --skill-id example-skill --version 1.0.0 --description 'Purpose of this skill'
```

When a skill needs existing account bindings, add `--connector github@1.0.0` and explicit `--connection CONNECTION_ID` values. Requirements identify dependencies; they do not authorize an account or broaden its current grant. Missing requirements keep the package available in the personal library while preventing runtime activation.

When the owner runs import/delete/derive in a separate native terminal during a managed Hermes conversation, these commands safely record durable changes with `activation: after_session`; the active skill directory remains unchanged until the session ends. Do not repeatedly invoke sync to bypass this boundary. Outside the session, `myhermes --state-dir "$MYHERMES_STATE_DIR" skills sync` sends queued personal changes and receives authorized company packages; use it only where the same managed state variable is available. `myhermes --state-dir "$MYHERMES_STATE_DIR" skills list` reports metadata, pending updates and missing requirements. A read-only offline start uses cached packages and cannot establish current publication authorization.

For a company skill or a distinct personal derivative:

```sh
myhermes --state-dir "$MYHERMES_STATE_DIR" skills derive SOURCE_ID --from-scope company --as PERSONAL_ID --version 1.0.0
```

Derivation explicitly copies the selected received package, including any valid local changes, into a personal package and records its source version/hash. It does not change company distribution. If a managed personal directory was intentionally edited directly, import that exact directory with `--projected`, its logical skill ID and a new version; the CLI verifies that no later edit is lost. Changes made by upstream `skill_manage` in unrelated, unregistered directories remain outside MyHermes sync until explicitly imported.

Conflicts preserve both packages. Use `myhermes --state-dir "$MYHERMES_STATE_DIR" skills conflicts`; export a specific candidate only to an owner-private directory with `--update-id UPDATE_ID --export-dir DIRECTORY`. Resolve with `myhermes --state-dir "$MYHERMES_STATE_DIR" skills resolve UPDATE_ID --choice local --version NEW_VERSION` or `--choice remote` according to the owner's intended content. Never choose automatically merely to clear an error. `myhermes --state-dir "$MYHERMES_STATE_DIR" skills history` is metadata-only; specific revision exports require an explicit directory. Do not print package bodies for diagnostics.

Interrupted directory activation uses `myhermes --state-dir "$MYHERMES_STATE_DIR" skills recover`; if post-crash edits prevent normal recovery, add `--choice target` to first preserve those trees outside runtime discovery. `myhermes --state-dir "$MYHERMES_STATE_DIR" skills restore SKILL_ID --scope company` likewise preserves local changes before restoring the recorded company package, after which sync can apply its current publication or withdrawal. Report the returned backup IDs. A managed directory drift error must not be bypassed by deleting the user's edits. No ordinary command publishes a personal package to the company library.

Package creation inside Docker alone is not import or synchronization. Completion means the owner has confirmed the requested package bytes were validated and durably imported; distinguish queued/offline/deferred activation from synchronized and active. Report logical ID, version, scope, pending state and any missing requirement, without leaking private skill content into monitoring or company metadata.
