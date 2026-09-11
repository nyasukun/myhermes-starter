"""Executable client mirror of public monitoring manifest 1.0.0. Metadata only."""

import copy
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from .errors import CompanionError

MANIFEST_VERSION = "1.0.0"
MAX_BODY_BYTES = 262_144
MAX_QUEUE_EVENTS = 10_000
OFFLINE_DAYS = 30
KINDS = frozenset(
    (
        "enrollment",
        "runtime_start",
        "runtime_stop",
        "runtime_update",
        "connection_change",
        "sync",
        "skill_change",
        "tool",
        "model",
        "delivery",
    )
)
OUTCOMES = frozenset(("ok", "failed", "conflict", "cancelled", "unknown"))
TOOL_KINDS = frozenset(("read", "write", "search", "execute", "connector", "other"))
ATTRIBUTE_FIELDS = frozenset(
    (
        "outcome",
        "duration_ms",
        "connection_id",
        "tool_kind",
        "model_alias",
        "prompt_tokens",
        "completion_tokens",
        "cost_nano",
        "request_id",
        "sent_count",
        "dropped_count",
    )
)


def rejected():
    raise CompanionError("telemetry_rejected", "Monitoring metadata does not match the published collection contract.")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", value
    ):
        rejected()
    return str(uuid.UUID(value))


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,6}\.\d{1,6}\.\d{1,6}", value, flags=re.ASCII):
        rejected()
    return value


def integer(value, maximum=9_007_199_254_740_991, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        rejected()
    return value


def timestamp(now_ns):
    return (
        datetime.fromtimestamp(now_ns // 1_000_000 / 1000, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def attributes(kind, value):
    if not isinstance(kind, str) or kind not in KINDS or type(value) is not dict or not set(value) <= ATTRIBUTE_FIELDS:
        rejected()
    if not isinstance(value.get("outcome"), str) or value["outcome"] not in OUTCOMES:
        rejected()
    result = copy.deepcopy(value)
    if "duration_ms" in result:
        integer(result["duration_ms"], 86_400_000)
    for key in ("connection_id", "request_id"):
        if key in result:
            result[key] = identifier(result[key])
    if "tool_kind" in result and (
        kind != "tool" or not isinstance(result["tool_kind"], str) or result["tool_kind"] not in TOOL_KINDS
    ):
        rejected()
    if "model_alias" in result and (kind != "model" or result["model_alias"] != "economy"):
        rejected()
    for key in ("prompt_tokens", "completion_tokens", "cost_nano"):
        if key in result:
            if kind != "model":
                rejected()
            integer(result[key], 1_000_000_000_000 if key == "cost_nano" else 100_000_000)
    for key in ("sent_count", "dropped_count"):
        if key in result:
            if kind != "delivery":
                rejected()
            integer(result[key], 100_000_000)
    return result


def validate_batch(value):
    """Validate stored metadata too, before inspection or transmission; age handled by state."""
    fields = {
        "schema_version",
        "manifest_version",
        "client_version",
        "batch_id",
        "stream_id",
        "first_sequence",
        "previous_batch_sha256",
        "events",
    }
    if (
        type(value) is not dict
        or set(value) != fields
        or value["schema_version"] != "1"
        or value["manifest_version"] != MANIFEST_VERSION
    ):
        rejected()
    version(value["client_version"])
    for key in ("batch_id", "stream_id"):
        if value[key] != identifier(value[key]):
            rejected()
    first = integer(value["first_sequence"], minimum=1)
    previous = value["previous_batch_sha256"]
    if previous is not None and (
        not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{64}", previous) or previous == "0" * 64
    ):
        rejected()
    if type(value["events"]) is not list or not 1 <= len(value["events"]) <= 100:
        rejected()
    for offset, event in enumerate(value["events"]):
        required = {"event_id", "sequence", "client_time", "kind", "attributes", "trace_id", "span_id"}
        if (
            type(event) is not dict
            or set(event) != required
            or event["event_id"] != identifier(event["event_id"])
            or event["sequence"] != first + offset
        ):
            rejected()
        integer(event["sequence"], minimum=1)
        attributes(event["kind"], event["attributes"])
        when = event["client_time"]
        if not isinstance(when, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", when, flags=re.ASCII
        ):
            rejected()
        try:
            datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            rejected()
        for key, size in (("trace_id", 32), ("span_id", 16)):
            item = event[key]
            if (
                not isinstance(item, str)
                or not re.fullmatch(r"[a-f0-9]{" + str(size) + r"}", item)
                or item == "0" * size
            ):
                rejected()
    if len(canonical(value).encode()) > MAX_BODY_BYTES:
        rejected()
    return copy.deepcopy(value)
