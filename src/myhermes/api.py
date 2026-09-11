"""Bounded JSON transport. No request/response logging or redirects."""

import http.client
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .auth import assertion, dpop
from .errors import CompanionError, OfflineError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_server(server: str, allow_local_http: bool = False) -> str:
    try:
        parsed = urllib.parse.urlsplit(server)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or not parsed.hostname
        ):
            raise ValueError("Invalid URL")
        if parsed.scheme != "https" and not (
            allow_local_http and parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
        ):
            raise ValueError("HTTPS required")
        if parsed.port is not None and not 0 < parsed.port < 65536:
            raise ValueError("Invalid port")
    except ValueError:
        raise CompanionError(
            "invalid_server", "Use an HTTPS server origin; explicit local development permits HTTP loopback only."
        ) from None
    return server.rstrip("/")


class API:
    def __init__(self, server, private_key=None, installation_id=None, *, timeout=20, opener=None):
        self.server, self.private_key, self.installation_id = server, private_key, installation_id
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self._token = None
        self._expires = 0
        self._token_lock = threading.Lock()

    def _request(self, method, path, payload=None, headers=None):
        url = self.server + path
        data = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
        )
        try:
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.status
                # Base64 skill packages allow 2 MiB decoded bytes; conflict
                # detail carries two full candidates. Other APIs stay at 2 MB.
                maximum = 2_000_000
                if path.startswith("/v1/skills/"):
                    maximum = 6_000_000 if "/conflicts/" in path else 3_000_000
                raw = response.read(maximum + 1)
            if len(raw) > maximum:
                raise CompanionError("response_rejected", "Server response exceeded the supported size.")
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("Invalid JSON object")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException):
            raise OfflineError() from None
        except (ValueError, UnicodeError):
            raise CompanionError("response_rejected", "Server returned an invalid response.") from None
        if status in (401, 403):
            raise CompanionError("access_denied", "Access was denied; check membership, enrollment or revocation.", 5)
        if method == "POST" and path == "/v1/skills/personal" and status == 429 and body == {"error": "skill_capacity"}:
            # This published permanent capacity response proves that the mutation
            # was not admitted. Other rate limits/auth/errors keep normal handling.
            return status, body
        if status not in (200, 201, 202, 409):
            raise CompanionError("server_rejected", "Server rejected the request. No response body was logged.", 5)
        return status, body

    def auth_headers(self, method, path):
        """Fresh DPoP proof, with one synchronized short-lived token refresh."""
        if not self.private_key or not self.installation_id:
            raise CompanionError("not_enrolled", "Enroll this installation before using owner APIs.", 3)
        with self._token_lock:
            if not self._token or time.monotonic() >= self._expires:
                _, response = self._request(
                    "POST",
                    "/v1/auth/token",
                    {
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion(self.private_key, self.installation_id, self.server + "/v1/auth/token"),
                    },
                )
                if (
                    response.get("token_type") != "DPoP"
                    or not isinstance(response.get("access_token"), str)
                    or not response["access_token"].isascii()
                    or any(ord(character) <= 32 or ord(character) == 127 for character in response["access_token"])
                    or not response["access_token"]
                    or len(response["access_token"]) > 16384
                    or type(response.get("expires_in")) is not int
                    or not 1 <= response["expires_in"] <= 300
                ):
                    raise CompanionError(
                        "auth_response_rejected", "Server did not return a supported bound credential.", 5
                    )
                self._token = response["access_token"]
                self._expires = time.monotonic() + max(0, response["expires_in"] - 10)
            token = self._token
        return {
            "Authorization": "DPoP " + token,
            "DPoP": dpop(self.private_key, method, self.server + path, token),
        }

    def request(self, method, path, payload=None):
        return self._request(method, path, payload, self.auth_headers(method, path))
