"""Fixed read-only GitHub connector. No shared login, arbitrary URL or secret output."""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .api import NoRedirect
from .errors import CompanionError, OfflineError

CONNECTOR_ID = "github"
CONNECTOR_VERSION = "1.0.0"
AUTH_METHOD = "github_fine_grained_pat"
API_ORIGIN = "https://api.github.com"
API_VERSION = "2026-03-10"
CAPABILITIES = ("repository.read", "issues.read")
REQUESTED_PERMISSIONS = ("metadata:read", "issues:read")
MAX_RESPONSE_BYTES = 2_000_000
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")


def github_resource(owner: str, repository: str) -> dict[str, str]:
    if (
        not isinstance(owner, str)
        or not isinstance(repository, str)
        or not _OWNER.fullmatch(owner)
        or not _REPOSITORY.fullmatch(repository)
        or repository in (".", "..")
    ):
        raise CompanionError("resource_rejected", "A GitHub resource must be one explicit valid owner/repository name.")
    return {"kind": "github_repository", "owner": owner, "repository": repository}


def parse_resource(value: str) -> dict[str, str]:
    if not isinstance(value, str) or value.count("/") != 1:
        raise CompanionError("resource_rejected", "Use owner/repository, without a URL, query or path suffix.")
    return github_resource(*value.split("/"))


def resource_key(resource: dict) -> str:
    if not isinstance(resource, dict):
        raise CompanionError("resource_rejected", "A resource must be a structured GitHub repository record.")
    parsed = github_resource(resource.get("owner"), resource.get("repository"))
    if resource.get("kind") != "github_repository" or set(resource) != {"kind", "owner", "repository"}:
        raise CompanionError("resource_rejected", "Unsupported resource kind or fields.")
    return (parsed["owner"] + "/" + parsed["repository"]).lower()


def validate_pat(token: str) -> None:
    if (
        not isinstance(token, str)
        or not 20 <= len(token) <= 512
        or not re.fullmatch(r"github_pat_[A-Za-z0-9_]+", token)
    ):
        raise CompanionError(
            "credential_format_rejected",
            "This connector accepts a fine-grained GitHub PAT entered in the owner's terminal.",
            3,
        )


def positive_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 10**20:
        raise CompanionError(
            "github_response_rejected", "GitHub returned an invalid account or resource identifier.", 5
        )
    return str(value)


