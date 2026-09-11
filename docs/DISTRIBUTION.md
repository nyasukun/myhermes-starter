# Python distribution and bundled runtime assets

The `myhermes-companion` wheel includes executable Python modules, `monitoring-manifest.json`, `sync-status-manifest.json`, `hermes_monitoring_plugin.py`, the local relay bridge, and these public Hermes skills as package data:

- `myhermes-skills`: author complete versioned personal packages, explicitly derive company packages and use the public import/recovery commands.
- `myhermes-connections`: choose explicit connection IDs/resources and use the fixed read-only connector within the same conversation.

The repository's `hermes-skills/` files and `src/myhermes/data/hermes-skills/` package copies must remain byte-identical; the builtin tests check this. These public instructions contain no company settings, account credentials or owner content. The Codex development skills in `.agents/skills/` remain repository assets; they are not installed into Hermes or silently installed into a user's Codex configuration by pip.

## Install and project the packaged instructions

Build a wheel from the public source:

```sh
.venv/bin/python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist
```

After installing that wheel and configuring a Hermes home with `myhermes setup`, use the same verified executable and explicit state directory for every command. These examples continue the virtual environment and state directory from [USER_GUIDE.md](USER_GUIDE.md); substitute the paths selected during your setup when they differ. The CLI does not use `MYHERMES_STATE_DIR` as its default. Omitting `--state-dir` selects `~/.local/state/myhermes`, which may identify another installation.

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" skills bootstrap --dry-run
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" skills bootstrap
```

The command needs neither enrollment nor a live service. It creates `HERMES_HOME/skills/myhermes-skills/SKILL.md` and `HERMES_HOME/skills/myhermes-connections/SKILL.md` from `importlib.resources`, so it works without the repository. Managed online/offline start verifies the same assets under the session lock. These reserved builtin names differ from `mh-personal-ID` and `mh-company-ID`; no account profiles are introduced, and builtin instructions never enter a user's sync outbox.

Owner modifications, extra files, unmanaged name collisions, symlinks and unsafe files stop normal replacement. To restore the bundled version deliberately:

```sh
.venv/bin/myhermes --state-dir "$HOME/.local/state/myhermes-work" skills bootstrap --restore
```

This moves the complete modified ordinary directory outside runtime discovery to `HERMES_HOME/.myhermes-builtin-backups/UUID/NAME/` before replacement. Reported backup IDs identify retained files. Symlinked paths remain refused. A persistent journal recovers interruption after the directory move or file replacement; it does not overwrite a newly modified third state. Backups are not automatically deleted in the MVP. Arbitrary external writers that ignore the managed lock remain unsupported.

## Executed package check

On 2026-09-11 a wheel was built locally without fetching dependencies, installed with pip into an isolated temporary target, and its installed `bin/myhermes` entry point was run from a temporary directory. It performed `setup` and `skills bootstrap`; imports were asserted to come from the installed wheel target, and both projected files matched package resources. The wheel archive also contained the manifest, monitoring plugin and relay bridge. No repository path was used to read the installed assets. The test reused the development interpreter's installed runtime dependencies; it was not a fresh OS/dependency installation or a live account authorization.

The installed-wheel check also ran the pinned official Hermes discovery and an actual CLI turn against a local synthetic provider. One fresh home simultaneously contained the two bundled skills and personal/company packages sharing the original `demo` ID. Official `skills_list` and `skill_view` retrieved all four distinct runtime names and their separate references. The initial inference included all four names and the official `skills_list`, `skill_view` and `skill_manage` tool schemas. No live inference account was used.

To repeat the opt-in official runtime test from a source checkout:

```sh
MYHERMES_TEST_UPSTREAM=/absolute/pinned-hermes-checkout .venv/bin/python -m unittest discover -s tests -p test_hermes_skill_discovery.py -v
```

For an installed-wheel run, execute the same test discovery from outside the repository with `PYTHONPATH` and `MYHERMES_TEST_INSTALLED` pointing to the isolated pip target. The latter assertion prevents an accidental source import from passing the installed-package check.

Unit regressions cover modified instructions, unmanaged extra files, interrupted replacement, interrupted explicit restore and symlink rejection. [OS_CHECKS.md](OS_CHECKS.md) distinguishes host, container, native keychain and real runtime checks.
