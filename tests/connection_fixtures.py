"""Synthetic HTTP/provider and company-contract fixtures; never use real credentials."""

from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import urllib.parse
import urllib.request
import uuid

from myhermes.api import NoRedirect
from myhermes.connection_schema import sha256
from myhermes.errors import CompanionError, OfflineError
from myhermes.github import API_ORIGIN, API_VERSION, GitHubClient


def token_for(account_id):
    return "github_pat_" + "synthetic_fixture_" + str(account_id) + "_" * 20


def template():
    return {
        "schema_version": "1",
        "template_id": "github-read",
        "version": "1.0.0",
        "display_name": "Synthetic GitHub read access",
        "description": "Test fixture; not a real account.",
        "connector_id": "github",
        "connector_version": "1.0.0",
        "service": {"base_url": API_ORIGIN, "api_version": API_VERSION, "allowed_hosts": ["api.github.com"]},
        "auth": {"method": "github_fine_grained_pat", "required_permissions": ["metadata:read", "issues:read"]},
        "supported_os": ["ubuntu", "macos"],
        "input_fields": [{"name": "resources", "type": "github_repository_list", "required": True}],
        "setup": {"procedure_id": "github-fine-grained-pat-v1", "procedure_version": "1.0.0"},
        "connection_test": {
            "procedure_id": "github-read-test-v1",
            "required_capabilities": ["repository.read", "issues.read"],
        },
        "capabilities": ["repository.read", "issues.read"],
        "resource_rules": [{"owner": "*", "repository": "*"}],
        "related_skills": [],
        "usage_notice": {
            "version": "1",
            "text": "Use only the selected resources for permitted work. Other users receive no access.",
        },
    }


class MemoryKeyring:
    def __init__(self):
        self.values = {}
        self.unavailable = False

    def get_password(self, service, key):
        if self.unavailable:
            raise RuntimeError("synthetic keyring locked")
        return self.values.get((service, key))

    def set_password(self, service, key, value):
        if self.unavailable:
            raise RuntimeError("synthetic keyring locked")
        self.values[(service, key)] = value

    def delete_password(self, service, key):
        self.values.pop((service, key), None)


class SyntheticTerminal:
    def __init__(self, token, *, accepted=True):
        self.token, self.accepted = token, accepted
        self.confirmations = 0
        self.prompts = 0

    def describe(self, template, context):
        self.prompts += 1

    def read_token(self):
        return self.token

    def confirm(self, template, account, context):
        self.confirmations += 1
        if not self.accepted:
            raise CompanionError("authorization_cancelled", "Synthetic cancellation", 3)


class CompanyFixture:
    def __init__(self):
        self.person_id = str(uuid.uuid4())
        self.template = template()
        self.connections = {}
        self.grants = {}
        self.bindings = {}
        self.receipts = {}
        self.installations = []
        self.enabled = True
        self.denied = False
        self.offline = False
        self.lose_once = None
        self.requests = []
        self.lock = threading.RLock()

    def device(self, installation_id):
        if installation_id not in self.installations:
            self.installations.append(installation_id)
        fixture = self

        class API:
            def request(self, method, path, payload=None):
                return fixture.request(installation_id, method, path, payload)

        return API()

    def request(self, installation_id, method, path, payload):
        with self.lock:
            if self.denied:
                raise CompanionError("access_denied", "Synthetic company denial", 5)
            if self.offline:
                raise OfflineError()
            self.requests.append((installation_id, method, path, copy.deepcopy(payload)))
            if path.startswith("/v1/templates/github-read/versions/"):
                if not self.enabled:
                    raise CompanionError("server_rejected", "Synthetic template disabled", 5)
                return 200, {"template": copy.deepcopy(self.template), "sha256": sha256(self.template)}
            if path == "/v1/templates":
                return 200, {
                    "templates": [
                        {
                            key: self.template[key]
                            for key in ("template_id", "version", "display_name", "connector_id", "connector_version")
                        }
                        | {"published_at": "2026-09-11T00:00:00Z"}
                    ],
                    "next_cursor": None,
                }
            if method == "GET" and path == "/v1/connections":
                return 200, {"connections": [self.metadata(key) for key in self.connections], "next_cursor": None}
            if method == "GET" and path.startswith("/v1/connections/"):
                detail = self.detail(path.split("/")[3])
                detail["bindings"] = [b for b in detail["bindings"] if b["installation_id"] == installation_id]
                return 200, detail
            if payload and payload.get("request_id") in self.receipts:
                original_method, original_path, original_payload, result = self.receipts[payload["request_id"]]
                if (method, path, payload) != (original_method, original_path, original_payload):
                    return 409, {"error": "request_id_reused"}
                return copy.deepcopy(result)
            if path == "/v1/connections" and method == "POST":
                if not self.enabled:
                    raise CompanionError("server_rejected", "Synthetic template disabled", 5)
                connection_id = str(uuid.uuid4())
                self.connections[connection_id] = copy.deepcopy(payload)
                self.grants[connection_id] = {
                    "connection_id": connection_id,
                    "revision": 1,
                    "operations": payload["usage_grant"]["operations"],
                    "resources": payload["usage_grant"]["resources"],
                    "notice_version": payload["usage_grant"]["notice_version"],
                    "notice_sha256": sha256(self.template["usage_notice"]),
                    "accepted_at": "2026-09-11T00:00:00Z",
                    "created_by_installation_id": installation_id,
                }
                result = (201, {"connection_id": connection_id, "grant_revision": 1})
            elif path.endswith("/binding") and method == "PUT":
                connection_id = path.split("/")[3]
                if payload["grant_revision"] != self.grants[connection_id]["revision"]:
                    return 409, {"error": "stale_grant"}
                if self.bindings.get((connection_id, installation_id), {}).get("revoked_at"):
                    raise CompanionError("server_rejected", "Synthetic binding revoked", 5)
                self.bindings[(connection_id, installation_id)] = {
                    key: copy.deepcopy(value)
                    for key, value in payload.items()
                    if key not in ("schema_version", "request_id")
                } | {"updated_at": "2026-09-11T00:00:00Z", "revoked_at": None}
                result = (
                    200,
                    {
                        "connection_id": connection_id,
                        "installation_id": installation_id,
                        "status": payload["status"],
                        "grant_revision": payload["grant_revision"],
                    },
                )
            else:
                raise AssertionError((method, path))
            self.receipts[payload["request_id"]] = (method, path, copy.deepcopy(payload), copy.deepcopy(result))
            if self.lose_once == (method, path):
                self.lose_once = None
                raise OfflineError()
            return result

    def metadata(self, connection_id):
        source = self.connections[connection_id]
        return {
            key: copy.deepcopy(source[key])
            for key in (
                "template_id",
                "template_version",
                "account_kind",
                "display_name",
                "account",
                "management",
                "project",
                "resources",
            )
        } | {
            "connection_id": connection_id,
            "person_id": self.person_id,
            "connector_id": "github",
            "connector_version": "1.0.0",
            "grant_revision": self.grants[connection_id]["revision"],
            "created_at": "2026-09-11T00:00:00Z",
            "updated_at": "2026-09-11T00:00:00Z",
            "revoked_at": source.get("revoked_at"),
            "evidence_source": "client_reported",
            "effective_status": "revoked"
            if source.get("revoked_at")
            else "active"
            if self.enabled
            else "template_disabled",
        }

    def detail(self, connection_id):
        grant = self.grants[connection_id]
        bindings = []
        for installation_id in self.installations:
            binding = copy.deepcopy(
                self.bindings.get(
                    (connection_id, installation_id),
                    {
                        "grant_revision": grant["revision"],
                        "status": "needs_auth",
                        "verified_account_id": None,
                        "requested_permissions": [],
                        "tested_capabilities": [],
                        "tested_resources": [],
                        "error_code": None,
                        "updated_at": None,
                        "revoked_at": None,
                    },
                )
            )
            binding |= {
                "connection_id": connection_id,
                "installation_id": installation_id,
                "evidence_source": "client_reported",
                "effective_status": "revoked"
                if binding["revoked_at"]
                else "stale"
                if binding["grant_revision"] != grant["revision"]
                else binding["status"],
            }
            bindings.append(binding)
        return {"connection": self.metadata(connection_id), "usage_grant": copy.deepcopy(grant), "bindings": bindings}


