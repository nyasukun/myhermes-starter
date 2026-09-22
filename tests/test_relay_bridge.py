"""M3 bridge acceptance over real loopback HTTP and ephemeral synthetic keys."""

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import select
import socket
from socketserver import TCPServer
import threading
import time
import unittest
from unittest.mock import patch
import urllib.parse
import uuid

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from myhermes import __version__
from myhermes.api import API
from myhermes.auth import b64, public_jwk
from myhermes.errors import CompanionError
from myhermes.relay_bridge import RelayBridge


def body(mode="normal", *, stream=False):
    return {"model": "economy", "messages": [{"role": "user", "content": mode}], "stream": stream}


def decode_jwt(value, key):
    header, payload, signature = value.split(".")
    signature_bytes = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    encoded = encode_dss_signature(
        int.from_bytes(signature_bytes[:32], "big"), int.from_bytes(signature_bytes[32:], "big")
    )
    key.public_key().verify(encoded, (header + "." + payload).encode(), ec.ECDSA(hashes.SHA256()))
    return tuple(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))) for part in (header, payload))


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def server_bind(self):
        # Numeric loopback fixtures must not depend on reverse DNS, including
        # fresh signal-test processes without the parent's resolver cache.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, *_):
        pass


@contextmanager
def company(*, completion_text=None, on_request=None, completion_factory=None):
    state = {
        "key": ec.generate_private_key(ec.SECP256R1()),
        "installation": str(uuid.uuid4()),
        "auth": [],
        "calls": [],
        "executions": {},
        "errors": [],
        "lock": threading.Lock(),
        "entered": threading.Event(),
        "cancelled": threading.Event(),
        "release": threading.Event(),
        "tokens": [],
        "user_agents": [],
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def response(self, status, value, media="application/json", **headers):
            raw = json.dumps(value).encode() if isinstance(value, dict) else value
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            for name, content in headers.items():
                self.send_header(name, content)
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()
            self.close_connection = True

        def do_GET(self):
            self.handle_api()

        def do_POST(self):
            self.handle_api()

        def handle_api(self):
            try:
                self._api()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                state["errors"].append(type(error).__name__)
                raise

        def _api(self):
            with state["lock"]:
                state["user_agents"].append((self.path, self.headers.get_all("User-Agent")))
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            value = json.loads(raw) if raw else None
            if self.path == "/v1/auth/token":
                header, claims = decode_jwt(value["assertion"], state["key"])
                assert header["alg"] == "ES256"
                assert claims["sub"] == state["installation"]
                assert claims["aud"] == state["origin"] + self.path
                with state["lock"]:
                    state["auth"].append(claims)
                    token = "synthetic.access." + str(len(state["auth"]))
                    state["tokens"].append(token)
                time.sleep(0.025)
                self.response(200, {"token_type": "DPoP", "access_token": token, "expires_in": 300})
                return
            token = self.headers["Authorization"].removeprefix("DPoP ")
            assert token in state["tokens"]
            header, claims = decode_jwt(self.headers["DPoP"], state["key"])
            assert header["typ"] == "dpop+jwt"
            assert header["jwk"] == public_jwk(state["key"])
            assert claims["ath"] == b64(hashlib.sha256(token.encode()).digest())
            assert claims["htm"] == self.command
            assert claims["htu"] == state["origin"] + self.path
            request_id = self.headers.get("MyHermes-Request-Id")
            with state["lock"]:
                state["calls"].append(
                    {"method": self.command, "path": self.path, "headers": dict(self.headers), "claims": claims}
                )
                prior = request_id in state["executions"]
                if request_id and not prior:
                    state["executions"][request_id] = 1
            if prior:
                self.response(
                    409,
                    {
                        "error": "relay_request_registered",
                        "receipt": {
                            "request_id": request_id,
                            "server_request_id": str(uuid.uuid4()),
                            "state": "completed",
                            "usage_state": "unknown",
                            "cost_nano": None,
                            "unsafe_extra": "synthetic-provider-secret",
                        },
                    },
                )
                return
            if self.command == "GET":
                self.response(200, {"object": "list", "data": [{"id": "economy", "object": "model"}]})
                return
            if completion_text is not None:
                if on_request is not None:
                    on_request(value)
                response_format = value.get("response_format") or {}
                text = completion_text
                if response_format.get("type") in ("json_schema", "json_object"):

                    def example(schema):
                        if not isinstance(schema, dict):
                            return None
                        if "enum" in schema:
                            return schema["enum"][0] if schema["enum"] else None
                        kind = schema.get("type")
                        if kind == "object" or "properties" in schema:
                            return {key: example(item) for key, item in schema.get("properties", {}).items()}
                        if kind == "boolean":
                            return False
                        if kind in ("number", "integer"):
                            return 0
                        if kind == "array":
                            return []
                        return ""

                    text = json.dumps(
                        example((response_format.get("json_schema") or {}).get("schema", {"type": "object"}))
                    )
                message = {"role": "assistant", "content": text}
                finish_reason = "stop"
                if completion_factory is not None and not response_format:
                    message, finish_reason = completion_factory(value)
                value_out = {
                    "id": "fixture-completion",
                    "object": "chat.completion",
                    "created": 1_783_641_600,
                    "model": "economy",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                }
                if value.get("stream"):
                    chunk = {key: value_out[key] for key in ("id", "created", "model")}
                    chunk.update(
                        {
                            "object": "chat.completion.chunk",
                            "choices": [{"index": 0, "delta": message, "finish_reason": None}],
                        }
                    )
                    ending = {
                        **chunk,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                        "usage": value_out["usage"],
                    }
                    data = (
                        b"data: "
                        + json.dumps(chunk).encode()
                        + b"\n\ndata: "
                        + json.dumps(ending).encode()
                        + b"\n\ndata: [DONE]\n\n"
                    )
                    self.response(200, data, media="text/event-stream")
                else:
                    self.response(200, value_out)
                return
            mode = value["messages"][0]["content"]
            if mode == "expired":
                self.response(410, {"error": "relay_request_id_expired"})
                return
            if mode == "lost":
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            if mode in ("stalled", "cancel"):
                state["entered"].set()
                readable, _, _ = select.select([self.connection], [], [], 3)
                if readable and self.connection.recv(1) == b"":
                    state["cancelled"].set()
                self.close_connection = True
                return
            if mode == "redirect":
                self.response(302, b"", Location="https://outside.example.invalid/leak")
                return
            if mode == "error":
                self.response(
                    429,
                    {"error": {"code": "synthetic-provider-secret", "message": "synthetic-provider-secret"}},
                    **{"Retry-After": "1", "Authorization": "synthetic-provider-secret"},
                )
                return
            if mode == "oversized":
                self.response(200, {"text": "x" * 1024})
                return
            if mode == "malformed":
                self.response(200, b"{synthetic-provider-secret")
                return
            if mode == "bad_media":
                self.response(200, b"synthetic-provider-secret", media="text/plain")
                return
            if value.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                first = b'data: {"choices":[{"delta":{"tool_calls":[{"id":"fixture","function":{"name":"search","arguments":"{}"}}]}}]}\r\n\r\n'
                self.wfile.write(first)
                self.wfile.flush()
                state["entered"].set()
                if mode == "paced":
                    state["release"].wait(2)
                if mode == "large_frame":
                    self.wfile.write(b"data: " + b"x" * 1024 + b"\n\n")
                elif mode != "truncated":
                    self.wfile.write(b'data: {"usage":{"prompt_tokens":1,"completion_tokens":2}}\n\ndata:[DONE]\n\n')
                self.wfile.flush()
                self.close_connection = True
                return
            self.response(
                200,
                {
                    "id": "fixture-completion",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "fixture",
                                        "type": "function",
                                        "function": {"name": "search", "arguments": "{}"},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                },
                **{"MyHermes-Server-Request-Id": str(uuid.uuid4())},
            )

    server = FixtureServer(("127.0.0.1", 0), Handler)
    state["origin"] = f"http://127.0.0.1:{server.server_port}"
    state["api"] = API(state["origin"], state["key"], state["installation"], timeout=1)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.025), daemon=True)
    thread.start()
    try:
        yield state
    finally:
        state["release"].set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class APITransportAcceptance(unittest.TestCase):
    def test_token_and_authenticated_requests_identify_the_companion(self):
        with company() as peer:
            status, _ = peer["api"].request("GET", "/v1/sync")
            self.assertEqual(status, 200)
            self.assertEqual(
                peer["user_agents"],
                [
                    ("/v1/auth/token", ["myhermes-companion/" + __version__]),
                    ("/v1/sync", ["myhermes-companion/" + __version__]),
                ],
            )
            self.assertEqual(peer["errors"], [])


