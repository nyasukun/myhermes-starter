# GitHub connections — M2 acceptance and implementation

Status: implemented with connector tests, including actual HTTP requests to a synthetic loopback GitHub fixture. Acceptance criteria were written before implementation. Live GitHub authorization and the real credential store remain separate from synthetic terminal checks. The versioned API/template source of truth is [M2 connection contract](M2_CONNECTION_CONTRACT.md).

## Acceptance criteria

1. The authenticated companion fetches an exact immutable template ID/version, rejects unknown fields and unsupported connector/version/auth/host/operation combinations, and never evaluates a template string as shell.
2. A real native-terminal onboarding flow obtains a fine-grained PAT without echo, command arguments, stdout, environment variables, files or Git; validates the GitHub identity; tests every requested repository/capability; and stores the credential only in the native OS keyring.
3. A metadata-only server connection, environment-specific credential binding and explicit usage grant are separate records. Registration records the notice version, accepted operations/resources and server timestamp. The owner is derived from authenticated requests, not client-supplied IDs.
4. One company, two client and two personal connections can coexist and be selected by `connection_id` plus a concrete resource. Concurrent operations cannot change another operation's account or inherit a global GitHub login. Personal connections do not introduce additional approval for each allowed read.
5. A new environment sees the same logical connections with a missing/needs-auth binding until its owner authenticates there. No credential is synchronized with personality or copied between environments. Reauthentication must match the connection's original provider account ID.
6. Before using a stored token, the companion refreshes owner authorization/connection metadata and checks the current operation/resource grant, template status and this installation's binding. Revoked/disabled connections or installations and out-of-grant resources fail closed; an unavailable authorization service does not permit an offline remote read.
7. Reads use actual bounded HTTPS requests to fixed GitHub REST endpoints for repository metadata and issues. Redirects, path traversal, arbitrary URLs, response-provided pagination hosts, unbounded responses and unsupported methods are rejected. Returned GitHub content never goes to company metadata or telemetry APIs.
8. Onboarding metadata writes survive lost responses through persistent request IDs; retries cannot create duplicate logical connections or lose track of a saved native credential. A failed or interrupted flow is inspectable and resumable without printing tokens.
9. Connection diagnostics and onboarding JSON contain metadata only. Content output is explicit for an owner-requested read; raw upstream errors and headers are never echoed. Real authorization/startup stays outside Codex-captured terminals and PTYs.
10. Automated tests use synthetic credentials with a local HTTP fixture and the production request construction/parser/selection paths. Tests cover multiple simultaneous accounts, missing binding, identity mismatch, grant revocation, malicious templates/resources/redirects, denied access, rate limits, lost responses, redaction and cleanup. Real GitHub grants and native-keyring authorization remain separately reported and are not performed by automated fixtures.
11. An isolated synthetic controlling-terminal test must open the actual `/dev/tty`, accept hidden input and Unicode notices, restore terminal settings after success, cancellation, invalid input and interruption, and never echo its synthetic token. This verifies terminal mechanics only; real authorization still belongs in the owner's uncaptured terminal.
12. Secret input must temporarily require canonical line input even when the original terminal is noncanonical. Pasted extra lines must not approve the later notice. Confirmation accepts the complete line `yes` only; long or padded prefixes are rejected. SIGINT, SIGTERM and SIGHUP must restore original terminal attributes and signal handlers before interruption. A non-main-thread caller must fail before changing echo. Unhandleable SIGKILL and loss of the terminal device are outside this restoration guarantee.

## Verified GitHub authentication choice

