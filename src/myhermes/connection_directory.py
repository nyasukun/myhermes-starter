"""Company guide retrieval and metadata-only MCP reports through the enrolled API."""

import json
import re
import threading
import urllib.parse
import uuid

from . import __version__
from .connection_schema import bounded, exact, identifier, request_id, version
from .errors import CompanionError

SERVER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
STATES = {"connected", "configured", "connecting", "not_connected", "needs_auth", "error", "disabled", "unknown"}
EFFECTIVE_STATES = STATES | {"ready", "stale", "revoked", "template_disabled"}


def rejected():
    raise CompanionError("connection_directory_rejected", "The connection directory metadata was not valid.", 5)


def snapshot(value):
    exact(value, ("collection_status", "connections"))
    if value["collection_status"] not in ("ok", "unavailable"):
        rejected()
    rows = value["connections"]
    if not isinstance(rows, list) or len(rows) > 100:
        rejected()
    names = set()
    for row in rows:
        exact(row, ("server_name", "status"))
        name = row["server_name"]
        if not isinstance(name, str) or not SERVER_NAME.fullmatch(name) or name in names:
            rejected()
        if not isinstance(row["status"], str) or row["status"] not in STATES:
            rejected()
        names.add(name)
    if value["collection_status"] == "unavailable" and rows:
        rejected()
    return value


