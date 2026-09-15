# Managed Docker sandbox

`myhermes start` and `myhermes start --offline` use Docker for Hermes terminal and `execute_code` execution. Ordinary file tools use that same backend. A missing or failed Docker backend never selects the host `local` backend. The launcher supplies this policy through the process environment and temporary managed configuration; the owner's `config.yaml` bytes remain unchanged.

`myhermes start --dry-run` reports `terminal_backend: docker`, `sandbox_required: true`, and `workspace: /workspace`. It does not start Docker or prove that its daemon, image or mounts are ready.

## Install and startup

Use a working local Docker installation with Linux containers: Docker Desktop on macOS or Docker Engine on Ubuntu. A local VM such as Colima must share the selected Hermes home. Prefer the normal user-home location when it is shared with Docker. Remote contexts and TCP/SSH `DOCKER_HOST` endpoints are unsupported.

Pull the public image once in the owner's native terminal:

```sh
docker pull nikolaik/python-nodejs:python3.11-nodejs20@sha256:8f958bdc1b4a422bfafd97cab4f69836401f616ae985d4b57a53d254f5bcb038
```

This is the pinned upstream's Python 3.11 / Node.js 20 image, additionally fixed to the manifest digest inspected on 2026-09-15. Changing the digest requires another compatibility check. Startup does not download an image; an absent image returns `runtime_sandbox_image_missing` with the pull command.

Before starting Hermes or its relay, the companion checks the Linux daemon and local Unix socket context. A short `--rm --network=none --read-only` container then receives only a fresh random nonsecret marker directory, mounted read-only. Its output must match that marker. This detects unshared VM paths that Docker `-v` would otherwise silently create inside the VM: a real Colima test reproduced this with `/private/tmp`. No owner files or credentials participate in the probe. The probe has a unique generated container name and a bounded exact-name cleanup in `finally`, including timeout/interruption paths. Subprocess diagnostics are suppressed. Docker probes have 15-second time limits, and the bind check has a 30-second time limit.

The normal start command remains `myhermes start`. Hermes' controller and its personality/session data remain on the host.

## Files and persistence

| Location | Meaning |
| --- | --- |
| Sandbox `/workspace` | Normal shell, code and working-file location. |
| `$HERMES_HOME/myhermes-sandboxes/docker/<task>/workspace` | Dedicated host directory backing that workspace. |
| `$HERMES_HOME/myhermes-sandboxes/docker/<task>/home` | Dedicated directory backing sandbox `/root`; not the owner's real OS home. |
| Original `$HERMES_HOME` | Host-side persona, sessions, configuration and installed skills; the whole directory is not mounted. |

The pinned runtime chooses `<task>` (`default` or a sanitized session key). Do not infer the active workspace from modification times. While the session is active, select its container using `docker ps --filter label=hermes-agent=1`, then inspect only its workspace mount:

```sh
docker inspect --format '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{println .Source}}{{end}}{{end}}' CONTAINER_ID
```

Confirm the returned directory belongs beneath the configured `myhermes-sandboxes/docker/` root. Use ordinary native-terminal file copying for selected files, or `docker cp` with the selected container and an explicit `/workspace/...` path. Do not copy credential stores, companion state or the whole home. Dedicated workspace/home files survive normal container removal and stay local; they are not automatically synchronized. Files elsewhere in the container and globally installed packages can disappear at cleanup. A different session can use another workspace.

## Isolation boundary

Managed settings disable host-working-directory mounts, additional volumes, arbitrary Docker flags, forwarded environment variables, Docker Snap compatibility reductions, cross-process container reuse and the general orphan reaper. Upstream supplies its normal capability restrictions and `no-new-privileges`. Network access inside workload containers remains enabled; there is no egress allowlist.