class GitHubClient:
    """One token per client/request context. The opener is injectable only by Python tests."""

    def __init__(self, token: str, *, opener=None, timeout: float = 20):
        validate_pat(token)
        self._token = token
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.timeout = timeout

    def __repr__(self):
        return "GitHubClient(credential=<native-store>)"

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(self._token, "[credential redacted]")
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        return value

    def _get(self, path: str, query: dict | None = None):
        # All call sites construct paths from validated resources. Keep a defensive
        # guard here so a future caller cannot turn the connector into a URL proxy.
        if not re.fullmatch(r"/(?:user|repos/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+(?:/issues(?:/[0-9]+)?)?)", path):
            raise CompanionError(
                "endpoint_rejected", "This connector only supports its published read-only GitHub endpoints."
            )
        url = API_ORIGIN + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + self._token,
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "myhermes-github/" + CONNECTOR_VERSION,
            },
        )
        try:
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.status
                if 300 <= status < 400:
                    raise CompanionError(
                        "github_redirect_rejected",
                        "GitHub redirected this resource; review the registered resource instead of forwarding credentials.",
                        5,
                    )
                if status == 429 or (status == 403 and response.headers.get("X-RateLimit-Remaining") == "0"):
                    raise CompanionError(
                        "github_rate_limited",
                        "GitHub rate limited this read. Retry later; no automatic request replay occurred.",
                        8,
                    )
                if status == 401:
                    raise CompanionError(
                        "github_credentials_invalid",
                        "GitHub rejected this environment's credential. Reauthorize this connection.",
                        5,
                    )
                if status in (403, 404):
                    raise CompanionError(
                        "github_access_denied",
                        "GitHub denied this resource or capability; check token selection and organization approval.",
                        5,
                    )
                if status != 200:
                    raise CompanionError(
                        "github_request_failed",
                        "GitHub did not complete the read. Its response body was not logged.",
                        5,
                    )
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in ("application/json", "application/vnd.github+json"):
                    raise CompanionError("github_response_rejected", "GitHub returned an unexpected response type.", 5)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise CompanionError(
                    "github_response_too_large",
                    "GitHub returned more than the supported response size; narrow the read.",
                    5,
                )
            return json.loads(raw)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException):
            raise OfflineError() from None
        except (ValueError, UnicodeError):
            raise CompanionError(
                "github_response_rejected", "GitHub returned invalid JSON; its response body was not logged.", 5
            ) from None

    def identity(self) -> dict[str, str]:
        value = self._get("/user")
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("login"), str)
            or not _OWNER.fullmatch(value["login"])
        ):
            raise CompanionError("github_response_rejected", "GitHub returned an invalid authenticated account.", 5)
        return {"provider_account_id": positive_id(value.get("id")), "login": value["login"]}

    def repository(self, resource: dict, *, include_content: bool = False) -> dict:
        key = resource_key(resource)
        path = "/repos/" + resource["owner"] + "/" + resource["repository"]
        value = self._get(path)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("full_name"), str)
            or value["full_name"].lower() != key
            or not isinstance(value.get("private"), bool)
        ):
            raise CompanionError(
                "github_resource_mismatch",
                "GitHub returned a different or invalid repository; no content was exposed.",
                5,
            )
        result = {
            "repository_id": positive_id(value.get("id")),
            "full_name": value["full_name"],
            "private": value["private"],
            "url": "https://github.com/" + resource["owner"] + "/" + resource["repository"],
        }
        if include_content:
            for field in ("description", "default_branch"):
                content = value.get(field)
                if content is not None and (not isinstance(content, str) or len(content) > 20_000):
                    raise CompanionError("github_response_rejected", "GitHub returned an invalid repository field.", 5)
                result[field] = content
        return self._redact(result)

    def issues(self, resource: dict, *, issue_number=None, page=1, per_page=20, state="open", include_content=False):
        resource_key(resource)
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or not 1 <= page <= 1000
            or isinstance(per_page, bool)
            or not isinstance(per_page, int)
            or not 1 <= per_page <= 100
            or state not in ("open", "closed", "all")
        ):
            raise CompanionError(
                "query_rejected", "Issue reads require bounded page/per-page and an open, closed or all state."
            )
        path = "/repos/" + resource["owner"] + "/" + resource["repository"] + "/issues"
        if issue_number is not None:
            if isinstance(issue_number, bool) or not isinstance(issue_number, int) or not 0 < issue_number <= 10**10:
                raise CompanionError("query_rejected", "Issue number must be a positive bounded integer.")
            path += "/" + str(issue_number)
        value = self._get(
            path, None if issue_number is not None else {"state": state, "per_page": per_page, "page": page}
        )
        if issue_number is not None:
            value = [value]
        if not isinstance(value, list) or len(value) > (1 if issue_number else per_page):
            raise CompanionError("github_response_rejected", "GitHub returned an invalid issue page.", 5)
        results = []
        for item in value:
            if not isinstance(item, dict) or item.get("state") not in ("open", "closed"):
                raise CompanionError("github_response_rejected", "GitHub returned an invalid issue.", 5)
            number = int(positive_id(item.get("number")))
            if issue_number is not None and number != issue_number:
                raise CompanionError("github_resource_mismatch", "GitHub returned a different issue.", 5)
            record = {
                "number": number,
                "state": item["state"],
                "kind": "pull_request" if "pull_request" in item else "issue",
                "url": "https://github.com/"
                + resource["owner"]
                + "/"
                + resource["repository"]
                + "/issues/"
                + str(number),
            }
            if include_content:
                for field, limit in (("title", 4096), ("body", 1_000_000)):
                    content = item.get(field)
                    if content is not None and (not isinstance(content, str) or len(content) > limit):
                        raise CompanionError("github_response_rejected", "GitHub returned an invalid issue field.", 5)
                    record[field] = content
            results.append(self._redact(record))
        # Never follow a response-provided Link URL. A full page only suggests that
        # the caller may request the next bounded page from the same fixed endpoint.
        return {
            "items": results,
            "page": page,
            "next_page_possible": page + 1
            if issue_number is None and len(results) == per_page and page < 1000
            else None,
        }

    def test_capabilities(
        self, resources: list[dict], *, expected_account_id: str | None = None, operations=CAPABILITIES
    ):
        if (
            not operations
            or len(set(operations)) != len(operations)
            or any(operation not in CAPABILITIES for operation in operations)
        ):
            raise CompanionError("operation_rejected", "Unsupported connector capability.")
        account = self.identity()
        if expected_account_id is not None and account["provider_account_id"] != expected_account_id:
            raise CompanionError(
                "github_account_mismatch",
                "This token belongs to a different GitHub account. The connection was not rebound.",
                5,
            )
        for resource in resources:
            if "repository.read" in operations:
                self.repository(resource)
            if "issues.read" in operations:
                self.issues(resource, per_page=1)
        return {"account": account, "tested_capabilities": list(operations), "tested_resources": resources}
