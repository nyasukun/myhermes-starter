# Hermes upstream compatibility

Verified on 2026-09-11 against the official Nous Research repository. This document distinguishes inspected source behavior from runtime verification. MyHermes is a companion to local Hermes installations; it does not move Hermes execution or session storage into the control plane.

## Pinned source

| Property | Verified value |
| --- | --- |
| Official repository | <https://github.com/NousResearch/hermes-agent> |
| Release tag | `v2026.9.7`, published 2026-09-07 |
| Commit | `2237be355906fbe6065ce1815711eee52b2d646e` |
| Python package version at this commit | `0.21.1` |
| Python requirement | `>=3.11,<3.14` |
| License | MIT; retain its notice when redistributing upstream software |

The release tag and package version use different numbering. A tag name alone is insufficient for an immutable installation: verify the checkout commit. Updates require deliberate pin changes and compatibility validation.

Evidence: [release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.7), [pinned package metadata](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/pyproject.toml), [license](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/LICENSE).

## Persona files and lifecycle

`HERMES_HOME` selects the Hermes home; the normal default is `~/.hermes`. The adapter resolves one explicit Hermes home for each registered environment. Account categories do not select different Hermes profiles.

| Relative path | Meaning | Upstream behavior |
| --- | --- | --- |
| `SOUL.md` | Agent identity, personality and style | Loaded from Hermes home as the primary identity prompt section, not from the project working directory. |
| `memories/USER.md` | User information and preferences | Read and written by the built-in memory store, using the `user` target. |
| `memories/MEMORY.md` | Durable agent notes | Read and written by the built-in memory store, using the `memory` target. |

Hermes seeds a default SOUL when needed and preserves customized content. This is not an absolute promise that upstream never changes the file: its initializer upgrades recognized legacy scaffolds, and a configuration migration removes a specific obsolete Bot Mode section. MyHermes must detect the resulting local changes rather than assume that SOUL is immutable.

The built-in memory format is UTF-8 text with entries separated by `\n§\n`. A bare `§` inside an entry is not a separator. Upstream strips a UTF-8 BOM when reading, trims entries and deduplicates them in memory. It defaults to 2,200 characters for MEMORY and 1,375 for USER; configuration can change these limits. Synchronization must preserve the text and separator format, distinguish byte limits from character limits, and avoid lossy decoding. A synchronized large file can otherwise load successfully but make later memory edits fail their capacity check.

Memory mutations use a sibling lock file (`MEMORY.md.lock` or `USER.md.lock`) with `fcntl.flock` on the supported Unix platforms. Within that lock Hermes rereads the current file, applies the operation and uses a temporary file plus atomic replacement. It refuses to overwrite an existing unreadable file. Replace/remove operations also guard against external content that would not round-trip through the memory parser. The companion must share these lock paths when applying memory changes, recheck its expected local content while locked, and preserve a conflict when that expectation no longer holds. A private companion lock alone does not coordinate with Hermes.

Memory prompt blocks are frozen when loaded. Mid-session writes normally change disk without modifying that snapshot. However, the pinned source explicitly reloads memory when rebuilding the prompt after context compression. Therefore, “changes only take effect next session” is not a strict guarantee. A fresh session is the predictable point to verify newly synchronized content. SOUL also participates in prompt construction and may be reread at a rebuild. MyHermes does not promise immediate prompt refresh inside a running session.

There is no inspected upstream SOUL write lock shared by all editors. Starting Hermes through the companion permits coordination around startup and shutdown; a separately launched process or editor can still write files. The client must not silently overwrite such local changes.

Source evidence:

- [Home resolution](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/hermes_constants.py#L82)
- [SOUL loading](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/prompt_builder.py#L1440)
- [SOUL seeding](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/hermes_cli/config.py#L623)
- [Memory directory](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/memory_tool.py#L39)
- [Memory store, limits and locking](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/memory_tool_store.py#L66)
- [Prompt rebuild and memory reload](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/system_prompt.py#L680)

## Skills and extension points

Hermes recursively discovers packages containing `SKILL.md` beneath `$HERMES_HOME/skills`. Packages can contain reference documents, templates, scripts, examples and assets. A skill is not just its Markdown entry point. MyHermes package distribution must include a validated manifest of all required files and reject path traversal, symlink escapes and unexpected files.

The verified `skills.external_dirs` setting adds other skill roots to the same agent. Entries are expanded, relative paths are based at Hermes home, missing directories are ignored, and duplicate roots are removed. External skills are read-only to Hermes' skill curator. This supports a separate company-managed package directory alongside personal packages in the normal skills directory without splitting the user's conversation or persona. Package names should be unambiguous; external-directory discovery is not an operating-system access control boundary.

Hermes has optional skill inline-shell preprocessing. Its default is `skills.inline_shell: false`; the MyHermes managed overlay explicitly pins it to false even when owner config enables it. An actual pinned-upstream preprocessor test first reproduced a harmless marker-file command executing during preview, then verified literal text and no execution after the overlay correction. Other skill settings and owner config bytes remain unchanged. Explicitly invoking a packaged script is separate from viewing a skill. Connection templates remain structured data and may only select operations implemented by verified public client code. Their arbitrary strings must never become shell commands.

The pinned runtime supports named OpenAI-compatible providers with a configured endpoint, `key_env` and `transport: chat_completions`. This is an integration point for a relay using an environment-specific credential injected by a trusted launcher. A `key_cmd` facility also exists, but its contract prints tokens to stdout; it does not satisfy MyHermes' credential-output rule and is not the selected integration. Configuring a provider does not prove normal responses, SSE, tool calling, auxiliary calls or retry behavior work through MyHermes; each requires integration tests.

Plugin hooks cover both CLI and Gateway. Some hook payloads contain messages, responses, tool arguments or other sensitive content. A monitoring adapter must construct a new event from a public allowlist; forwarding a hook context or arbitrary attributes would violate the monitoring boundary. The existence of hooks is not evidence of an installed OTel exporter. Memory-provider plugins also exist, but are not required for the initial file-based persona adapter and must not be represented as an implemented cloud memory feature.

Source evidence:

- [Skill package structure](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/website/docs/user-guide/features/skills.md#L303)
- [External roots](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/skill_utils.py#L332)
- [External skill mutation guards](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/skill_manager_guards.py)
- [Skill preprocessing](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/skill_preprocessing.py#L89)
- [Custom provider configuration](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/website/docs/integrations/providers.md#L1305)
- [Hook systems and payloads](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/website/docs/user-guide/features/hooks.md)
- [Memory-provider plugin interface](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/website/docs/developer-guide/memory-provider-plugin.md)

## Verification boundary and upgrade checks

This initial compatibility investigation inspected the official release metadata and pinned source. It did not install or execute the full Hermes runtime, call an LLM provider, or authorize a real service account. The development Mac's default Python is 3.14, which is outside upstream's declared range. A supported Python 3.11–3.13 interpreter is required before making an upstream runtime compatibility claim. Neither Ubuntu nor macOS end-to-end Hermes operation is established by source inspection or by companion-only tests.

The initial synchronization allowlist is exactly SOUL, USER and MEMORY above. It excludes configuration, credentials, `.env`, session databases, sessions, logs, caches, and the remainder of Hermes home. Personal skill package synchronization is a separate milestone. No upstream-native cross-device revision protocol, conflict resolution, cloud identity isolation, body-free relay or MyHermes monitoring implementation is assumed to exist.

For every supported upstream pin, run these checks with a synthetic, isolated Hermes home:

1. Verify the checkout commit, package metadata and supported Python version; reject unknown runtime pins until reviewed.
2. Place synthetic SOUL/USER/MEMORY files and verify upstream reads the intended home and preserves expected entry boundaries.
3. Make an upstream memory-tool write; verify disk updates, the existing prompt snapshot remains stable, and explicit reload observes the change.
4. Exercise companion pull concurrently with an upstream memory mutation using the same sibling lock; verify neither update disappears and stale expectations create a recoverable conflict.
5. Confirm a managed company package and a personal package, including support files, are both discoverable in one agent; no individual package becomes company-published automatically.
6. Verify excluded files, invalid UTF-8, unsafe paths, symlinks, limits and application failures cannot cause unintended synchronization or data loss.
7. When relay and monitoring milestones land, test real normal/SSE/tool-calling and auxiliary request paths plus strict removal of body/argument/result/secret fields from telemetry.

Record actual operating-system and live-integration results separately from these source findings.

## Managed skill package constraints at the pinned version

These are adapter requirements derived from inspected upstream code, not evidence that package distribution or runtime tests have already passed.

Discovery and loading have different collision behavior. `skills_list` scans trusted project roots, the local skills root, then external roots; it truncates each name to 64 characters and keeps the first occurrence. `skill_view` deliberately prefers a project match, but refuses ambiguous matches among local and external roots. Managed packages therefore need unique names of at most 64 characters and unique directory paths. A company/personal prefix must be part of the actual `name`, not just a directory label. Avoid `:` because it dispatches through the plugin namespace resolver. A user-trusted project skill can still shadow a managed name; external roots do not enforce company precedence. [Discovery and collision resolution](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/skills_tool.py#L176), [name limit](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/skills_tool_plugin.py#L15).

An external directory does not automatically receive the skill-hub installation scan. The local/external discovery path walks packages directly; only trusted project roots pass through the project quarantine path. The ordinary loader logs injection-pattern warnings without blocking. The iterator follows filesystem symlinks. MyHermes must validate the complete package itself before activation, including rejecting symlinks, hard links, nonregular files and root escapes. Content scanning may detect known patterns but cannot establish that arbitrary instructions or scripts are safe. An external package is protected from autonomous curator maintenance, not from all user-directed edits or processes running as its owner. [Scan and warning paths](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/skills_tool.py#L203), [iterator](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/skill_utils.py#L736), [external ownership semantics](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/skill_utils.py#L608).

Frontmatter is operational input. `required_environment_variables` can prompt for credentials, persist them into the Hermes environment file and register them for sandbox passthrough; `required_credential_files` can register existing host credential files for remote sandboxes. Slash-command expansion of `metadata.hermes.config` resolves declared defaults and configuration values, expands environment references, and inserts the results into the agent message. Managed packages must use a strict frontmatter allowlist and reject these credential/config declarations unless a separately reviewed adapter explicitly supports them. Connection credentials belong in the verified credential client, not in skill metadata. Keep `skills.inline_shell: false`; enabling it runs embedded commands through `bash -c` and inserts captured output into skill content. [Credential readiness](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/skills_tool.py#L410), [config expansion](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/skill_utils.py#L701), [message injection](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/skill_commands.py#L180), [shell preprocessing](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/skill_preprocessing.py#L89).

The package manifest must enumerate every distributed file, including support files not exposed in the default linked-file listing. That listing treats `references`, `templates`, `assets` and `scripts` differently: references and scripts are not recursively listed, and `examples` is not a standard linked-file group. Explicit file loading accepts a contained relative path. Discovery prunes the four support directory names beneath a skill root, but not `examples`; a nested `examples/.../SKILL.md` can be discovered as another skill. Require exactly one root `SKILL.md` per managed package and reject nested entry points. Do not use the reserved `_org` directory, which has upstream-native active-organization gating. [Linked-file listing](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/skills_tool.py#L360), [contained file loading](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/skills_tool_plugin.py#L68), [support-directory pruning and organization gating](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/skill_utils.py#L736).

Do not promise immediate refresh in a running agent. The tool-list cache has a 30-second TTL, but the system-prompt cache is keyed by directories and configuration rather than package file modification times. Existing in-process entries can return before the disk snapshot is checked. Upstream's own hub and web operations explicitly invalidate their prompt cache; an external file replacement does not invoke those operations. Activate a complete validated package atomically while the managed session is stopped, then verify it in a new Hermes process. Do not couple the companion to internal cache-clearing or compatibility-shim functions. [Tool-list cache](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/skills_tool.py#L37), [prompt cache](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/prompt_builder.py#L1333), [hub invalidation](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/hermes_cli/skills_hub.py#L116).

On 2026-09-11, isolated synthetic checks using Python 3.11 and this pinned source passed for prefixed frontmatter parsing, local/external root iteration, external-directory resolution, support-directory pruning, discovery of a nested `examples/.../SKILL.md`, and expansion of a fictional environment value in declared skill configuration. These checks imported the lightweight upstream metadata utilities; they did not start an agent, exercise `skill_view`, run a package script or call a provider.
# Managed relay and metadata observer validation

At the same pinned commit, `HERMES_MANAGED_DIR` selects an existing directory whose `config.yaml` is deep-merged over user config. This is a supported configuration layer, separate from `HERMES_HOME` or account profiles. MyHermes uses a temporary secret-free overlay for its named custom provider, retry controls and explicit plugin opt-in. `providers.<name>.key_env` is resolved from the process environment by `runtime_provider_custom.py`. The dotenv loader reads the home `.env`/`.op.env` and repository `.env`, and can hydrate `secrets` before applying the managed layer; there is no supported blanket dotenv skip. Managed startup rejects those dotenv sources and external-secret configuration rather than invent an override.

The official `post_tool_call` plugin event provides `tool_name`, `duration_ms`, and `status` alongside sensitive arguments/results. Crucially, `PluginDispatchMixin._invoke_hook_callback` inspects a callback's signature and filters undeclared fields unless it accepts `**kwargs`. MyHermes's observer declares only `tool_name`, `duration_ms`, `status`, converts names to fixed categories, and emits no names/content. Statuses observed in `model_tools.py` are `ok`, `error`, and `blocked`; durations use monotonic elapsed milliseconds. Some agent-loop-only tools bypass this dispatcher; this hook is not a completeness guarantee for all activity.

Verified on macOS/Python3.11 against the installed pinned source: actual `load_config` and `resolve_runtime_provider` resolve the temporary provider's environment token/base URL with `chat_completions`; main retry count is1 and auxiliary transient retries0. Actual plugin discovery and dispatch invoke the observer while undeclared argument/result/error fields contain objects that raise if inspected. Only normalized category/outcome/duration reach the local callback. See [RELAY_BRIDGE.md](RELAY_BRIDGE.md) for executable tests and deliberate runtime limits.

## Actual memory tool and managed session exit

The pinned [`MEMORY_SCHEMA` and registration](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/memory_tool.py#L215) expose the `memory` tool in toolset `memory`. Its required `target` is `memory` or `user`; `action` accepts `add`, `replace` or `remove`, with `content`/`old_text`, or a batch `operations` array. Built-in memory and user profile flags default to enabled. The optional write-approval gate is off unless configured; an owner who enabled it can require approval, and synchronization does not bypass that upstream gate.

[`MemoryStore._mutate` and `add`](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/memory_tool_store.py#L190) perform the locked disk reread and write described above. A model tool result alone is insufficient evidence of synchronization: the companion must capture the resulting local files after the actual child process exits, publish the causal update and allow a separate environment to pull it.

`tests/test_hermes_memory_sync.py` is an opt-in integration for that exact sequence. On macOS/Python3.11, a real pinned `hermes chat --oneshot -t memory` receives two synthetic SSE tool calls, writes both files through the official tool implementation, returns two successful tool results to the synthetic provider and exits. The actual `start` path then synchronizes both files; an independent home/state receives byte-identical copies. The test also sees the real plugin's normalized metadata callbacks; the existing generic mapping classifies this tool as `other`, not a new unpublished category. Companion JSON contains neither memory entry.

Only the controlling-terminal input/output is replaced with fixed fictional stdin/captured fixture output. Provider authentication uses the public DPoP bridge fixture and persona storage uses the explicit in-memory contract peer; this test does not prove Cloudflare persistence or live model behavior. No memory-tool/store/registry implementation or Hermes child is mocked. Existing oneshot and skill-discovery tests passed alongside this case on macOS (3 tests), and the shared relay fixture's 21 tests passed after its synthetic tool-response extension.

The same memory-tool/session-exit test subsequently passed in a disposable Ubuntu24.04.4/aarch64 container with Python3.12.3 and a fresh official pinned runtime installation. This used only Public source and synthetic fixture data; the container was removed afterward. [OS_CHECKS.md](OS_CHECKS.md) records the distinct ordinary suite, explicit native-store tests, upstream probes and artifact-copy failure corrected before the successful run.

```sh
MYHERMES_TEST_UPSTREAM=/absolute/path/to/hermes-agent .venv/bin/python -m unittest discover -s tests -p test_hermes_memory_sync.py -v
```

Primary sources: [managed scope](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/hermes_cli/managed_scope.py), [custom provider credentials](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/hermes_cli/runtime_provider_custom.py#L110), [dotenv loader](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/hermes_cli/env_loader.py#L307), [post-tool event](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/model_tools.py#L615), [callback filtering](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/hermes_cli/plugins_dispatch.py#L152).

## Shared memory and the current execution environment

Managed-launch acceptance: retain the owner's existing environment hint (nonblank `HERMES_ENVIRONMENT_HINT` takes precedence over `agent.environment_hint`), append current-installation guidance only to the child's process environment, and leave SOUL, memory and owner configuration bytes unchanged. Verify the combined hint with the pinned official `agent.prompt_builder.build_environment_hints`, including a remote-terminal case; never print the hint or send it to monitoring.

The hint identifies the current installation and Hermes host OS, treats synchronized OS/path descriptions as past observations, and asks Hermes to verify the actual terminal backend, OS and path before operations. Environment-specific memory should include its source installation and OS. The host identity does not replace the official terminal-backend context. This is model guidance, not an execution sandbox or enforced OS-compatibility test; package platform capabilities remain outside this change. `myhermes history` already returns validated per-file-update `installation_id` metadata; inspect older pages with `--before CURSOR`. This is file-level last-writer history, not automatic provenance for each individual memory entry. Existing owner hints can contain private information and, like the resulting prompt context, pass to the selected inference provider; they are not new company-monitoring fields.

The official implementation appends an embedder hint after its independently generated local-host or remote-backend block. [Environment-hint construction](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/prompt_builder.py#L991), [runtime prompt placement](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/system_prompt.py#L632).

On 2026-09-11, the macOS/Python 3.11 runtime suite passed all 34 groups after this addition. Four environment-hint groups cover exact owner-text precedence, config fallback, private process-only lifetime, invalid hint rejection, and the actual pinned prompt builder with local and simulated remote-backend observations. SOUL, memory and owner-config preservation are asserted; fixture stdout and monitoring remain empty. The remote case mocks only the backend probe and does not connect to an SSH host.

The same four environment-hint groups then ran without skips in the latest disposable Ubuntu24.04.4/aarch64/Python3.12.3 verification. The official inline-shell/config/plugin group ran11 tests, oneshot1 and actual-memory/session-exit1, all without skips or failures. The ordinary source suite separately ran268 tests (259 passed,9 explicit opt-in skips); Ruff and the separate2 native-store checks passed. The container was removed and its absence verified. See [OS_CHECKS.md](OS_CHECKS.md) for the remaining distinction between executed and skipped opt-in checks.

## YAML header boundary regression

The pinned `agent.skill_utils.parse_frontmatter` treats bare CR and Unicode NEL/LS/PS as YAML line breaks. A raw two-line check alone can therefore admit extra credential-related frontmatter fields embedded inside a description. Both public validators now reject those characters within the two logical header lines while preserving standard LF/CRLF and Unicode body text. Synthetic pre-fix tests reproduced the extra parsed field; post-fix Python/Core tests reject the package before readiness processing. The actual pinned parser probe invokes metadata parsing only: it never requests credentials, registers credential files or runs a script. See [skill package validation](SKILL_PACKAGES.md).

A separate 79-case synthetic description audit compared both public validators with the pinned parser, `skills_list` and `skill_view(preprocess=False)`. YAML interprets a TAB before `#` as a comment separator: seven previously accepted boolean/null spellings then became non-string descriptions and were omitted by `skills_list` (the system prompt index instead stringifies truthy values and leaves false/null descriptions empty). Both validators now reject plain comments after either space or TAB. Regression tests preserve ordinary TAB text, Japanese, adjacent hashes, quoted comments, and existing colon descriptions. No additional frontmatter keys or credential requirements appeared in this audit; it made no provider requests and did not run scripts. [List description handling](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/skills_tool.py#L215), [prompt normalization](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/agent/skill_utils.py#L718).
