"""Python validation of the public M2 connection contract; templates remain data."""

from __future__ import annotations

import hashlib
import json
import re

from .errors import CompanionError
from .github import (
    API_ORIGIN,
    API_VERSION,
    AUTH_METHOD,
    CAPABILITIES,
    CONNECTOR_ID,
    CONNECTOR_VERSION,
    REQUESTED_PERMISSIONS,
    github_resource,
    resource_key,
)

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z", re.I)
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z")
_VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\Z")


def rejected():
    return CompanionError(
        "connection_schema_rejected", "Connection or template data does not match the supported public contract."
    )


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise rejected()
    return value


def bounded(value, limit, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value) <= limit or "\0" in value:
        raise rejected()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise rejected() from None
    return value


def identifier(value):
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise rejected()
    return value


def version(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise rejected()
    return value


def request_id(value):
    # Match the public UUID contract while preserving native-store key spelling.
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise rejected()
    return value


def choices(value, allowed, minimum=1):
    if (
        not isinstance(value, list)
        or not minimum <= len(value) <= len(allowed)
        or any(not isinstance(item, str) or item not in allowed for item in value)
        or len(value) != len(set(value))
    ):
        raise rejected()
    return value


def resources(value, minimum=1):
    if not isinstance(value, list) or not minimum <= len(value) <= 50:
        raise rejected()
    keys = [resource_key(resource) for resource in value]
    if len(set(keys)) != len(keys):
        raise rejected()
    return value


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_template(value):
    template = exact(
        value,
        (
            "schema_version",
            "template_id",
            "version",
            "display_name",
            "description",
            "connector_id",
            "connector_version",
            "service",
            "auth",
            "supported_os",
            "input_fields",
            "setup",
            "connection_test",
            "capabilities",
            "resource_rules",
            "related_skills",
            "usage_notice",
        ),
    )
    if (
        template["schema_version"] != "1"
        or template["connector_id"] != CONNECTOR_ID
        or template["connector_version"] != CONNECTOR_VERSION
    ):
        raise rejected()
    identifier(template["template_id"])
    version(template["version"])
    bounded(template["display_name"], 120)
    bounded(template["description"], 2000, 0)
    if exact(template["service"], ("base_url", "api_version", "allowed_hosts")) != {
        "base_url": API_ORIGIN,
        "api_version": API_VERSION,
        "allowed_hosts": ["api.github.com"],
    }:
        raise rejected()
    auth = exact(template["auth"], ("method", "required_permissions"))
    if auth["method"] != AUTH_METHOD:
        raise rejected()
    permissions = choices(auth["required_permissions"], REQUESTED_PERMISSIONS)
    choices(template["supported_os"], ("ubuntu", "macos"))
    if not isinstance(template["input_fields"], list) or len(template["input_fields"]) != 1:
        raise rejected()
    field = exact(template["input_fields"][0], ("name", "type", "required"))
    if field["name"] != "resources" or field["type"] != "github_repository_list" or field["required"] is not True:
        raise rejected()
    if exact(template["setup"], ("procedure_id", "procedure_version")) != {
        "procedure_id": "github-fine-grained-pat-v1",
        "procedure_version": CONNECTOR_VERSION,
    }:
        raise rejected()
    test = exact(template["connection_test"], ("procedure_id", "required_capabilities"))
    if test["procedure_id"] != "github-read-test-v1":
        raise rejected()
    capabilities = choices(template["capabilities"], CAPABILITIES)
    tested = choices(test["required_capabilities"], CAPABILITIES)
    if (
        "repository.read" not in capabilities
        or set(capabilities) != set(tested)
        or "metadata:read" not in permissions
        or ("issues:read" in permissions) != ("issues.read" in capabilities)
    ):
        raise rejected()
    rules = template["resource_rules"]
    if not isinstance(rules, list) or not 1 <= len(rules) <= 50:
        raise rejected()
    seen_rules = set()
    for rule in rules:
        exact(rule, ("owner", "repository"))
        github_resource(
            "example" if rule["owner"] == "*" else rule["owner"],
            "example" if rule["repository"] == "*" else rule["repository"],
        )
        key = (rule["owner"].lower(), rule["repository"].lower())
        if key in seen_rules:
            raise rejected()
        seen_rules.add(key)
    related = template["related_skills"]
    if not isinstance(related, list) or len(related) > 20:
        raise rejected()
    for skill in related:
        exact(skill, ("skill_id", "version"))
        identifier(skill["skill_id"])
        version(skill["version"])
    notice = exact(template["usage_notice"], ("version", "text"))
    if not isinstance(notice["version"], str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", notice["version"]
    ):
        raise rejected()
    bounded(notice["text"], 4000)
    return template


def template_response(value, template_id, template_version):
    envelope = exact(value, ("template", "sha256"))
    template = parse_template(envelope["template"])
    if (
        template["template_id"] != identifier(template_id)
        or template["version"] != version(template_version)
        or envelope["sha256"] != sha256(template)
    ):
        raise CompanionError(
            "template_integrity_failed", "The fetched template version or hash did not match its public contract.", 5
        )
    return template


def assert_grant(template, connection_resources, operations, granted_resources, notice_version):
    resources(connection_resources)
    resources(granted_resources)
    choices(operations, CAPABILITIES)
    allowed = {resource_key(item) for item in connection_resources}
    if notice_version != template["usage_notice"]["version"] or not set(operations).issubset(template["capabilities"]):
        raise CompanionError("grant_denied", "The operation is outside the current accepted template grant.", 5)
    for resource in connection_resources:
        if not any(
            (rule["owner"] == "*" or rule["owner"].lower() == resource["owner"].lower())
            and (rule["repository"] == "*" or rule["repository"].lower() == resource["repository"].lower())
            for rule in template["resource_rules"]
        ):
            raise CompanionError("grant_denied", "The repository is outside the template's allowed resources.", 5)
    if any(resource_key(item) not in allowed for item in granted_resources):
        raise CompanionError("grant_denied", "The repository is outside the connection's accepted resources.", 5)


def account(value):
    exact(value, ("provider_account_id", "login"))
    if not isinstance(value["provider_account_id"], str) or not re.fullmatch(
        r"[1-9][0-9]{0,19}", value["provider_account_id"]
    ):
        raise rejected()
    github_resource(value["login"], "example")
    return value


def connection_metadata(value):
    obj = exact(
        value,
        (
            "connection_id",
            "person_id",
            "template_id",
            "template_version",
            "connector_id",
            "connector_version",
            "account_kind",
            "display_name",
            "account",
            "management",
            "project",
            "resources",
            "grant_revision",
            "created_at",
            "updated_at",
            "revoked_at",
            "evidence_source",
            "effective_status",
        ),
    )
    request_id(obj["connection_id"])
    bounded(obj["person_id"], 200)
    identifier(obj["template_id"])
    version(obj["template_version"])
    if (
        obj["connector_id"] != CONNECTOR_ID
        or obj["connector_version"] != CONNECTOR_VERSION
        or obj["account_kind"] not in ("company", "client", "personal")
        or obj["evidence_source"] != "client_reported"
        or obj["effective_status"] not in ("active", "revoked", "template_disabled")
    ):
        raise rejected()
    bounded(obj["display_name"], 120)
    account(obj["account"])
    management = exact(obj["management"], ("kind", "label"))
    if management["kind"] not in ("company", "client", "individual"):
        raise rejected()
    bounded(management["label"], 120)
    if obj["project"] is not None:
        project = exact(obj["project"], ("id", "label"))
        identifier(project["id"])
        bounded(project["label"], 120)
    resources(obj["resources"])
    revision(obj["grant_revision"])
    for field in ("created_at", "updated_at"):
        bounded(obj[field], 64)
    if obj["revoked_at"] is not None:
        bounded(obj["revoked_at"], 64)
    return obj


def revision(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**53 - 1:
        raise rejected()
    return value


def parse_detail(value, connection_id, person_id, installation_id=None):
    detail = exact(value, ("connection", "usage_grant", "bindings"))
    connection = connection_metadata(detail["connection"])
    if connection["connection_id"] != request_id(connection_id) or connection["person_id"] != person_id:
        raise CompanionError(
            "connection_owner_mismatch", "The connection response does not belong to the authenticated owner.", 5
        )
    grant = exact(
        detail["usage_grant"],
        (
            "connection_id",
            "revision",
            "operations",
            "resources",
            "notice_version",
            "notice_sha256",
            "accepted_at",
            "created_by_installation_id",
        ),
    )
    if (
        grant["connection_id"] != connection_id
        or revision(grant["revision"]) != connection["grant_revision"]
        or not isinstance(grant["notice_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", grant["notice_sha256"])
    ):
        raise rejected()
    choices(grant["operations"], CAPABILITIES)
    resources(grant["resources"])
    bounded(grant["notice_version"], 32)
    bounded(grant["accepted_at"], 64)
    if grant["created_by_installation_id"] is not None:
        request_id(grant["created_by_installation_id"])
    if not isinstance(detail["bindings"], list) or len(detail["bindings"]) > 1000:
        raise rejected()
    if installation_id is not None and (
        len(detail["bindings"]) != 1
        or not isinstance(detail["bindings"][0], dict)
        or detail["bindings"][0].get("installation_id") != request_id(installation_id)
    ):
        raise CompanionError(
            "connection_owner_mismatch", "The binding response does not identify this authenticated installation.", 5
        )
    seen = set()
    for binding in detail["bindings"]:
        exact(
            binding,
            (
                "connection_id",
                "installation_id",
                "grant_revision",
                "status",
                "verified_account_id",
                "requested_permissions",
                "tested_capabilities",
                "tested_resources",
                "error_code",
                "updated_at",
                "revoked_at",
                "evidence_source",
                "effective_status",
            ),
        )
        if binding["connection_id"] != connection_id or request_id(binding["installation_id"]) in seen:
            raise rejected()
        seen.add(binding["installation_id"])
        revision(binding["grant_revision"])
        if (
            binding["status"] not in ("ready", "needs_auth", "error")
            or binding["effective_status"] not in ("ready", "needs_auth", "error", "stale", "revoked")
            or binding["evidence_source"] != "client_reported"
        ):
            raise rejected()
        choices(binding["requested_permissions"], REQUESTED_PERMISSIONS, 0)
        choices(binding["tested_capabilities"], CAPABILITIES, 0)
        resources(binding["tested_resources"], 0)
        if binding["verified_account_id"] is not None and (
            not isinstance(binding["verified_account_id"], str)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", binding["verified_account_id"])
        ):
            raise rejected()
        if binding["error_code"] not in (
            None,
            "auth_rejected",
            "resource_forbidden",
            "network_error",
            "account_mismatch",
            "credential_unavailable",
        ):
            raise rejected()
        for field in ("updated_at", "revoked_at"):
            if binding[field] is not None:
                bounded(binding[field], 64)
    return detail
