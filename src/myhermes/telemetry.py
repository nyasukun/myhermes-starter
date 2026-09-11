"""Explicit metadata instrumentation; never auto-instrument user code or global OTel."""

import base64
import json
import time
import urllib.error
import urllib.request
import uuid

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import SpanKind, Status, StatusCode

from . import __version__
from .auth import sign_jwt
from .errors import CompanionError
from .telemetry_contract import (
    MANIFEST_VERSION,
    MAX_BODY_BYTES,
    attributes,
    canonical,
    digest,
    identifier,
    timestamp,
    version,
)
from .telemetry_state import TelemetryState

AUDIT_PATH = "/v1/monitoring/audit"
TRACES_PATH = "/v1/monitoring/traces"


class _Capture(SpanProcessor):
    def __init__(self):
        self.value = None

    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span):
        self.value = span

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


def _unbase64(value):
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def span_attributes(event):
    return {
        "myhermes.manifest_version": MANIFEST_VERSION,
        **{"myhermes." + key: event[key] for key in ("event_id", "sequence", "client_time", "kind")},
        **{"myhermes." + key: value for key, value in event["attributes"].items()},
    }


def _validate_proto(raw, batch):
    """Stored bytes are untrusted too; do not send new fields after local corruption."""
    if not isinstance(raw, bytes) or len(raw) > MAX_BODY_BYTES:
        raise ValueError("Invalid stored telemetry")
    message = ExportTraceServiceRequest.FromString(raw)
    # Check the fields known to this SDK as well as unknown wire fields. New
    # protobuf schema additions must never silently expand this collector.
    allowed = {
        "ExportTraceServiceRequest": {"resource_spans"},
        "ResourceSpans": {"resource", "scope_spans"},
        "Resource": {"attributes"},
        "ScopeSpans": {"scope", "spans"},
        "InstrumentationScope": {"name", "version"},
        "Span": {
            "trace_id",
            "span_id",
            "flags",
            "name",
            "kind",
            "start_time_unix_nano",
            "end_time_unix_nano",
            "attributes",
            "status",
        },
        "Status": {"code"},
        "KeyValue": {"key", "value"},
        "AnyValue": {"string_value", "int_value"},
    }

    def check_fields(node):
        for field, value in node.ListFields():
            if field.name not in allowed.get(node.DESCRIPTOR.name, set()):
                raise ValueError("Invalid stored telemetry")
            if field.message_type is not None:
                for child in value if field.is_repeated else (value,):
                    check_fields(child)

    check_fields(message)
    # A malicious/accidentally altered unknown protobuf field must not leave the host.
    message.DiscardUnknownFields()
    if message.SerializeToString() != raw or len(message.resource_spans) != 1:
        raise ValueError("Invalid stored telemetry")
    resource = message.resource_spans[0]
    expected_resource = {"service.name": "myhermes", "service.version": batch["client_version"]}
    actual_resource = {entry.key: entry.value.string_value for entry in resource.resource.attributes}
    if (
        len(resource.resource.attributes) != 2
        or actual_resource != expected_resource
        or resource.resource.dropped_attributes_count
        or resource.schema_url
        or len(resource.scope_spans) != 1
    ):
        raise ValueError("Invalid stored telemetry")
    scope_spans = resource.scope_spans[0]
    scope = scope_spans.scope
    if (
        scope.name != "myhermes"
        or scope.version != batch["client_version"]
        or scope.attributes
        or scope.dropped_attributes_count
        or scope_spans.schema_url
        or len(scope_spans.spans) != len(batch["events"])
    ):
        raise ValueError("Invalid stored telemetry")
    for span, event in zip(scope_spans.spans, batch["events"]):
        if (
            span.name != "myhermes.activity"
            or span.kind != 1
            or span.events
            or span.links
            or span.status.message
            or span.status.code > 2
            or span.parent_span_id
            or span.trace_state
            or span.dropped_attributes_count
            or span.dropped_events_count
            or span.dropped_links_count
            or span.flags > 1023
        ):
            raise ValueError("Invalid stored telemetry")
        if (
            span.trace_id.hex() != event["trace_id"]
            or span.span_id.hex() != event["span_id"]
            or span.end_time_unix_nano < span.start_time_unix_nano
            or span.end_time_unix_nano - span.start_time_unix_nano > 86_400_000_000_000
        ):
            raise ValueError("Invalid stored telemetry")
        actual = {}
        for attr in span.attributes:
            selected = attr.value.WhichOneof("value")
            if selected not in ("string_value", "int_value") or attr.key in actual:
                raise ValueError("Invalid stored telemetry")
            actual[attr.key] = getattr(attr.value, selected)
        if actual != span_attributes(event):
            raise ValueError("Invalid stored telemetry")
    return message


