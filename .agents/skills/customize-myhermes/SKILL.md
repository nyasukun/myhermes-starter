---
name: customize-myhermes
description: Edit and synchronize the owner's MyHermes personality, memory and personal skills through the published companion.
---

# customize-myhermes

Use when the owner requests personality, persistent-memory or personal-skill changes for their MyHermes environment.

Personality inputs: state directory, one supported path (`SOUL.md`, `memories/MEMORY.md`, `memories/USER.md`), and an owner-authored private local UTF-8 source file or explicit deletion.

1. Run `myhermes --state-dir STATE inspect`. Do not read/export private personality into Codex unless the owner specifically asked you to review that content.
2. Run `myhermes --state-dir STATE edit --path PATH --source SOURCE --dry-run`, then the same command without `--dry-run` for an authorized edit. For explicit deletion use `--delete`, never an empty placeholder file.
3. Run `myhermes --state-dir STATE sync --dry-run`, followed by `sync` when owner synchronization is authorized.

Failure/recovery: do not truncate over-capacity memory. Exit 7 means an active managed session must finish. Exit 6 preserves conflicts: use `conflicts` metadata and have the owner choose a deliberate `resolve UUID --choice local|remote`; keep private exports outside Git and conversation. Offline exit 4 retains the queued update for retry.

For a personal skill, use a private authored directory containing `SKILL.md` and all required reference/script/asset files. Its frontmatter has exactly `name: logical-id` and `description: "Single-line description"`. Use an explicit new semantic version for changes and run `skills import --source DIRECTORY --skill-id ID --version VERSION --description DESCRIPTION --dry-run`, then import without dry-run. Optional pinned connector and logical connection requirements use `--connector ID@VERSION` and `--connection UUID`; never put credentials in these fields or files. During a managed session the import is durable but runtime activation waits until it ends. Outside the session, `skills sync` synchronizes the personal package and received company packages. Inspect `skills list` for pending state and missing requirements.

Derive a received company skill only on the owner's request: `skills derive SOURCE_ID --from-scope company --as PERSONAL_ID --version VERSION`. This creates a personal derivative; it does not publish anything company-wide. The runtime namespaces remain distinct (`mh-company-ID`, `mh-personal-ID`) in the same Hermes. Unregistered upstream `skill_manage` directories stay outside sync. Direct edits to a managed personal projection require explicit `skills import --projected` from that directory with a new version; company edits require derivation. For skill conflicts use `skills conflicts` and explicit `skills resolve UUID --choice local --version NEW_VERSION` or `--choice remote`; for interrupted directory activation use `skills recover`. Do not clear conflicts by discarding files.

Completion: the requested local change is applied and its sync result is reported accurately. Do not publish personal changes to company skills or imply that an offline queue has reached the server.
