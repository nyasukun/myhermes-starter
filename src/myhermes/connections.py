"""Owner-scoped connection orchestration with durable metadata-only onboarding."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import uuid

from .connection_schema import (
    account,
    assert_grant,
    bounded,
    choices,
    connection_metadata,
    exact,
    identifier,
    parse_detail,
    request_id,
    resources,
    sha256,
    template_response,
    version,
)
from .connection_store import ConnectionStore, GitHubCredentialStore
from .errors import CompanionError
from .files import export_json, file_lock
from .github import CAPABILITIES, GitHubClient, parse_resource, resource_key


def draft_input(args):
    identifier(args.template_id)
    version(args.template_version)
    if args.account_kind not in ("company", "client", "personal") or args.management_kind not in (
        "company",
        "client",
        "individual",
    ):
        raise CompanionError("connection_input_rejected", "Unsupported account or management classification.")
    bounded(args.display_name, 120)
    bounded(args.management_label, 120)
    if bool(args.project_id) != bool(args.project_label):
        raise CompanionError("connection_input_rejected", "Project ID and project label must be supplied together.")
    project = None
    if args.project_id:
        project = {"id": identifier(args.project_id), "label": bounded(args.project_label, 120)}
    selected = resources([parse_resource(value) for value in args.resource])
    if args.operation is not None:
        choices(args.operation, CAPABILITIES)
    return {
        "template_id": args.template_id,
        "template_version": args.template_version,
        "account_kind": args.account_kind,
        "display_name": args.display_name,
        "management": {"kind": args.management_kind, "label": args.management_label},
        "project": project,
        "resources": selected,
    }


class Connections:
    def __init__(
        self,
        config,
        directory: Path,
        *,
        api=None,
        api_factory=None,
        store=None,
        credentials=None,
        github_factory=GitHubClient,
    ):
        self.config = config
        self.directory = directory
        self.api = api
        self.api_factory = api_factory
        self.store = store or ConnectionStore(directory)
        self._credentials = credentials
        self.github_factory = github_factory

    @property
    def credentials(self):
        if self._credentials is None:
            self._credentials = GitHubCredentialStore(self.config.get("installation_id"))
        return self._credentials

    def _request(self, method, path, payload=None):
        if self.api is None:
            if self.api_factory is None:
                raise CompanionError("not_enrolled", "Enroll this installation before connection API access.", 3)
            self.api = self.api_factory()
        status, body = self.api.request(method, path, payload)
        if status == 409:
            if body.get("error") in ("stale_grant", "concurrent_mutation"):
                raise CompanionError(
                    "grant_changed",
                    "The connection grant changed while testing. Retry the pending request against current authorization.",
                    6,
                )
            raise CompanionError(
                "connection_request_conflict",
                "The server rejected this mutation's request ID or current state; no service response was printed.",
                6,
            )
        if status not in (200, 201):
            raise CompanionError(
                "connection_request_rejected", "The company API did not complete this connection operation.", 5
            )
        return body

    def template(self, template_id, template_version):
        identifier(template_id)
        version(template_version)
        result = self._request("GET", "/v1/templates/" + template_id + "/versions/" + template_version)
        template = template_response(result, template_id, template_version)
        if self.config.get("os") not in template["supported_os"]:
            raise CompanionError(
                "connector_os_unavailable",
                "The selected template does not support this environment's operating system.",
                3,
            )
        return template

    def list_templates(self, after=None):
        suffix = "" if after is None else "?after=" + identifier(after)
        result = exact(self._request("GET", "/v1/templates" + suffix), ("templates", "next_cursor"))
        if not isinstance(result["templates"], list) or len(result["templates"]) > 100:
            raise CompanionError("connection_schema_rejected", "Invalid template metadata page.")
        for row in result["templates"]:
            exact(row, ("template_id", "version", "display_name", "connector_id", "connector_version", "published_at"))
            identifier(row["template_id"])
            version(row["version"])
            bounded(row["display_name"], 120)
            bounded(row["published_at"], 64)
            if row["connector_id"] != "github" or row["connector_version"] != "1.0.0":
                raise CompanionError("connector_unavailable", "This client cannot use a listed connector version.", 3)
        if result["next_cursor"] is not None:
            identifier(result["next_cursor"])
        return result

    def list(self, after=None):
        suffix = "" if after is None else "?after=" + request_id(after)
        result = exact(self._request("GET", "/v1/connections" + suffix), ("connections", "next_cursor"))
        if not isinstance(result["connections"], list) or len(result["connections"]) > 100:
            raise CompanionError("connection_schema_rejected", "Invalid connection metadata page.")
        for row in result["connections"]:
            connection_metadata(row)
            if row["person_id"] != self.config.get("person_id"):
                raise CompanionError(
                    "connection_owner_mismatch", "A connection page did not match the authenticated owner.", 5
                )
        if result["next_cursor"] is not None:
            request_id(result["next_cursor"])
        return result

    def detail(self, connection_id):
        request_id(connection_id)
        return parse_detail(
            self._request("GET", "/v1/connections/" + connection_id),
            connection_id,
            self.config.get("person_id"),
            self.config.get("installation_id"),
        )

    def _authorized(self, connection_id, *, require_ready):
        detail = self.detail(connection_id)
        connection, grant = detail["connection"], detail["usage_grant"]
        if connection["effective_status"] != "active" or connection["revoked_at"] is not None:
            raise CompanionError(
                "connection_disabled",
                "This connection or its template has been disabled. No GitHub request was sent.",
                5,
            )
        binding = next(
            (item for item in detail["bindings"] if item["installation_id"] == self.config.get("installation_id")), None
        )
        if binding is not None and (binding["effective_status"] == "revoked" or binding["revoked_at"] is not None):
            raise CompanionError(
                "binding_revoked", "This environment's connection binding is revoked. No GitHub request was sent.", 5
            )
        template = self.template(connection["template_id"], connection["template_version"])
        assert_grant(
            template, connection["resources"], grant["operations"], grant["resources"], grant["notice_version"]
        )
        if grant["notice_sha256"] != sha256(template["usage_notice"]):
            raise CompanionError(
                "notice_integrity_failed", "The accepted notice does not match the pinned template.", 5
            )
        if require_ready:
            if binding is None or binding["effective_status"] not in ("ready",):
                raise CompanionError(
                    "binding_not_ready",
                    "This environment needs authorization or a fresh connection test for the current grant.",
                    3,
                )
            if (
                binding["status"] != "ready"
                or binding["grant_revision"] != grant["revision"]
                or binding["verified_account_id"] != connection["account"]["provider_account_id"]
                or set(binding["tested_capabilities"]) != set(grant["operations"])
                or {resource_key(item) for item in binding["tested_resources"]}
                != {resource_key(item) for item in grant["resources"]}
                or set(binding["requested_permissions"]) != set(template["auth"]["required_permissions"])
            ):
                raise CompanionError(
                    "binding_evidence_mismatch",
                    "The ready binding does not cover the current account and usage grant.",
                    5,
                )
        return detail, template

    def add(self, draft, *, operations, terminal):
        template = self.template(draft["template_id"], draft["template_version"])
        operations = list(template["capabilities"] if operations is None else choices(operations, CAPABILITIES))
        assert_grant(template, draft["resources"], operations, draft["resources"], template["usage_notice"]["version"])
        # Check the backend before asking the owner to enter any credential.
        credentials = self.credentials
        context = {
            "account_kind": draft["account_kind"],
            "management": draft["management"],
            "project": draft["project"],
            "operations": operations,
            "resources": draft["resources"],
        }
        terminal.describe(template, context)
        token = terminal.read_token()
        tested = self.github_factory(token).test_capabilities(draft["resources"], operations=operations)
        account(tested["account"])
        terminal.confirm(template, tested["account"], context)
        pending_id = str(uuid.uuid4())
        payload = {
            "schema_version": "1",
            "request_id": pending_id,
            **draft,
            "account": tested["account"],
            "usage_grant": {
                "notice_version": template["usage_notice"]["version"],
                "accepted": True,
                "operations": operations,
                "resources": draft["resources"],
            },
        }
        data = {
            "credential_id": str(uuid.uuid4()),
            "create_payload": payload,
            "connection_id": None,
            "binding_payload": None,
        }
        self.store.create_pending("create", data, pending_id=pending_id, phase="credential_pending")
        credentials.save(data["credential_id"], token)
        self.store.update(pending_id, data, "authorized")
        return self.resume(pending_id)

    def authorize(self, connection_id, *, terminal):
        self.store.require_settled(connection_id)
        detail, template = self._authorized(connection_id, require_ready=False)
        credentials = self.credentials
        grant = detail["usage_grant"]
        terminal.describe(
            template,
            {
                "account": detail["connection"]["account"],
                "operations": grant["operations"],
                "resources": grant["resources"],
            },
        )
        token = terminal.read_token()
        self.github_factory(token).test_capabilities(
            grant["resources"],
            expected_account_id=detail["connection"]["account"]["provider_account_id"],
            operations=grant["operations"],
        )
        data = {
            "credential_id": str(uuid.uuid4()),
            "connection_id": connection_id,
            "binding_payload": None,
            "expected_account_id": detail["connection"]["account"]["provider_account_id"],
        }
        pending = self.store.create_pending("authorize", data, phase="credential_pending")
        credentials.save(data["credential_id"], token)
        self.store.update(pending["request_id"], data, "authorized")
        return self.resume(pending["request_id"])

    def _saved_token(self, data):
        token = self.credentials.load(data["credential_id"])
        if token is None:
            raise CompanionError(
                "credential_unavailable",
                "This pending request has no native credential. Authorize again in the owner's terminal; no plaintext fallback is available.",
                3,
            )
        return token

    def resume(self, pending_id):
        pending = self.store.pending(pending_id)
        data = pending["data"]
        if pending["phase"] == "complete":
            return {"status": "already_completed", "request_id": pending_id, "connection_id": data["connection_id"]}
        if pending["phase"] == "cancelled":
            return {"status": "cancelled", "request_id": pending_id, "connection_id": data.get("connection_id")}
        if pending["kind"] == "cancel":
            return self._finish_cancel(pending)
        if pending["kind"] == "forget":
            return self._finish_forget(pending)
        if pending["phase"] == "cleanup_pending":
            return self._finish_cleanup(pending)
        token = self._saved_token(data)
        if pending["kind"] == "create" and data["connection_id"] is None:
            payload = data["create_payload"]
            # Re-fetch validates disabled templates on every retry without changing
            # the accepted payload or reusing a request ID for different content.
            self.template(payload["template_id"], payload["template_version"])
            result = exact(self._request("POST", "/v1/connections", payload), ("connection_id", "grant_revision"))
            request_id(result["connection_id"])
            if result["grant_revision"] != 1:
                raise CompanionError("connection_schema_rejected", "Unexpected initial grant revision.")
            data["connection_id"] = result["connection_id"]
            self.store.update(pending_id, data, "registered")
        connection_id = data["connection_id"]
        detail, template = self._authorized(connection_id, require_ready=False)
        grant = detail["usage_grant"]
        expected_id = detail["connection"]["account"]["provider_account_id"]
        if data.get("expected_account_id", expected_id) != expected_id or (
            pending["kind"] == "create" and data["create_payload"]["account"]["provider_account_id"] != expected_id
        ):
            raise CompanionError(
                "github_account_mismatch", "The pending request does not match the registered account.", 5
            )
        # An immutable binding payload survives lost responses. A grant change
        # requires new tests and a fresh mutation ID, never payload mutation/reuse.
        binding_payload = data.get("binding_payload")
        if binding_payload is None or binding_payload["grant_revision"] != grant["revision"]:
            tested = self.github_factory(token).test_capabilities(
                grant["resources"], expected_account_id=expected_id, operations=grant["operations"]
            )
            binding_payload = {
                "schema_version": "1",
                "request_id": str(uuid.uuid4()),
                "grant_revision": grant["revision"],
                "status": "ready",
                "verified_account_id": tested["account"]["provider_account_id"],
                "requested_permissions": template["auth"]["required_permissions"],
                "tested_capabilities": tested["tested_capabilities"],
                "tested_resources": tested["tested_resources"],
                "error_code": None,
            }
            data["binding_payload"] = binding_payload
            self.store.update(pending_id, data, "binding_pending")
        result = exact(
            self._request("PUT", "/v1/connections/" + connection_id + "/binding", binding_payload),
            ("connection_id", "installation_id", "status", "grant_revision"),
        )
        if result != {
            "connection_id": connection_id,
            "installation_id": self.config.get("installation_id"),
            "status": "ready",
            "grant_revision": binding_payload["grant_revision"],
        }:
            raise CompanionError(
                "connection_schema_rejected", "The binding acknowledgement did not match this environment."
            )
        current, _ = self._authorized(connection_id, require_ready=True)
        if current["usage_grant"]["revision"] != binding_payload["grant_revision"]:
            raise CompanionError(
                "grant_changed", "The grant changed after the binding report; retry to test the current scope.", 6
            )
        self.store.finish({**pending, "data": data}, connection_id, expected_id, binding_payload["grant_revision"])
        return self._finish_cleanup(self.store.pending(pending_id))

    def _finish_cleanup(self, pending):
        data = pending["data"]
        for credential_id in data.get("cleanup_ids", []):
            self.credentials.delete(credential_id)
        self.store.update(pending["request_id"], data, "complete")
        return {
            "status": "ready",
            "connection_id": data["connection_id"],
            "installation_id": self.config.get("installation_id"),
            "grant_revision": data["binding_payload"]["grant_revision"],
            "request_id": pending["request_id"],
        }

    def cancel(self, pending_id):
        pending = self.store.cancel_pending(pending_id)
        if pending["kind"] == "cancel":
            return self._finish_cancel(pending)
        if pending["kind"] == "forget":
            return self._finish_forget(pending)
        return {
            "status": pending["phase"],
            "request_id": pending_id,
            "connection_id": pending["data"].get("connection_id"),
        }

    def _finish_cancel(self, pending):
        for credential_id in pending["data"]["cleanup_ids"]:
            self.credentials.delete(credential_id)
        self.store.update(pending["request_id"], pending["data"], "cancelled")
        return {
            "status": "cancelled",
            "request_id": pending["request_id"],
            "connection_id": pending["data"].get("connection_id"),
        }

    def test(self, connection_id):
        self.store.require_settled(connection_id)
        detail, _ = self._authorized(connection_id, require_ready=False)
        binding = self.store.binding(connection_id)
        if binding is None:
            raise CompanionError(
                "credential_unavailable", "This environment has no local credential; authorize this connection here.", 3
            )
        data = {
            "credential_id": binding["credential_id"],
            "connection_id": connection_id,
            "binding_payload": None,
            "expected_account_id": detail["connection"]["account"]["provider_account_id"],
        }
        pending = self.store.create_pending("test", data)
        return self.resume(pending["request_id"])

    def read(
        self,
        connection_id,
        operation,
        resource,
        *,
        issue_number=None,
        page=1,
        per_page=20,
        issue_state="open",
        include_content=False,
    ):
        if operation not in CAPABILITIES:
            raise CompanionError("operation_rejected", "This connector supports only its published read operations.")
        selected = resource_key(resource)
        detail, _ = self._authorized(connection_id, require_ready=True)
        grant = detail["usage_grant"]
        if operation not in grant["operations"] or selected not in {resource_key(item) for item in grant["resources"]}:
            raise CompanionError(
                "grant_denied",
                "The requested operation or repository is outside this connection's current business-use grant.",
                5,
            )
        binding = self.store.binding(connection_id)
        if binding is None or binding["provider_account_id"] != detail["connection"]["account"]["provider_account_id"]:
            raise CompanionError(
                "credential_unavailable",
                "This environment has no matching local credential. Authorize the logical connection here.",
                3,
            )
        token = self._saved_token(binding)
        client = self.github_factory(token)
        identity = client.identity()
        if identity["provider_account_id"] != detail["connection"]["account"]["provider_account_id"]:
            raise CompanionError(
                "github_account_mismatch",
                "The stored token belongs to a different account; no repository request was sent.",
                5,
            )
        if operation == "repository.read":
            if issue_number is not None:
                raise CompanionError("query_rejected", "An issue number requires the issues.read operation.")
            result = client.repository(resource, include_content=include_content)
        else:
            result = client.issues(
                resource,
                issue_number=issue_number,
                page=page,
                per_page=per_page,
                state=issue_state,
                include_content=include_content,
            )
        return {
            "connection_id": connection_id,
            "provider_account_id": identity["provider_account_id"],
            "resource": resource,
            "operation": operation,
            "grant_revision": grant["revision"],
            "content_included": include_content,
            "result": result,
        }

    def forget(self, connection_id):
        request_id(connection_id)
        pending = self.store.begin_forget(connection_id)
        if pending is None:
            return {"status": "already_forgotten", "connection_id": connection_id}
        return self._finish_forget(pending)

    def _finish_forget(self, pending):
        data = pending["data"]
        connection_id = data["connection_id"]
        for credential_id in data["credential_ids"]:
            self.credentials.delete(credential_id)
        self.store.update(pending["request_id"], data, "local_forgotten")
        try:
            detail, template = self._authorized(connection_id, require_ready=False)
            payload = data.get("binding_payload")
            if payload is None or payload["grant_revision"] != detail["usage_grant"]["revision"]:
                payload = {
                    "schema_version": "1",
                    "request_id": str(uuid.uuid4()),
                    "grant_revision": detail["usage_grant"]["revision"],
                    "status": "needs_auth",
                    "verified_account_id": None,
                    "requested_permissions": template["auth"]["required_permissions"],
                    "tested_capabilities": [],
                    "tested_resources": [],
                    "error_code": None,
                }
                data["binding_payload"] = payload
                self.store.update(pending["request_id"], data, "local_forgotten")
            self._request("PUT", "/v1/connections/" + connection_id + "/binding", payload)
        except CompanionError as error:
            if error.code in ("connection_disabled", "binding_revoked"):
                # Local credential deletion has completed. An explicitly disabled
                # remote binding cannot be updated and must not wedge local cleanup.
                self.store.update(pending["request_id"], data, "complete")
                return {
                    "status": "locally_forgotten",
                    "connection_id": connection_id,
                    "metadata_sync": "not_required_disabled",
                    "error": error.code,
                }
            return {
                "status": "locally_forgotten",
                "connection_id": connection_id,
                "metadata_sync": "pending_or_denied",
                "request_id": pending["request_id"],
                "error": error.code,
            }
        self.store.update(pending["request_id"], data, "complete")
        return {"status": "forgotten", "connection_id": connection_id, "metadata_sync": "complete"}


def connection_command(args, config, directory, *, owner_api):
    # All secrets remain behind native store/terminal interfaces; parser values
    # contain identifiers and explicit owner-selected resource metadata only.
    command = getattr(args, "connection_command", None)
    if getattr(args, "dry_run", False):
        result = {
            "status": "dry_run",
            "network_requests": False,
            "authorization": "not_checked",
            "connector_id": "github",
            "connector_version": "1.0.0",
        }
        if command == "add":
            result["planned_metadata"] = draft_input(args)
        if getattr(args, "connection_id", None):
            result["connection_id"] = request_id(args.connection_id)
        if getattr(args, "request_id", None):
            result["request_id"] = request_id(args.request_id)
        if command == "read":
            result["resource"] = parse_resource(args.resource)
            result["operation"] = args.operation
        return result
    mutates = command in ("add", "resume", "authorize", "test", "forget", "cancel")
    lock = file_lock(directory / "connections.lock") if mutates else nullcontext()
    with lock:
        manager = Connections(config, directory, api_factory=lambda: owner_api(config))
        terminal = None
        try:
            if args.command == "templates":
                if args.template_command == "list":
                    return manager.list_templates(args.after)
                template = manager.template(args.template_id, args.version)
                return {"template": template, "sha256": sha256(template)}
            if command == "list":
                return manager.list(args.after)
            if command == "show":
                return manager.detail(args.connection_id)
            if command == "pending":
                return {"requests": manager.store.pending_metadata()}
            if command in ("add", "authorize"):
                from .connection_terminal import NativeConnectionTerminal

                terminal = NativeConnectionTerminal()
                if command == "add":
                    return manager.add(draft_input(args), operations=args.operation, terminal=terminal)
                return manager.authorize(args.connection_id, terminal=terminal)
            if command == "resume":
                return manager.resume(args.request_id)
            if command == "cancel":
                return manager.cancel(args.request_id)
            if command == "test":
                return manager.test(args.connection_id)
            if command == "forget":
                return manager.forget(args.connection_id)
            if command == "read":
                result = manager.read(
                    args.connection_id,
                    args.operation,
                    parse_resource(args.resource),
                    issue_number=args.issue,
                    page=args.page,
                    per_page=args.per_page,
                    issue_state=args.issue_state,
                    include_content=args.include_content or args.export_dir is not None,
                )
                if args.export_dir is not None:
                    artifact_id = str(uuid.uuid4())
                    export_json(args.export_dir / (artifact_id + ".json"), result)
                    return {
                        "status": "exported",
                        "artifact_id": artifact_id,
                        "connection_id": args.connection_id,
                        "operation": args.operation,
                    }
                return result
            raise CompanionError("unknown_command", "Unsupported connection command.")
        finally:
            if terminal is not None:
                terminal.close()
            manager.store.close()