class Telemetry:
    def __init__(self, config, directory, api, private_key, *, client_version=None, clock_ns=time.time_ns):
        self.installation_id = identifier(config["installation_id"])
        self.client_version = version(__version__ if client_version is None else client_version)
        self.api, self.private_key, self.clock_ns = api, private_key, clock_ns
        if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(private_key.curve, ec.SECP256R1):
            raise CompanionError(
                "telemetry_key_unavailable", "Monitoring requires this installation's native-store signing key."
            )
        self.state = TelemetryState(directory, self.installation_id)
        self.capture = _Capture()
        self.provider = TracerProvider(
            sampler=ALWAYS_ON,
            resource=Resource({"service.name": "myhermes", "service.version": self.client_version}),
            shutdown_on_exit=False,
            span_limits=SpanLimits(max_attributes=24, max_events=0, max_links=0, max_attribute_length=128),
        )
        self.provider.add_span_processor(self.capture)
        self.tracer = self.provider.get_tracer("myhermes", self.client_version)

    def close(self):
        self.provider.shutdown()
        self.state.close()

    def _record(self, kind, attrs, *, delivery=False):
        attrs = attributes(kind, attrs)
        now_ns = self.clock_ns()

        def make(sequence, stream_id, previous, dropped):
            event = {
                "event_id": str(uuid.uuid4()),
                "sequence": sequence,
                "client_time": timestamp(now_ns),
                "kind": kind,
                "attributes": dict(attrs),
            }
            if delivery:
                event["attributes"]["dropped_count"] = dropped
            duration = event["attributes"].get("duration_ms", 0)
            self.capture.value = None
            span = self.tracer.start_span(
                "myhermes.activity",
                context=Context(),
                kind=SpanKind.INTERNAL,
                attributes=span_attributes(event),
                start_time=now_ns - duration * 1_000_000,
                record_exception=False,
                set_status_on_exception=False,
            )
            if not span.is_recording():
                raise CompanionError(
                    "telemetry_sdk_disabled", "The configured OpenTelemetry SDK is not recording metadata."
                )
            context = span.get_span_context()
            event.update(trace_id=f"{context.trace_id:032x}", span_id=f"{context.span_id:016x}")
            if attrs["outcome"] == "ok":
                span.set_status(Status(StatusCode.OK))
            elif attrs["outcome"] == "failed":
                span.set_status(Status(StatusCode.ERROR))
            span.end(end_time=now_ns)
            if self.capture.value is None:
                raise CompanionError(
                    "telemetry_sdk_unavailable", "The OpenTelemetry SDK did not produce a metadata span."
                )
            traces = encode_spans([self.capture.value]).SerializeToString()
            batch = {
                "schema_version": "1",
                "manifest_version": MANIFEST_VERSION,
                "client_version": self.client_version,
                "batch_id": str(uuid.uuid4()),
                "stream_id": stream_id,
                "first_sequence": sequence,
                "previous_batch_sha256": previous,
                "events": [event],
            }
            jws = sign_jwt(
                self.private_key, {"alg": "ES256", "typ": "myhermes-audit+jws", "kid": self.installation_id}, batch
            )
            if len(jws.encode()) > MAX_BODY_BYTES or len(traces) > MAX_BODY_BYTES:
                raise CompanionError("telemetry_rejected", "Encoded metadata exceeded the public transport size limit.")
            _validate_proto(traces, batch)
            return batch, jws, traces

        result = self.state.queue(make, now_ns, delivery=delivery)
        return result["events"][0] if result else None

    def record(self, kind, attrs):
        """Only fixed enum kinds and documented scalar attributes, never arbitrary context."""
        return self._record(kind, attrs)

    def _validate_delivery(self, row):
        try:
            parts = row["audit_jws"].split(".")
            if len(parts) != 3 or len(row["audit_jws"]) > MAX_BODY_BYTES:
                raise ValueError("Invalid stored telemetry")
            header, payload, signature = map(_unbase64, parts)
            if (
                json.loads(header) != {"alg": "ES256", "typ": "myhermes-audit+jws", "kid": self.installation_id}
                or payload.decode("utf-8") != canonical(row["batch"])
                or len(signature) != 64
                or row["batch_sha256"] != digest(row["batch"])
            ):
                raise ValueError("Invalid stored telemetry")
            der = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
            self.private_key.public_key().verify(
                der, (parts[0] + "." + parts[1]).encode("ascii"), ec.ECDSA(hashes.SHA256())
            )
            _validate_proto(row["traces"], row["batch"])
        except Exception:
            raise CompanionError(
                "telemetry_corrupt", "Queued monitoring metadata failed its integrity check; nothing was sent."
            ) from None

    def _send(self, path, raw, content_type):
        headers = {
            "Content-Type": content_type,
            "Accept": "application/x-protobuf" if path == TRACES_PATH else "application/json",
            **self.api.auth_headers("POST", path),
        }
        request = urllib.request.Request(self.api.server + path, data=raw, headers=headers, method="POST")
        try:
            try:
                response = self.api.opener.open(request, timeout=self.api.timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status, response_type = response.status, response.headers.get("Content-Type", "").split(";")[0].strip()
                data = response.read(4097)
            if status != 200 or len(data) > 4096:
                raise ValueError("Deferred metadata delivery")
            return response_type, data
        except (OSError, ValueError, urllib.error.URLError):
            raise CompanionError(
                "telemetry_deferred", "Monitoring delivery was not acknowledged; safe metadata remains queued.", 4
            ) from None

    def flush(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Flush limit must be from 1 to 1000")
        sent = 0
        deferred = False
        for _ in range(limit):
            row = self.state.next(self.clock_ns())
            # Queue a durable drop counter as soon as chain capacity is available.
            self._record("delivery", {"outcome": "ok"}, delivery=True)
            if row is None:
                row = self.state.next(self.clock_ns())
            if row is None:
                break
            self._validate_delivery(row)
            try:
                if not row["audit_sent"]:
                    content_type, data = self._send(AUDIT_PATH, row["audit_jws"].encode("ascii"), "application/jose")
                    ack = json.loads(data)
                    expected_count = (
                        0 if type(ack) is dict and ack.get("status") == "duplicate" else len(row["batch"]["events"])
                    )
                    if (
                        content_type != "application/json"
                        or type(ack) is not dict
                        or ack.get("status") not in ("accepted", "duplicate")
                        or ack.get("batch_id") != row["batch"]["batch_id"]
                        or ack.get("batch_sha256") != row["batch_sha256"]
                        or type(ack.get("last_sequence")) is not int
                        or ack["last_sequence"] != row["batch"]["events"][-1]["sequence"]
                        or type(ack.get("accepted")) is not int
                        or ack["accepted"] != expected_count
                    ):
                        raise ValueError("Unconfirmed metadata receipt")
                    self.state.acknowledge(row["cursor"], "audit", self.clock_ns())
                if not row["traces_sent"]:
                    content_type, data = self._send(TRACES_PATH, row["traces"], "application/x-protobuf")
                    if content_type != "application/x-protobuf" or data != b"":
                        raise ValueError("Unconfirmed telemetry receipt")
                    self.state.acknowledge(row["cursor"], "traces", self.clock_ns())
                sent += 1
            except (CompanionError, ValueError, UnicodeError):
                deferred = True
                break
        return {
            "sent_now": sent,
            "deferred": deferred,
            **{key: value for key, value in self.state.inspect(limit=1).items() if key != "records"},
        }

    def inspect(self, *, include_wire=False, limit=100):
        """Owner-visible exact safe batches and, explicitly, signed/wire payloads."""
        from google.protobuf.json_format import MessageToDict

        result = self.state.inspect(include_wire=True, limit=limit)
        for item in result["records"]:
            row = {**item, "traces": base64.b64decode(item["traces_base64"], validate=True)}
            self._validate_delivery(row)
            if include_wire:
                item["otlp"] = MessageToDict(
                    _validate_proto(row["traces"], item["batch"]), preserving_proto_field_name=True
                )
            else:
                item.pop("audit_jws")
                item.pop("traces_base64")
        return result
