---
name: setup-myhermes
description: Set up and verify a MyHermes companion and its pinned Hermes runtime on Ubuntu or macOS.
---

# setup-myhermes

Use when the user asks to install MyHermes or prepare a new runtime environment.

Inputs: an approved server origin, independent state directory, independent Hermes home, runtime checkout path, and an environment label. These are not account-kind profiles.

1. Read `../../../docs/COMPANION.md` relative to this skill directory and `../../../docs/SANDBOX.md` for the supported pin, OS prerequisites, local Docker requirement and exact digest-pinned image pull command. Before a live start, verify the local Docker daemon and shared Hermes-home location and fetch that public image within the authorized setup scope. Inspect existing directories and preserve user changes. Locate the installed `myhermes` executable (a repository virtual environment may provide `.venv/bin/myhermes`). If absent, use the reviewed repository's `../../../docs/USER_GUIDE.md` installation steps; do not assume an unpublished package exists on PyPI. Use that verified executable consistently in every command below.
2. Run the published `myhermes --state-dir STATE setup --server ORIGIN --hermes-home HOME --upstream RUNTIME --label LABEL --dry-run` using individually quoted arguments. Then run that same setup without `--dry-run` when the user's request authorizes local setup.
3. Run `myhermes --state-dir STATE install-runtime --python python3.11 --dry-run`, then install the supported pin within the authorized scope. Do not substitute a template-supplied shell command or unverified upstream version.
4. Run `myhermes --state-dir STATE inspect` and `myhermes --state-dir STATE start --dry-run`.

Failure/recovery: fix reported prerequisites and rerun; use separate homes for different installations. A Python other than 3.11–3.13 cannot install the pinned upstream. Never erase an existing home to make setup pass.

Completion: setup binding and pinned-runtime verification succeed. Report real upstream startup separately; `start --dry-run` is not a successful live session. Enrollment is a separate owner-browser action.
