"""Authenticated loopback OpenAI transport to the public, body-free MyHermes relay."""

from __future__ import annotations

import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import select
import socket
from socketserver import TCPServer
import ssl
import threading
import time
import urllib.parse

from .relay_ids import new_request_id
from .api import validate_server
from .errors import CompanionError
from .telemetry_contract import attributes as telemetry_attributes

MAX_REQUEST_BYTES = 262_144
MAX_RESPONSE_BYTES = 4_194_304
MAX_FRAME_BYTES = 131_072
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z", re.I)
_ERROR_CODES = frozenset(
    (
        "relay_request_registered",
        "relay_request_id_reused",
        "relay_request_id_expired",
        "relay_budget_exhausted",
        "relay_budget_frozen",
        "relay_capacity",
        "relay_model_not_allowed",
        "relay_timeout",
        "upstream_rejected",
        "upstream_unavailable",
        "upstream_invalid",
        "upstream_policy_mismatch",
        "stream_interrupted",
        "client_cancelled",
        "invalid_device_auth",
        "installation_unavailable",
        "membership_required",
        "request_too_large",
    )
)


class _Failure(Exception):
    def __init__(self, status, code, request_id=None):
        self.status, self.code, self.request_id = status, code, request_id


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("Nonfinite JSON value")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        canonical = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (ValueError, UnicodeError, RecursionError):
        raise _Failure(400, "relay_invalid_json") from None
    if not isinstance(value, dict):
        raise _Failure(400, "relay_invalid_json")
    return value, canonical


def _receipt(value):
    """Only receipt identifiers, enums and counters can decorate an error response."""
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("request_id", "server_request_id"):
        if isinstance(value.get(key), str) and _UUID.fullmatch(value[key]):
            result[key] = value[key]
    for key, choices in (
        ("state", ("registered", "completed", "failed", "cancelled", "timeout")),
        ("usage_state", ("unknown", "known")),
    ):
        if value.get(key) in choices:
            result[key] = value[key]
    for key in ("reservation_nano", "cost_nano", "prompt_tokens", "completion_tokens", "total_tokens"):
        item = value.get(key)
        if item is None or (type(item) is int and 0 <= item <= 10**15):
            result[key] = item
    return result or None


def _shutdown(sock):
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class _Call:
    """Watch the consumer and absolute deadline, including a stalled upstream read."""

    def __init__(self, bridge, client):
        self.bridge, self.client = bridge, client
        self.deadline = time.monotonic() + bridge.timeout
        self.upstream = None
        self.reason = None
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._watch, name="myhermes-relay-cancel", daemon=True)

    def __enter__(self):
        with self.bridge._calls_lock:
            self.bridge._calls.add(self)
        self.thread.start()
        return self

    def cancel(self, reason, *, close_client=False):
        with self.lock:
            self.reason = self.reason or reason
            _shutdown(self.upstream)
            if close_client:
                _shutdown(self.client)
        self.stopped.set()

    def attach(self, sock):
        with self.lock:
            self.upstream = sock
            if self.reason:
                _shutdown(sock)
        self.check()

    def check(self):
        if self.reason or time.monotonic() >= self.deadline:
            raise _Failure(504 if self.reason in (None, "relay_timeout") else 502, self.reason or "relay_timeout")

    def _watch(self):
        while not self.stopped.wait(0.05):
            if time.monotonic() >= self.deadline:
                self.cancel("relay_timeout")
                return
            try:
                readable, _, _ = select.select([self.client], [], [], 0)
                if readable and self.client.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b"":
                    self.cancel("client_cancelled")
                    return
            except BlockingIOError:
                pass
            except OSError:
                self.cancel("client_cancelled")
                return

    def __exit__(self, *_):
        self.stopped.set()
        self.thread.join(timeout=0.2)
        with self.bridge._calls_lock:
            self.bridge._calls.discard(self)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, bridge):
        self.bridge = bridge
        self.slots = threading.BoundedSemaphore(bridge.max_connections)
        super().__init__(("127.0.0.1", 0), _Handler)

    def server_bind(self):
        # HTTPServer normally reverse-resolves even a numeric loopback address.
        # No hostname is needed here; macOS resolver stalls must not block
        # managed startup before its signal handlers can run.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # The stdlib default prints exceptions and request details to stderr.
        pass