Upstream intentionally mounts installed local skills and selected cache/attachment directories read-only so supplied skill scripts and attachments are usable. Known current/legacy automatic mount roots and their parents must not be symlinks; startup checks their paths without reading content. Distributed MyHermes packages cannot declare credential/environment passthrough hooks. Owner-installed third-party skills and plugins remain trusted extensions requiring separate review; this adapter is not a security boundary against arbitrary plugins or a compromised host controller.

Resource requests are 2 CPUs, 4096 MiB memory (`--memory 4096m`) and 51200 MiB disk. Upstream can omit CPU/memory/PID limits if cgroup controllers are unavailable, and disk quotas depend on the storage driver. These settings are not universally enforced quotas. The bind probe itself requires Docker's PID-limit flag to work.

The managed controller receives a 30-second shutdown grace period, covering the upstream Docker stop/remove and cleanup drain. Normal shutdown requests removal of the owned container while retaining workspace/home files. A crash, forced kill or unavailable daemon can leave a container behind; inspect its identity before manual cleanup. MyHermes does not sweep unrelated owner containers.

This policy covers terminal, `execute_code` and ordinary file operations. Hermes itself, its provider/relay client, memory tools, skill discovery/management, plugins and other host integrations are not all moved into Docker. Direct upstream launches bypass companion policy. Cloudflare hosts the control plane; each employee's computer hosts their Docker sandbox.

## Existing environments and host companion operations

Existing local-backend, mount and Docker-flag settings are overridden only for managed launches. No local-execution opt-out is exposed. Nonempty `terminal.env_passthrough`, `terminal.credential_files` or `terminal.docker_env` stop startup with `runtime_sandbox_passthrough_unsupported`: upstream reads the first two from raw owner YAML, and deep-merging retains keys from the third. Nonempty `skills.external_dirs` or `skills.trusted_project_dirs` is also rejected (`runtime_sandbox_external_skills_unsupported`) because upstream otherwise mounts external or trusted-project directories (including resolved symlink targets); use the validated company/personal packages under the selected Hermes home. The owner must back up and remove those settings explicitly. Hermes home paths containing colons, commas or line breaks are rejected because of Docker's mount syntax.

The companion CLI and native Keychain/Secret Service remain on the host. Run `myhermes connections ...`, `skills import`, `skills derive`, `skills sync`, enrollment and account authorization in the owner's native terminal. Shipped Hermes skills prepare packages/commands for that owner operation. This release has no sandbox-to-host companion bridge. Do not install the companion inside Docker, mount its state or credential store, or switch execution to the host as a workaround.

## Validation

On 2026-09-15, macOS ARM64 with local Colima Docker and the installed pinned upstream passed the actual Docker fixture: terminal, `execute_code`, `write_file` and `read_file` shared one container; an out-of-mount host canary was absent; the relay bearer was absent from container environment and Python execution; mounts, network mode, privilege settings and `no-new-privileges` were checked; both code/file artifacts remained on the real host after cleanup; and the owned container was removed. The fixture made no real model/provider request and used synthetic data only. This is a macOS Docker verification, not an Ubuntu Docker-runtime claim.

Run from this repository in a Docker-shared location:

```sh
MYHERMES_TEST_UPSTREAM=/absolute/path/to/hermes-agent MYHERMES_TEST_DOCKER=1 \
  .venv/bin/python -m unittest discover -s tests -p test_runtime_sandbox.py -v
```

Ordinary tests mock admission; real-container execution requires the explicit Docker opt-in. Full Hermes oneshot, memory-sync and discovery tests also require that opt-in because managed startup now requires Docker. Their private synthetic homes live beneath the repository for VM sharing and are removed afterward. The Ubuntu compatibility-test container has no nested daemon, Docker socket or host mounts, so it covers companion/imported-upstream policy checks rather than actual Docker operation.

Pinned sources: [configuration bridge](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/hermes_cli/config.py), [Docker backend](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/environments/docker.py), [backend selection](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/terminal_tool_backends.py), [code execution](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/code_execution_tool.py), [file tools](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/file_tools.py), [skill/cache mounts](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/tools/credential_files.py).