Checked 2026-09-11 against official GitHub documentation. The initial connector uses a fine-grained personal access token created by the owner for specific repositories of one resource owner. Separate logical connections can use different accounts or resource owners. GitHub organization policy may require token approval, and outside-collaborator limitations can make this method unavailable for some resources; the application must report the failure rather than silently use broader credentials. [GitHub PAT documentation](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

GitHub device authorization is a real supported alternative but requires an existing registered app client ID and device-flow enablement. No such application is assumed or invented here. OAuth/device-flow code is not presented as available in this initial connector. [GitHub OAuth device flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow).

The token is checked with `GET /user`; only the provider's numeric account ID and login are retained. Fine-grained tokens can call that endpoint without additional user permissions. [Authenticated-user endpoint](https://docs.github.com/en/rest/users/users#get-the-authenticated-user).

`GET /repos/{owner}/{repo}` requires repository Metadata read for private resources. [Repository endpoint](https://docs.github.com/en/rest/repos/repos#get-a-repository). Issue read operations use documented repository-issues endpoints and require the corresponding read permission. [Issues endpoints](https://docs.github.com/en/rest/issues/issues#list-repository-issues).

The connector fixes the GitHub API origin and REST API version in public implementation. Template metadata contains requested permissions; successful endpoint probes are reported as client-observed capability tests, **not a claim that GitHub disclosed every permission on the token**. API authorization itself remains GitHub's responsibility.

The pinned REST version is `2026-03-10`; this is an existing GitHub version, not a template-defined future capability. [GitHub API versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions?apiVersion=2026-03-10).

## Commands

Use an enrolled environment. The identifiers below are placeholders chosen from your authenticated template list and returned connection metadata; they are never credentials.

```sh
myhermes --state-dir STATE templates list
myhermes --state-dir STATE templates show TEMPLATE_ID --version VERSION
myhermes --state-dir STATE connections add \
  --template-id TEMPLATE_ID --template-version VERSION \
  --account-kind personal --display-name 'Personal work account' \
  --management-kind individual --management-label 'Owner selected label' \
  --resource OWNER/REPOSITORY --dry-run
```

Run the reviewed `add` command without `--dry-run` **in the owner's own terminal, outside Codex-captured tools and PTYs**. `/dev/tty` is captured when a tool allocates a PTY; it is not a private channel in that situation. Hidden input requires a real controlling terminal, disables echo, and has no stdin fallback. The command displays the fixed PAT creation page, requested resources/permissions, authenticated provider identity and business-use notice before registration. Tokens must never be passed as command arguments, environment variables, copied into conversations or placed in a file. The CLI does not support such inputs.

`--account-kind` accepts company/client/personal. `--management-kind` independently accepts company/client/individual. Repeat `--resource` for exact repositories (maximum 50); optional `--operation` restricts operations. If omitted, operations come from the pinned template. Optional `--project-id` and `--project-label` must be used together. These classifications do not change the Hermes profile or OS credential namespace.

```sh
myhermes --state-dir STATE connections list
myhermes --state-dir STATE connections show CONNECTION_ID
myhermes --state-dir STATE connections test CONNECTION_ID
myhermes --state-dir STATE connections read CONNECTION_ID \
  --operation repository.read --resource OWNER/REPOSITORY
myhermes --state-dir STATE connections read CONNECTION_ID \
  --operation issues.read --resource OWNER/REPOSITORY --issue 42 --include-content
```

The default read output contains resource/issue identifiers, states and canonical GitHub URLs; it excludes issue titles/bodies and repository descriptions. `--include-content` explicitly returns selected content for the owner's requested task, allowing Hermes to use it in the same conversation. `--export-dir NEW_PRIVATE_DIRECTORY` writes that content to a new private JSON artifact instead of stdout. Its result reports an artifact ID. Do not export into Git or shared directories. Provider JSON is projected onto known fields; upstream error bodies, private user fields and response-provided URLs are not echoed. A reflected exact token is redacted from selected content. This redaction does not make arbitrary issue content trustworthy or authorize sharing it.

Issues accept `--page`, `--per-page` (1–100) and `--issue-state open|closed|all`. Pagination is explicit and bounded; response Link hosts are never followed. Reads send only GET requests, and the connector does not provide GitHub writes, repository cloning, arbitrary endpoints or OAuth authorization. The owner-requested operation and repository are checked against the current server grant before any provider request. An account-ID probe then verifies that the locally stored token still belongs to the selected logical account.

For another environment, use the same logical connection ID and run `connections authorize CONNECTION_ID` in that environment's native terminal. Its token remains local and must authenticate the original GitHub account. The token is never synchronized. The native store service is `io.myhermes.github.v1`; keys combine the installation UUID with an independent credential UUID. A template cannot choose the backend, endpoint, executable or shell command. Secret Service and macOS Keychain are the only production backends; a locked or absent backend fails without fallback.

## Interrupted authorization and local removal

```sh
myhermes --state-dir STATE connections pending
myhermes --state-dir STATE connections resume REQUEST_ID
myhermes --state-dir STATE connections cancel REQUEST_ID
myhermes --state-dir STATE connections forget CONNECTION_ID
```

Metadata-only SQLite pending records are committed before saving a credential or sending an API mutation. Creation and binding requests retain exact request IDs and payloads through lost acknowledgements. Resume rechecks current owner access, template status, provider identity and grant. A grant revision change triggers fresh capability tests and a fresh binding request ID. It cannot silently reuse a proof for an older scope. Never rerun `add` as a generic recovery action: it intentionally creates a new logical connection.

Cancel prevents the pending request from activating and removes its unused native credential. Cancelling a capability test does not delete the already active credential shared by that test. If a creation acknowledgement was lost, cancelling can leave a metadata-only logical connection on the server; find it in the owner portal and revoke it there when appropriate. Cancelling does not claim to undo a server mutation whose outcome was lost.

Forget atomically detaches the local binding and cancels outstanding activation records before deleting all associated native keys. A locked native store leaves an inspectable cleanup record, with local CLI use already detached. Resume/forget retries finish key deletion and the metadata-only needs-auth report. The report may remain pending while the company API is offline or denies access; this does not restore local use. When the authenticated API explicitly reports that the connection or this binding is disabled/revoked, local deletion is complete and the unavailable report does not leave a permanent pending cleanup. Replacing a token also journals old-key cleanup, so deletion failure is retryable. New authorization is blocked until the existing request is settled. Local removal cannot undo a request already in flight; server revocation and provider token revocation are separate controls.

Logical connection and per-environment binding revocation use the owner portal. Company policy, grant and membership are checked on each new read, with no offline authorization fallback. A PAT might still be usable by its owner outside this companion; company revocation does not claim to revoke a GitHub-issued credential itself.

## Validation and remaining live checks

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_connections.py' -v
.venv/bin/ruff check src tests
```

Tests create an ephemeral loopback HTTP server, build production fixed-host requests, and route them through an explicitly injected test opener. There is no production CLI or environment switch for alternate GitHub origins. Synthetic credentials remain only in an injected in-memory keyring. The tests verify five concurrent account classifications, independent environments, identity swaps, scoped grants, stale/revoked bindings, disabled templates, denied/rate-limited responses, token redaction, malicious templates and paths, cancelled registration, response loss/restart, durable cleanup and forgotten-token non-resurrection.

The suite does not authorize real accounts, confirm GitHub organization approval, certify all fine-grained PAT limitations, or exercise native credential prompts on Ubuntu/macOS. Those checks require the owner's approved account and target OS. A separate Private M2 cross-language integration passed using the Python connector against the real local Worker and a synthetic GitHub HTTP server: template retrieval, connection creation, ready binding, second-environment needs-auth, wrong-account rejection, and reauthorization all exercised the production wire paths. This is not a real company deployment or live GitHub grant.

Additional macOS controlling-terminal acceptance tests use only a synthetic PAT in a disposable child PTY. The previous `open("/dev/tty", "r+")` mode failed before displaying the prompt because its buffered read/write stream requires seeking. An unbuffered binary device with a UTF-8 text wrapper supports terminal output; bounded input reads use the descriptor directly. The old revision reproduces `terminal_required` on the same isolated PTY.

Independent PTY probes then found that a noncanonical terminal could retain a pasted `yes` in the text wrapper's read-ahead buffer, and that a long line beginning with `yes` and spaces could pass a truncated confirmation check. SIGTERM and SIGHUP also left echo disabled. The common terminal reader now requires the main thread before changing terminal state, temporarily enables canonical input with carriage-return normalization, flushes queued input before and after the prompt, and reads one complete bounded line without text read-ahead. Only the line `yes` is accepted; leading/trailing spaces and excessive lines are rejected. A second confirmation must be entered after its prompt.

Temporary SIGINT, SIGTERM and SIGHUP handlers interrupt the prompt only after its `finally` path restores original terminal attributes and handlers. Signals are blocked briefly during that restoration to prevent a second signal from interrupting cleanup. Unhandleable SIGKILL or loss of the terminal device cannot guarantee restoration. These guarantees cover input mechanics, not authorization through a captured Codex PTY; real authorization still belongs in the owner's own terminal.

On macOS/Python 3.13, the combined real-PTY regression selection reported `Ran 13 tests`, `OK`; it includes eight connection, four identity-confirmation and one runtime-terminal tests. The additional cases first produced 11 failing subcases, then passed after correction. Six independent signal/paste/long-input probes also passed after the fix, preserving hidden input and original attributes. The full connection selection reported `Ran 40 tests in 13.433s`, `OK`, with no skips. No real GitHub account or native credential store was used.

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_connection_terminal.py' -v
```

The bundled Codex skill is `.agents/skills/connect-service/SKILL.md`. The reusable runtime skill source is `hermes-skills/myhermes-connections/SKILL.md`; installing it is explicit and does not grant additional API access. Both use only published CLI commands and explicit connection/resource selection.

## Scalar validation parity

Connection/template text rejects NUL and unpaired Unicode before display or provider authorization. Length limits count Unicode code points, including emoji. Connection request/credential IDs match the public UUID versions1–8 and RFC variant bits; valid existing casing is preserved so native-store entries are not renamed. Nil/max or reserved-version/non-RFC-variant forms are rejected locally. The additional validation and existing connection regressions ran32 tests without failures on macOS/Python3.11 after the change.