@contextmanager
def github_http_fixture():
    state = {"requests": [], "mode": "normal", "tokens": {token_for(n): n for n in range(101, 110)}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            identity = state["tokens"].get(token)
            state["requests"].append(
                {"path": self.path, "account_id": identity, "version": self.headers.get("X-GitHub-Api-Version")}
            )
            path = urllib.parse.urlsplit(self.path).path
            status, value, headers = 200, {}, {}
            if identity is None:
                status, value = 401, {"message": "Credential " + token}
            elif state["mode"] == "redirect":
                status, value, headers = 302, {}, {"Location": "https://untrusted.example/steal"}
            elif state["mode"] == "denied":
                status, value = 403, {"message": "Secret " + token}
            elif state["mode"] == "rate_limit":
                status, value, headers = 403, {}, {"X-RateLimit-Remaining": "0"}
            elif state["mode"] == "invalid_json":
                value = None
            elif path == "/user":
                value = {
                    "id": identity,
                    "login": "fixture-user-" + str(identity),
                    "email": "private@example.invalid",
                    "token": token,
                }
            elif path.endswith("/issues") or "/issues/" in path:
                item = {
                    "number": int(path.rsplit("/", 1)[1]) if "/issues/" in path else 1,
                    "state": "open",
                    "title": "Private issue for " + str(identity),
                    "body": "Synthetic issue body; reflected credential " + token,
                    "html_url": "https://untrusted.example/ignored",
                }
                value = item if "/issues/" in path else [item]
                headers["Link"] = '<https://untrusted.example/steal>; rel="next"'
            else:
                name = path.removeprefix("/repos/")
                value = {
                    "id": identity * 10,
                    "full_name": "wrong/resource" if state["mode"] == "wrong_resource" else name,
                    "private": True,
                    "default_branch": "main",
                    "description": "Private description " + str(identity),
                    "html_url": "https://untrusted.example/ignored",
                }
            raw = token.encode() if value is None else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))

    class FixtureOpener:
        def open(self, request, timeout):
            parsed = urllib.parse.urlsplit(request.full_url)
            if parsed.scheme != "https" or parsed.netloc != "api.github.com":
                raise AssertionError("Unexpected production request origin")
            rewritten = urllib.request.Request(
                "http://127.0.0.1:"
                + str(server.server_port)
                + parsed.path
                + ("?" + parsed.query if parsed.query else ""),
                headers=dict(request.header_items()),
                method=request.method,
            )
            return opener.open(rewritten, timeout=timeout)

    state["client_factory"] = lambda token: GitHubClient(token, opener=FixtureOpener())
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
