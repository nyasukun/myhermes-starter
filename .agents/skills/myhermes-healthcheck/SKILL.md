---
name: myhermes-healthcheck
description: Check the external HTTP reachability and unauthenticated routing of an approved deployed MyHermes server from an employee workstation.
---

# MyHermes external health check

Use this skill when an employee asks whether a deployed MyHermes service is reachable. The same instructions work for any MyHermes deployment; never assume a company domain. This is a small, on-demand synthetic check, not continuous monitoring or a full application/dependency health assessment.

## Run the check

1. Resolve the service origin in this order:
   - Use an origin explicitly given in the current request.
   - Otherwise, use a clearly labeled MyHermes portal/server entry already present in the user's Memory context. If that context is not available, inspect only the user's configured MyHermes memory files (`$HERMES_HOME/memories/USER.md` and `MEMORY.md`, or the standard `$HOME/.hermes/memories/` location when no home is configured) when accessible in the current authorized workspace. Read only matching service-entry lines; do not scan or quote unrelated memories.
   - An approved local config or runbook can supply the origin when the user points to it. If no source identifies one unambiguous MyHermes service, ask the user; do not guess from the repository, Git remote, or another deployment.
2. Use the HTTPS origin only, for example `https://service.example.com`. If the saved portal URL has a path, take only its scheme and host when it identifies the same MyHermes deployment. Do not pass a path, query, fragment, username, password, token, or other credential to the probe. If portal and API origins appear different or ambiguous, ask which deployment to check.
3. If the user wants endpoint details saved to Memory, suggest a small labeled entry such as `MyHermes service origin: https://service.example.com` and `MyHermes portal: https://service.example.com/`. Do not write Memory or local configuration unless the user asks. Never save access tokens, cookies, enrollment codes, or other credentials there.
4. Run the bundled probe once, passing the origin as one quoted argument:

   ```sh
   python3 /absolute/path/to/myhermes-healthcheck/scripts/check_public.py --origin 'https://service.example.com'
   ```

   Resolve the script path from this skill's directory. It makes four HTTPS `GET` requests with an eight-second per-request timeout, does not follow redirects, and sends no application credentials or cookies.
5. Report the JSON summary and explain that the check covers only the unauthenticated edge and Worker routing. A successful result does not verify employee login, D1/R2/Durable Object operations, sync, relay providers, or the authenticated UI. Do not include unrelated Memory content in the report.

## Boundaries

- The probe requests only `/`, `/guide.html`, `/v1/me`, and `/llm/v1/models`. It performs no writes, account operations, Cloudflare control-plane requests, retries, or authenticated calls.
- Never add tokens, cookies, user identity, private data, or request headers. Do not run a browser session or use employee account state.
- The probe prints status codes, durations, and fixed result labels only. Do not fetch or report response bodies, headers, redirect destinations, cookies, logs, or credentials. The API response body is inspected only in memory to recognize the fixed `access_auth_required` code.
- `healthy` means the portal routes redirect to an access login (`302`) and both API routes reach the application and return the expected unauthenticated `401 access_auth_required`. Any other result is `unhealthy`; show the affected path, status or sanitized network error, then stop. Do not retry automatically.
- Do not expose an unexpected redirect destination or response contents while explaining a failure. Ask an operator to investigate configuration or service logs separately.