def integration(value, *, effective=False):
    fields = [
        "integration_id",
        "display_name",
        "service",
        "required",
        "enabled",
        "source",
        "guide",
        "revision",
        "updated_at",
    ]
    if effective:
        fields += ["status", "evidence_source", "observed_at"]
    exact(value, fields)
    public = {
        key: item
        for key, item in value.items()
        if key not in ("revision", "updated_at", "status", "evidence_source", "observed_at")
    }
    try:
        size = len(json.dumps(public, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        rejected()
    if size > 16000:
        rejected()
    identifier(value["integration_id"])
    identifier(value["service"])
    bounded(value["display_name"], 120)
    bounded(value["updated_at"], 64)
    if type(value["revision"]) is not int or value["revision"] < 1:
        rejected()
    if type(value["required"]) is not bool or type(value["enabled"]) is not bool:
        rejected()
    source = value["source"]
    if not isinstance(source, dict):
        rejected()
    if source.get("kind") == "mcp":
        exact(source, ("kind", "server_name"))
        if not isinstance(source["server_name"], str) or not SERVER_NAME.fullmatch(source["server_name"]):
            rejected()
    elif source.get("kind") == "github":
        exact(source, ("kind", "template_id"))
        identifier(source["template_id"])
    else:
        rejected()
    guide = exact(value["guide"], ("summary", "steps", "url"))
    bounded(guide["summary"], 2000)
    if not isinstance(guide["steps"], list) or not 1 <= len(guide["steps"]) <= 12:
        rejected()
    for step in guide["steps"]:
        bounded(step, 1000)
    for text in [value["display_name"], guide["summary"], *guide["steps"]]:
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
            rejected()
    if guide["url"] is not None:
        raw = bounded(guide["url"], 2048)
        try:
            url = urllib.parse.urlsplit(raw)
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                rejected()
        except ValueError:
            rejected()
        if re.search(r"[\s\\]", raw):
            rejected()
    if effective:
        if (
            not isinstance(value["status"], str)
            or value["status"] not in EFFECTIVE_STATES
            or value["evidence_source"] != "client_reported"
        ):
            rejected()
        if value["observed_at"] is not None:
            bounded(value["observed_at"], 64)
    return value


class ConnectionDirectory:
    def __init__(self, api):
        self.api = api
        self.installation_id = request_id(api.installation_id)
        self._report_lock = threading.Lock()

    def _request(self, method, path, payload=None):
        code, value = self.api.request(method, path, payload)
        if code not in (200, 201):
            raise CompanionError("connection_directory_unavailable", "Refresh the company connection directory.", 5)
        return value

    def catalog(self):
        value = exact(self._request("GET", "/v1/integrations"), ("integrations",))
        self._integrations(value["integrations"])
        return value

    @staticmethod
    def _integrations(rows, *, effective=False):
        if not isinstance(rows, list) or len(rows) > 100:
            rejected()
        names = set()
        for row in rows:
            integration(row, effective=effective)
            if row["integration_id"] in names:
                rejected()
            names.add(row["integration_id"])

    def status(self, after=None):
        suffix = "" if after is None else "?after=" + request_id(after)
        value = exact(
            self._request("GET", "/v1/installations/" + self.installation_id + "/connections" + suffix),
            ("installation", "report", "integrations", "mcp", "github", "next_cursor"),
        )
        device = exact(value["installation"], ("installation_id", "person_id", "label", "revoked_at"))
        if request_id(device["installation_id"]) != self.installation_id:
            rejected()
        bounded(device["person_id"], 128)
        bounded(device["label"], 80)
        if device["revoked_at"] is not None:
            bounded(device["revoked_at"], 64)
        self._integrations(value["integrations"], effective=True)
        if value["report"] is not None:
            report = exact(value["report"], ("revision", "received_at", "collection_status", "companion_version"))
            version(report["companion_version"])
            if (
                type(report["revision"]) is not int
                or report["revision"] < 1
                or report["collection_status"] not in ("ok", "unavailable")
            ):
                rejected()
            bounded(report["received_at"], 64)
        for kind in ("mcp", "github"):
            if not isinstance(value[kind], list) or len(value[kind]) > 100:
                rejected()
            for row in value[kind]:
                if kind == "mcp":
                    exact(row, ("server_name", "status"))
                    if not isinstance(row["server_name"], str) or not SERVER_NAME.fullmatch(row["server_name"]):
                        rejected()
                else:
                    exact(row, ("connection_id", "display_name", "template_id", "account_kind", "status", "updated_at"))
                    request_id(row["connection_id"])
                    identifier(row["template_id"])
                    bounded(row["display_name"], 120)
                    if row["account_kind"] not in ("company", "client", "personal"):
                        rejected()
                    if row["updated_at"] is not None:
                        bounded(row["updated_at"], 64)
                if not isinstance(row["status"], str) or row["status"] not in EFFECTIVE_STATES:
                    rejected()
        if value["next_cursor"] is not None:
            request_id(value["next_cursor"])
        return value

    def report(self, value):
        snapshot(value)
        with self._report_lock:
            state = exact(self._request("GET", "/v1/connections/report"), ("revision",))
            revision = state["revision"]
            if type(revision) is not int or not 0 <= revision < 9007199254740991:
                rejected()
            report_id = str(uuid.uuid4())
            result = exact(
                self._request(
                    "POST",
                    "/v1/connections/report",
                    {
                        "schema_version": "1",
                        "report_id": report_id,
                        "base_revision": revision,
                        "companion_version": __version__,
                        **value,
                    },
                ),
                ("revision", "report_id", "received_at"),
            )
            if result["report_id"] != report_id or result["revision"] != revision + 1:
                rejected()
            bounded(result["received_at"], 64)
            return {"reported": True}

    def runtime(self, action, value):
        if action == "report":
            return self.report(value)
        exact(value, ("snapshot", "integration_id"))
        if value["integration_id"] is not None:
            identifier(value["integration_id"])
        snapshot(value["snapshot"])
        try:
            self.report(value["snapshot"])
            delivery = "reported"
        except CompanionError:
            delivery = "unavailable"
        result = self.status()
        from .companion_update import release_status

        try:
            result["companion_update"] = release_status(self.api)
        except CompanionError:
            # Update advice is optional; an unavailable release selection must
            # not hide the company connection instructions that were retrieved.
            result["companion_update"] = {"error": "companion_release_unavailable"}
        if value["integration_id"] is not None:
            result["integrations"] = [
                row for row in result["integrations"] if row["integration_id"] == value["integration_id"]
            ]
            if not result["integrations"]:
                raise CompanionError("integration_not_found", "That company integration is not enabled.", 3)
        else:
            result["integrations"] = [
                {**{key: item for key, item in row.items() if key != "guide"}, "guide_summary": row["guide"]["summary"]}
                for row in result["integrations"]
            ]
        return {"report_delivery": delivery, **result}