class LoopbackStartupAcceptance(unittest.TestCase):
    def test_relay_and_both_synthetic_servers_start_without_reverse_dns(self):
        from connection_fixtures import github_http_fixture

        with patch("socket.getfqdn", side_effect=AssertionError("Numeric loopback needs no reverse DNS")) as reverse:
            with github_http_fixture(), company() as peer:
                with RelayBridge(peer["api"], allow_local_http=True) as bridge:
                    self.assertEqual(bridge._server.server_name, "127.0.0.1")
                    self.assertGreater(bridge._server.server_port, 0)
                    self.assertTrue(bridge.base_url.startswith("http://127.0.0.1:"))
            reverse.assert_not_called()


class BridgeAcceptance(unittest.TestCase):
    def setUp(self):
        context = company()
        self.company = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.bridge = RelayBridge(self.company["api"], allow_local_http=True)
        self.bridge.__enter__()
        self.addCleanup(self.bridge.close)

    def request(self, value=None, *, method="POST", path="/v1/chat/completions", headers=None):
        parsed = urllib.parse.urlsplit(self.bridge.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=4)
        raw = json.dumps(value).encode() if isinstance(value, dict) else value
        combined = {"Authorization": "Bearer " + self.bridge.session_token, "Content-Type": "application/json"}
        combined.update(headers or {})
        try:
            connection.request(method, path, raw, combined)
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            connection.close()

    def raw_request(self, request):
        parsed = urllib.parse.urlsplit(self.bridge.base_url)
        with socket.create_connection((parsed.hostname, parsed.port), timeout=3) as sock:
            sock.sendall(request)
            with sock.makefile("rb") as incoming:
                return incoming.read()

    def assert_no_fixture_errors(self):
        self.assertEqual(self.company["errors"], [])

    def test_normal_tool_call_models_and_device_credentials(self):
        status, headers, raw = self.request(body(), headers={"X-API-Key": "synthetic-unforwarded"})
        self.assertEqual(status, 200)
        self.assertEqual(uuid.UUID(headers["MyHermes-Request-Id"]).version, 7)
        self.assertEqual(json.loads(raw)["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "search")
        self.assertEqual(headers["x-should-retry"], "false")
        self.assertEqual(headers["Cache-Control"], "private, no-store")
        self.assertEqual(self.request(method="GET", path="/v1/models")[0], 200)
        self.assertEqual(len(self.company["auth"]), 1)
        self.assertEqual(len(self.company["calls"]), 2)
        for call in self.company["calls"]:
            self.assertNotIn(self.bridge.session_token, json.dumps(call))
            self.assertNotIn("X-API-Key", call["headers"])
        self.assert_no_fixture_errors()

    def test_expired_id_is_sanitized_and_never_renewed_for_an_uncertain_retry(self):
        first = self.request(body("expired"))
        self.assertEqual(first[0], 410)
        self.assertEqual(json.loads(first[2])["error"]["code"], "relay_request_id_expired")
        self.assertEqual(first[1]["x-should-retry"], "false")
        self.assertEqual(len(self.company["calls"]), 1)
        original_id = first[1]["MyHermes-Request-Id"]
        # Explicit retry reaches the existing fixture receipt with the same ID.
        # Advancing the local generator's clock cannot cause automatic renewal.
        with patch("myhermes.relay_ids.time.time_ns", return_value=2_000_000_000_000_000_000):
            second = self.request(body("expired"))
        self.assertEqual(second[1]["MyHermes-Request-Id"], original_id)
        self.assertEqual(len(self.company["calls"]), 2)
        self.assertEqual(set(self.company["executions"]), {original_id})

    def test_parallel_refresh_once_and_fresh_signed_dpop_per_request(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            replies = list(pool.map(lambda index: self.request(body("parallel-" + str(index))), range(5)))
        self.assertEqual([reply[0] for reply in replies], [200] * 5)
        self.assertEqual(len(self.company["auth"]), 1)
        self.assertEqual(len({call["claims"]["jti"] for call in self.company["calls"]}), 5)
        self.company["api"]._expires = 0
        self.assertEqual(self.request(body("refresh"))[0], 200)
        self.assertEqual(len(self.company["auth"]), 2)
        self.assert_no_fixture_errors()

    def test_local_auth_rejected_before_company_request(self):
        for authorization in ("", "Bearer wrong", "Béarer wrong"):
            self.assertEqual(self.request(body(), headers={"Authorization": authorization})[0], 401)
        raw = self.raw_request(
            b"GET /v1/models HTTP/1.1\r\nHost: localhost\r\nAuthorization: wrong\r\nAuthorization: wrong\r\n\r\n"
        )
        self.assertIn(b" 400 ", raw.split(b"\r\n")[0])
        self.assertEqual(self.company["auth"], [])
        self.assertEqual(self.company["calls"], [])

    def test_path_query_and_request_framing_rejected_without_forward(self):
        for path in (
            "/v1/models?key=secret",
            "https://elsewhere.invalid/v1/models",
            "/v1/relay/requests/id",
            "/v1/models#x",
        ):
            self.assertEqual(self.request(method="GET", path=path)[0], 404)
        for headers in ({"Transfer-Encoding": "chunked"}, {"X-MyHermes-Request-Id": str(uuid.uuid4())}):
            self.assertEqual(self.request(body(), headers=headers)[0], 400)
        self.assertEqual(self.request(body(), headers={"Content-Length": "262145"})[0], 413)
        for raw in (b'{"model":"economy","model":"x"}', b'{"number":NaN}', b'{"stream":1}', b"[]"):
            self.assertEqual(self.request(raw)[0], 400)
        self.assertEqual(self.company["auth"], [])

    def test_missing_length_and_duplicate_length_are_rejected(self):
        prefix = (
            "POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer "
            + self.bridge.session_token
            + "\r\nContent-Type: application/json\r\n"
        ).encode()
        self.assertIn(b" 411 ", self.raw_request(prefix + b"\r\n").split(b"\r\n")[0])
        self.assertIn(
            b" 400 ", self.raw_request(prefix + b"Content-Length: 2\r\nContent-Length: 3\r\n\r\n{}").split(b"\r\n")[0]
        )

    def test_success_duplicate_does_not_reexecute_or_cache_completion(self):
        original = self.request(body())
        second = self.request(json.dumps(body(), sort_keys=True, separators=(",", ":")).encode())
        self.assertEqual(second[0], 409)
        self.assertEqual(original[1]["MyHermes-Request-Id"], second[1]["MyHermes-Request-Id"])
        result = json.loads(second[2])
        self.assertEqual(result["error"]["code"], "relay_request_registered")
        self.assertEqual(result["receipt"]["usage_state"], "unknown")
        self.assertIsNone(result["receipt"]["cost_nano"])
        self.assertNotIn("unsafe_extra", result["receipt"])
        self.assertNotIn(b"fixture-completion", second[2])
        self.assertEqual(sum(self.company["executions"].values()), 1)
        self.assertEqual(len(self.bridge._digests), 1)

    def test_uncertain_transport_preserves_original_id_without_automatic_retry(self):
        first = self.request(body("lost"))
        self.assertEqual(first[0], 502)
        self.assertEqual(json.loads(first[2])["error"]["code"], "relay_transport_uncertain")
        self.assertEqual(len(self.company["calls"]), 1)
        second = self.request(body("lost"))
        self.assertEqual(second[0], 409)
        self.assertEqual(first[1]["MyHermes-Request-Id"], second[1]["MyHermes-Request-Id"])
        self.assertEqual(sum(self.company["executions"].values()), 1)

    def test_explicit_ids_exact_preservation_changed_body_and_new_id_fail_closed(self):
        request_id = str(uuid.uuid4()).upper()
        first = self.request(body(), headers={"MyHermes-Request-Id": request_id})
        self.assertEqual(first[1]["MyHermes-Request-Id"], request_id)
        self.assertEqual(self.company["calls"][0]["headers"]["MyHermes-Request-Id"], request_id)
        self.assertEqual(self.request(body("different"), headers={"MyHermes-Request-Id": request_id})[0], 409)
        self.assertEqual(self.request(body(), headers={"MyHermes-Request-Id": str(uuid.uuid4())})[0], 409)
        self.assertEqual(self.request(body("new"), headers={"MyHermes-Request-Id": "invalid"})[0], 400)
        self.assertEqual(len(self.company["calls"]), 1)

    def test_capacity_never_evicts_previous_id(self):
        self.bridge.max_requests = 1
        first = self.request(body())
        self.assertEqual(self.request(body("different"))[0], 429)
        repeat = self.request(body())
        self.assertEqual(repeat[0], 409)
        self.assertEqual(first[1]["MyHermes-Request-Id"], repeat[1]["MyHermes-Request-Id"])
        self.assertEqual(len(self.company["executions"]), 1)

    def test_errors_redirect_and_invalid_response_are_sanitized_without_logs(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            for mode, status, code in (
                ("redirect", 502, "relay_redirect_rejected"),
                ("error", 429, "relay_rejected"),
                ("malformed", 502, "relay_response_rejected"),
                ("bad_media", 502, "relay_response_rejected"),
            ):
                response = self.request(body(mode))
                self.assertEqual(response[0], status)
                self.assertEqual(json.loads(response[2])["error"]["code"], code)
                self.assertNotIn(b"synthetic-provider-secret", response[2])
                self.assertNotIn("Retry-After", response[1])
                self.assertNotIn("Authorization", response[1])
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
        self.assertEqual(len(self.company["calls"]), 4)

    def test_bounded_response_and_stream_frame(self):
        with patch("myhermes.relay_bridge.MAX_RESPONSE_BYTES", 512):
            response = self.request(body("oversized"))
        self.assertEqual(response[0], 502)
        self.assertEqual(json.loads(response[2])["error"]["code"], "relay_response_too_large")
        with patch("myhermes.relay_bridge.MAX_FRAME_BYTES", 256):
            response = self.request(body("large_frame", stream=True))
        self.assertEqual(response[0], 200)
        self.assertIn(b"relay_frame_too_large", response[2])
        self.assertNotIn(b"x" * 256, response[2])

    def test_sse_tool_usage_frames_preserved_and_incremental_before_eof(self):
        parsed = urllib.parse.urlsplit(self.bridge.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            "/v1/chat/completions",
            json.dumps(body("paced", stream=True)),
            {"Authorization": "Bearer " + self.bridge.session_token, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        first = response.readline()
        self.assertIn(b'"tool_calls"', first)
        self.assertTrue(first.endswith(b"\r\n"))
        self.assertFalse(self.company["release"].is_set())
        self.company["release"].set()
        rest = response.read()
        self.assertIn(b'"usage":{"prompt_tokens":1,"completion_tokens":2}', rest)
        self.assertTrue(rest.endswith(b"data:[DONE]\n\n"))

    def test_truncated_stream_is_explicit_error_without_fake_done(self):
        response = self.request(body("truncated", stream=True))
        self.assertEqual(response[0], 200)
        self.assertIn(b"relay_stream_interrupted", response[2])
        self.assertNotIn(b"[DONE]", response[2])

    def test_absolute_timeout_closes_upstream_and_preserves_request_id(self):
        self.bridge.timeout = 0.2
        start = time.monotonic()
        response = self.request(body("stalled"))
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(response[0], 504)
        self.assertEqual(json.loads(response[2])["error"]["code"], "relay_timeout")
        self.assertTrue(self.company["cancelled"].wait(1))
        self.assertIn("MyHermes-Request-Id", response[1])

    def test_client_disconnect_and_bridge_shutdown_cancel_upstream(self):
        parsed = urllib.parse.urlsplit(self.bridge.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        connection.request(
            "POST",
            "/v1/chat/completions",
            json.dumps(body("cancel")),
            {"Authorization": "Bearer " + self.bridge.session_token, "Content-Type": "application/json"},
        )
        self.assertTrue(self.company["entered"].wait(1))
        connection.close()
        self.assertTrue(self.company["cancelled"].wait(1))
        self.company["entered"].clear()
        self.company["cancelled"].clear()
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            "/v1/chat/completions",
            json.dumps(body("stalled")),
            {"Authorization": "Bearer " + self.bridge.session_token, "Content-Type": "application/json"},
        )
        self.assertTrue(self.company["entered"].wait(1))
        self.bridge.close()
        self.assertTrue(self.company["cancelled"].wait(1))

    def test_remote_http_origin_is_rejected_even_in_local_mode(self):
        for origin, allow in (("http://outside.example.invalid", True), (self.company["origin"], False)):
            with self.assertRaises(CompanionError):
                RelayBridge(API(origin), allow_local_http=allow)

    def test_invalid_token_response_fails_before_company_relay(self):
        for token in ("synthetic\r\nInjected: value", "synthetic space", "nonasciié"):
            with patch.object(
                self.company["api"],
                "_request",
                return_value=(200, {"token_type": "DPoP", "access_token": token, "expires_in": 300}),
            ):
                self.assertEqual(self.request(body(token))[0], 403)
        self.assertEqual(self.company["calls"], [])

    def test_model_observer_only_published_metadata_and_known_usage(self):
        events, recorded = [], threading.Event()
        self.bridge.on_activity = lambda kind, attrs: (events.append((kind, attrs)), recorded.set())
        first = self.request(body())
        self.assertTrue(recorded.wait(1))
        self.assertEqual(events[0][0], "model")
        self.assertEqual(events[0][1]["outcome"], "ok")
        self.assertEqual(events[0][1]["request_id"], first[1]["MyHermes-Request-Id"])
        self.assertEqual(events[0][1]["prompt_tokens"], 1)
        self.assertEqual(events[0][1]["completion_tokens"], 2)
        self.assertNotIn("cost_nano", events[0][1])
        self.assertEqual(
            set(events[0][1]),
            {"outcome", "request_id", "model_alias", "duration_ms", "prompt_tokens", "completion_tokens"},
        )
        recorded.clear()
        self.assertEqual(self.request(body("lost"))[0], 502)
        self.assertTrue(recorded.wait(1))
        self.assertEqual(events[-1][1]["outcome"], "unknown")
        self.assertNotIn("prompt_tokens", events[-1][1])
        recorded.clear()
        self.request(body("streamed", stream=True))
        self.assertTrue(recorded.wait(1))
        self.assertEqual(events[-1][1]["completion_tokens"], 2)

    def test_tool_endpoint_strict_local_only_and_plugin_normalizes_names(self):
        from myhermes import hermes_monitoring_plugin

        events = []
        self.bridge.on_activity = lambda kind, attrs: events.append((kind, attrs))
        value = {"tool_kind": "read", "outcome": "ok", "duration_ms": 12}
        self.assertEqual(self.request(value, path="/v1/myhermes/tool-events")[0], 202)
        self.assertEqual(events, [("tool", value)])
        for rejected in (
            {**value, "args": "synthetic-secret"},
            {**value, "tool_kind": "custom-private-name"},
            {**value, "duration_ms": True},
        ):
            self.assertEqual(self.request(rejected, path="/v1/myhermes/tool-events")[0], 400)
        self.assertEqual(self.company["auth"], [])
        with patch.dict(
            "os.environ",
            {
                "AUXILIARY_MYHERMES_API_KEY": self.bridge.session_token,
                "MYHERMES_MONITORING_URL": self.bridge.base_url + "/myhermes/tool-events",
            },
        ):
            hermes_monitoring_plugin.post_tool_call("read_file", 7, "ok")
            hermes_monitoring_plugin.post_tool_call("synthetic-secret-private-tool", 8, "error")
        self.assertEqual(
            events[-2:],
            [
                ("tool", {"tool_kind": "read", "outcome": "ok", "duration_ms": 7}),
                ("tool", {"tool_kind": "other", "outcome": "failed", "duration_ms": 8}),
            ],
        )
        self.assertNotIn("synthetic-secret", json.dumps(events))
        self.assertEqual(self.company["calls"], [])

    def test_observer_exception_never_breaks_relay_or_prints_secret(self):
        def broken(*_):
            raise RuntimeError("synthetic-secret")

        self.bridge.on_activity = broken
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(self.request(body())[0], 200)
            self.assertEqual(
                self.request(
                    {"tool_kind": "other", "outcome": "unknown", "duration_ms": 0}, path="/v1/myhermes/tool-events"
                )[0],
                202,
            )
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