class RelayBridge:
    """Context manager exposing ``base_url`` and a process-only ``session_token``.

    The supplied API owns the installation key. No configuration or log file is
    written. Explicit loopback upstream HTTP is available only for local tests.
    """

    def __init__(
        self,
        api,
        *,
        allow_local_http=False,
        timeout=120.0,
        max_requests=1000,
        max_connections=8,
        on_activity=None,
        connection_directory=None,
    ):
        if not 0 < timeout <= 300 or type(max_requests) is not int or not 1 <= max_requests <= 10000:
            raise CompanionError("relay_configuration_rejected", "Invalid relay bridge limits.")
        if type(max_connections) is not int or not 1 <= max_connections <= 32:
            raise CompanionError("relay_configuration_rejected", "Invalid relay bridge concurrency.")
        self.api = api
        self.origin = validate_server(api.server, allow_local_http)
        self.timeout, self.max_requests, self.max_connections = timeout, max_requests, max_connections
        self.session_token = secrets.token_urlsafe(32)
        self.on_activity = on_activity
        self.connection_directory = connection_directory
        self._ids, self._digests = {}, {}
        self._ids_lock = threading.Lock()
        self._calls, self._calls_lock = set(), threading.Lock()
        self._server = None
        self._thread = None
        self.base_url = None

    def __repr__(self):
        return "RelayBridge(credential=<process-only>)"

    def __enter__(self):
        if self._server is not None:
            raise CompanionError("relay_already_started", "This relay bridge has already started.")
        self._server = _Server(self)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}/v1"
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05), name="myhermes-relay", daemon=True
        )
        self._thread.start()
        return self

    def close(self):
        if self._server is None:
            return
        self._server.shutdown()
        with self._calls_lock:
            calls = list(self._calls)
        for call in calls:
            call.cancel("bridge_stopped", close_client=True)
        self._server.server_close()
        self._thread.join(timeout=1)
        self._server = None

    def __exit__(self, *_):
        self.close()

    def activity(self, kind, attrs):
        if self.on_activity is not None:
            try:
                self.on_activity(kind, telemetry_attributes(kind, attrs))
            except Exception:
                # Monitoring is best-effort; no raw observer exception is logged.
                pass

    def request_id(self, canonical, supplied=None):
        if supplied is not None and (not isinstance(supplied, str) or not _UUID.fullmatch(supplied)):
            raise _Failure(400, "relay_invalid_request_id")
        digest = hashlib.sha256(canonical).hexdigest()
        with self._ids_lock:
            if supplied in self._ids and self._ids[supplied] != digest:
                raise _Failure(409, "relay_request_id_reused", supplied)
            existing = self._digests.get(digest)
            if existing:
                if supplied is not None and supplied != existing:
                    raise _Failure(409, "relay_body_registered", existing)
                return existing
            if len(self._ids) >= self.max_requests:
                raise _Failure(429, "relay_bridge_capacity")
            request_id = supplied or new_request_id()
            self._ids[request_id], self._digests[digest] = digest, request_id
            return request_id


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def log_message(self, *_):
        pass

    def send_error(self, code, message=None, explain=None):
        self._error(code, "invalid_http_request")

    def _headers(self, status, content_type, request_id=None, server_id=None, length=None):
        self.send_response_only(status)
        for name, value in (
            ("Content-Type", content_type),
            ("Cache-Control", "private, no-store"),
            ("Connection", "close"),
            ("x-should-retry", "false"),
            ("X-Content-Type-Options", "nosniff"),
        ):
            self.send_header(name, value)
        if request_id:
            self.send_header("MyHermes-Request-Id", request_id)
        if server_id and _UUID.fullmatch(server_id):
            self.send_header("MyHermes-Server-Request-Id", server_id)
        self.send_header("Content-Length", str(length)) if length is not None else self.send_header(
            "Transfer-Encoding", "chunked"
        )
        self.end_headers()
        self.close_connection = True

    def _error(self, status, code, request_id=None, *, receipt=None, server_id=None):
        value = {
            "error": {
                "type": "myhermes_relay_error",
                "code": code,
                "message": "The relay did not provide a completion. Check the request receipt before retrying.",
            }
        }
        if request_id:
            value["request_id"] = request_id
        if receipt:
            value["receipt"] = receipt
        raw = json.dumps(value, separators=(",", ":")).encode()
        try:
            self._headers(status, "application/json", request_id, server_id, len(raw))
            self.wfile.write(raw)
        except (OSError, ValueError):
            self.close_connection = True

    def _one_header(self, name, *, optional=False):
        values = self.headers.get_all(name) or []
        if len(values) != 1:
            if optional and not values:
                return None
            raise _Failure(400, "relay_invalid_headers")
        return values[0]

    def _input(self):
        bridge = self.server.bridge
        authorization = self._one_header("Authorization", optional=True)
        if not authorization or not hmac.compare_digest(
            authorization.encode("utf-8"), ("Bearer " + bridge.session_token).encode("ascii")
        ):
            raise _Failure(401, "relay_local_auth_required")
        paths = {("GET", "/v1/models"): "/llm/v1/models", ("POST", "/v1/chat/completions"): "/llm/v1/chat/completions"}
        local_tool = (
            self.command == "POST" and self.path == "/v1/myhermes/tool-events" and bridge.on_activity is not None
        )
        local_directory = (
            self.command == "POST"
            and self.path in ("/v1/myhermes/connections", "/v1/myhermes/connection-report")
            and bridge.connection_directory is not None
        )
        upstream_path = paths.get((self.command, self.path))
        if upstream_path is None and not local_tool and not local_directory:
            raise _Failure(404, "relay_path_rejected")
        if self.headers.get_all("Transfer-Encoding") or self.headers.get_all("Expect"):
            raise _Failure(400, "relay_framing_rejected")
        if self.headers.get_all("X-MyHermes-Request-Id"):
            raise _Failure(400, "relay_invalid_headers")
        length = self._one_header("Content-Length", optional=True)
        if length is None:
            if self.command == "POST":
                raise _Failure(411, "relay_length_required")
            length = "0"
        if not re.fullmatch(r"[0-9]{1,10}", length):
            raise _Failure(400, "relay_framing_rejected")
        length = int(length)
        if length > (1024 if local_tool else 16384 if local_directory else MAX_REQUEST_BYTES) or (
            self.command == "GET" and length
        ):
            raise _Failure(413, "relay_request_too_large")
        if self.command == "GET":
            return upstream_path, None, False, None
        if self._one_header("Content-Type").split(";", 1)[0].strip().lower() != "application/json":
            raise _Failure(415, "relay_json_required")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise _Failure(400, "relay_incomplete_request")
        value, canonical = _json(raw)
        if local_directory:
            from .connection_directory import snapshot

            try:
                if self.path.endswith("/connection-report"):
                    snapshot(value)
                else:
                    if set(value) != {"snapshot", "integration_id"}:
                        raise ValueError()
                    snapshot(value["snapshot"])
                    if value["integration_id"] is not None and (
                        not isinstance(value["integration_id"], str)
                        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", value["integration_id"])
                    ):
                        raise ValueError()
            except (CompanionError, ValueError, TypeError):
                raise _Failure(400, "connection_metadata_rejected") from None
            return self.path, value, False, None
        if local_tool:
            if set(value) != {"tool_kind", "outcome", "duration_ms"}:
                raise _Failure(400, "relay_tool_metadata_rejected")
            try:
                telemetry_attributes("tool", value)
            except CompanionError:
                raise _Failure(400, "relay_tool_metadata_rejected") from None
            return None, value, False, None
        if "stream" in value and type(value["stream"]) is not bool:
            raise _Failure(400, "relay_invalid_stream")
        self._known_alias = value.get("model") == "economy"
        request_id = bridge.request_id(canonical, self._one_header("MyHermes-Request-Id", optional=True))
        return upstream_path, raw, value.get("stream", False), request_id

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _handle(self):
        request_id, started = None, False
        connection, call = None, None
        began, outcome, observed_usage = time.monotonic(), "unknown", {}
        try:
            path, raw, stream, request_id = self._input()
            bridge = self.server.bridge
            if path in ("/v1/myhermes/connections", "/v1/myhermes/connection-report"):
                result = bridge.connection_directory.runtime(
                    "report" if path.endswith("/connection-report") else "guide", raw
                )
                encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
                if len(encoded) > 2_000_000:
                    raise _Failure(502, "connection_directory_too_large")
                self._headers(200, "application/json", length=len(encoded))
                self.wfile.write(encoded)
                return
            if path is None:
                bridge.activity("tool", raw)
                self._headers(202, "application/json", length=17)
                self.wfile.write(b'{"accepted":true}')
                return
            with _Call(bridge, self.connection) as call:
                headers = bridge.api.auth_headers(self.command, path)
                call.check()
                headers = {
                    "Authorization": headers["Authorization"],
                    "DPoP": headers["DPoP"],
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream" if stream else "application/json",
                    "User-Agent": "myhermes-relay-bridge/1",
                }
                if request_id:
                    headers["MyHermes-Request-Id"] = request_id
                parsed = urllib.parse.urlsplit(bridge.origin)
                if parsed.scheme == "https":
                    connection = http.client.HTTPSConnection(
                        parsed.hostname,
                        parsed.port,
                        timeout=min(10, bridge.timeout),
                        context=ssl.create_default_context(),
                    )
                else:
                    connection = http.client.HTTPConnection(
                        parsed.hostname, parsed.port, timeout=min(10, bridge.timeout)
                    )
                connection.connect()
                call.attach(connection.sock)
                connection.sock.settimeout(max(0.01, call.deadline - time.monotonic()))
                connection.request(self.command, path, body=raw, headers=headers)
                response = connection.getresponse()
                call.check()
                server_id = response.getheader("MyHermes-Server-Request-Id")
                media = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
                if 300 <= response.status < 400:
                    raise _Failure(502, "relay_redirect_rejected")
                if response.status != 200:
                    outcome = "unknown" if response.status == 409 or response.status >= 500 else "failed"
                    error_raw = response.read(65_537)
                    code, receipt = "relay_rejected", None
                    if len(error_raw) <= 65_536 and media == "application/json":
                        try:
                            error, _ = _json(error_raw)
                            candidate = error.get("error")
                            candidate = candidate.get("code") if isinstance(candidate, dict) else candidate
                            if isinstance(candidate, str) and candidate in _ERROR_CODES:
                                code = candidate
                            receipt = _receipt(error.get("receipt"))
                        except _Failure:
                            pass
                    self._error(
                        response.status if 400 <= response.status <= 599 else 502,
                        code,
                        request_id,
                        receipt=receipt,
                        server_id=server_id,
                    )
                    return
                declared = response.getheader("Content-Length")
                if declared and (not declared.isdecimal() or int(declared) > MAX_RESPONSE_BYTES):
                    raise _Failure(502, "relay_response_too_large")
                if stream:
                    if media != "text/event-stream":
                        raise _Failure(502, "relay_response_rejected")
                    self._headers(200, "text/event-stream", request_id, server_id)
                    started = True
                    observed_usage = self._stream(response, call)
                    outcome = "ok"
                else:
                    if media != "application/json":
                        raise _Failure(502, "relay_response_rejected")
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                    call.check()
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise _Failure(502, "relay_response_too_large")
                    try:
                        parsed_body, _ = _json(body)
                    except _Failure:
                        raise _Failure(502, "relay_response_rejected") from None
                    self._headers(200, "application/json", request_id, server_id, len(body))
                    self.wfile.write(body)
                    outcome = "ok"
                    observed_usage = self._usage(parsed_body.get("usage"))
        except _Failure as error:
            outcome = "unknown" if error.status >= 500 or error.status == 409 else "failed"
            if not started:
                self._error(error.status, error.code, error.request_id or request_id)
            else:
                self._stream_error(error.code, request_id)
        except CompanionError:
            outcome = "failed"
            if not started:
                self._error(403, "relay_device_auth_unavailable", request_id)
            else:
                self._stream_error("relay_device_auth_unavailable", request_id)
        except (OSError, http.client.HTTPException, ValueError):
            code = call.reason if call is not None and call.reason else "relay_transport_uncertain"
            if call is not None and time.monotonic() >= call.deadline:
                code = "relay_timeout"
            status = 504 if code == "relay_timeout" else 502
            outcome = "cancelled" if code in ("client_cancelled", "bridge_stopped") else "unknown"
            if not started:
                self._error(status, code, request_id)
            else:
                self._stream_error(code, request_id)
        finally:
            if connection is not None:
                connection.close()
            self.close_connection = True
            if request_id is not None:
                self.server.bridge.activity(
                    "model",
                    {
                        "outcome": outcome,
                        "duration_ms": min(86_400_000, int((time.monotonic() - began) * 1000)),
                        **({"model_alias": "economy"} if self._known_alias else {}),
                        "request_id": request_id,
                        **observed_usage,
                    },
                )

    @staticmethod
    def _usage(value):
        if not isinstance(value, dict):
            return {}
        return {
            key: value[key]
            for key in ("prompt_tokens", "completion_tokens")
            if type(value.get(key)) is int and 0 <= value[key] <= 100_000_000
        }

    def _chunk(self, raw):
        self.wfile.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
        self.wfile.flush()

    def _stream(self, response, call):
        total, pending, done = 0, bytearray(), False
        usage = {}
        while True:
            call.check()
            chunk = response.read1(8192)
            call.check()
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise _Failure(502, "relay_response_too_large")
            pending.extend(chunk)
            while True:
                match = re.search(rb"\r?\n\r?\n", pending)
                if match is None:
                    if len(pending) > MAX_FRAME_BYTES:
                        raise _Failure(502, "relay_frame_too_large")
                    break
                end = match.end()
                if end > MAX_FRAME_BYTES:
                    raise _Failure(502, "relay_frame_too_large")
                frame = bytes(pending[:end])
                del pending[:end]
                if any(line.startswith(b"data:") and line[5:].strip() == b"[DONE]" for line in frame.splitlines()):
                    done = True
                for line in frame.splitlines():
                    if line.startswith(b"data:"):
                        try:
                            data, _ = _json(line[5:].strip())
                            candidate = self._usage(data.get("usage"))
                            if candidate:
                                usage = candidate
                        except _Failure:
                            pass
                self._chunk(frame)
        if pending or not done:
            raise _Failure(502, "relay_stream_interrupted")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()
        return usage

    def _stream_error(self, code, request_id):
        value = {
            "error": {
                "code": code,
                "message": "Relay stream interrupted. Check the original request receipt before retrying.",
            }
        }
        if request_id:
            value["request_id"] = request_id
        try:
            self._chunk(b"data: " + json.dumps(value, separators=(",", ":")).encode() + b"\n\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (OSError, ValueError):
            pass
