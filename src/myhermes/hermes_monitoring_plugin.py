"""Public Hermes observer: the narrow signature never receives tool arguments/results.

This file is copied verbatim into the managed Hermes plugin directory. It uses
only the standard library and cannot access the installation signing key.
"""

import http.client
import json
import os
import urllib.parse

_KINDS = {
    "read_file": "read",
    "list_directory": "read",
    "web_extract": "read",
    "skill_view": "read",
    "skills_list": "read",
    "tool_describe": "read",
    "write_file": "write",
    "patch": "write",
    "edit_file": "write",
    "web_search": "search",
    "search_files": "search",
    "tool_search": "search",
    "terminal": "execute",
    "execute_code": "execute",
}


def post_tool_call(tool_name, duration_ms=0, status=None):
    # Do not add **kwargs: pinned Hermes filters undeclared fields BEFORE calling
    # this function. Raw names are converted before constructing any IPC payload.
    if type(duration_ms) is not int or not 0 <= duration_ms <= 86_400_000:
        return
    kind = _KINDS.get(tool_name, "other") if isinstance(tool_name, str) and len(tool_name) <= 128 else "other"
    outcome = (
        {"ok": "ok", "error": "failed", "blocked": "failed"}.get(status, "unknown")
        if isinstance(status, str)
        else "unknown"
    )
    value = {"tool_kind": kind, "duration_ms": duration_ms, "outcome": outcome}
    connection = None
    try:
        endpoint = urllib.parse.urlsplit(os.environ.get("MYHERMES_MONITORING_URL", ""))
        token = os.environ.get("AUXILIARY_MYHERMES_API_KEY", "")
        if (
            endpoint.scheme != "http"
            or endpoint.hostname != "127.0.0.1"
            or not endpoint.port
            or endpoint.path != "/v1/myhermes/tool-events"
            or endpoint.query
            or endpoint.fragment
            or endpoint.username
            or endpoint.password
            or not token
            or not token.isascii()
            or len(token) > 128
            or any(ord(character) <= 32 or ord(character) == 127 for character in token)
        ):
            return
        connection = http.client.HTTPConnection("127.0.0.1", endpoint.port, timeout=0.25)
        connection.request(
            "POST",
            endpoint.path,
            json.dumps(value, separators=(",", ":")).encode(),
            {"Content-Type": "application/json", "Authorization": "Bearer " + token},
        )
        response = connection.getresponse()
        response.read(1024)
    except (OSError, ValueError, http.client.HTTPException):
        pass
    finally:
        if connection is not None:
            connection.close()


def register(ctx):
    ctx.register_hook("post_tool_call", post_tool_call)
