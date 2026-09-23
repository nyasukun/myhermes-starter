#!/usr/bin/env python3
"""Make a bounded, credential-free HTTP reachability check for MyHermes."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


TIMEOUT_SECONDS = 8
PROBES = (
    "/",
    "/guide.html",
    "/v1/me",
    "/llm/v1/models",
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def validate_origin(value: str) -> tuple[str, str]:
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or parsed.port not in (None, 443)
        ):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        host_ascii = host.encode("idna").decode("ascii").lower().rstrip(".")
        labels = host_ascii.split(".")
        if (
            len(host_ascii) > 253
            or len(labels) < 2
            or host_ascii.endswith(".local")
            or any(
                not label
                or len(label) > 63
                or not label[0].isalnum()
                or not label[-1].isalnum()
                or any(not (character.isalnum() or character == "-") for character in label)
                for label in labels
            )
        ):
            raise ValueError
        origin = f"https://{host_ascii}"
    except (UnicodeError, ValueError):
        raise argparse.ArgumentTypeError(
            "Use an HTTPS DNS origin without credentials, path, query, fragment, or nonstandard port."
        ) from None
    return origin, host_ascii


def classify_network_error(error: BaseException) -> str:
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(error, ssl.SSLError):
        return "tls_error"
    if isinstance(error, urllib.error.URLError):
        if isinstance(error.reason, socket.gaierror):
            return "dns_error"
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            return "timeout"
        if isinstance(error.reason, ssl.SSLError):
            return "tls_error"
        return "connection_error"
    if isinstance(error, (OSError, http.client.HTTPException)):
        return "connection_error"
    return "request_error"


def is_access_login_redirect(location: str | None, origin: str) -> bool:
    if not location:
        return False
    try:
        target_host = urllib.parse.urlsplit(origin).hostname
        redirect = urllib.parse.urlsplit(location)
        redirect_host = redirect.hostname
        return (
            redirect.scheme == "https"
            and redirect_host is not None
            and redirect_host.endswith(".cloudflareaccess.com")
            and redirect.username is None
            and redirect.password is None
            and redirect.port in (None, 443)
            and target_host is not None
            and redirect.path == f"/cdn-cgi/access/login/{target_host}"
        )
    except ValueError:
        return False


def probe(opener: urllib.request.OpenerDirector, origin: str, path: str) -> dict[str, object]:
    request = urllib.request.Request(
        origin + path,
        method="GET",
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "myhermes-external-healthcheck/1.0",
        },
    )
    started = time.monotonic()
    try:
        try:
            response = opener.open(request, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status = response.status
            access_login = is_access_login_redirect(response.headers.get("Location"), origin)
            api_error = None
            if path in ("/v1/me", "/llm/v1/models") and status == 401:
                raw = response.read(4097)
                if len(raw) <= 4096:
                    try:
                        body = json.loads(raw)
                        if isinstance(body, dict):
                            api_error = body.get("error")
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        return {
            "path": path,
            "result": classify_network_error(error),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "healthy": False,
        }

    if path in ("/", "/guide.html"):
        result = "expected_access_redirect" if status == 302 and access_login else "unexpected_http_status"
    elif status == 401 and api_error == "access_auth_required":
        result = "expected_unauthenticated_api"
    elif status == 401:
        result = "unexpected_unauthenticated_response"
    else:
        result = "unexpected_http_status"
    return {
        "path": path,
        "status": status,
        "result": result,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "healthy": result in ("expected_access_redirect", "expected_unauthenticated_api"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True, type=validate_origin, metavar="HTTPS_ORIGIN")
    args = parser.parse_args()
    origin, hostname = args.origin
    opener = urllib.request.build_opener(NoRedirect())
    checks = [probe(opener, origin, path) for path in PROBES]
    report = {
        "status": "healthy" if all(check["healthy"] for check in checks) else "unhealthy",
        "host": hostname,
        "checks": checks,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
