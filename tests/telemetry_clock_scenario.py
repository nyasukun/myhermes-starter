"""Generate only synthetic SDK/JWS metadata for a controlled server-clock test."""

import json
from pathlib import Path
import tempfile
import uuid

from cryptography.hazmat.primitives.asymmetric import ec

from myhermes.auth import public_jwk
from myhermes.telemetry import Telemetry
from myhermes.telemetry_state import DAY_NS

BASE_NS = 1_789_084_800_000_000_000
SKEW_NS = 300_000_000_000


def scenario(*, elapsed_ns=90 * DAY_NS + 1_000_000, acknowledged=("audit", "traces"), legacy=False):
    """Use real durable state and a restart; server acceptance is checked by Node."""
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw).resolve()
        config = {"installation_id": str(uuid.uuid4())}
        key = ec.generate_private_key(ec.SECP256R1())
        clock = [BASE_NS + SKEW_NS]
        telemetry = Telemetry(config, directory, None, key, clock_ns=lambda: clock[0])
        try:
            telemetry.record("sync", {"outcome": "ok"})
            first = telemetry.inspect(include_wire=True)["records"][0]
            cursor = telemetry.state.next(clock[0])["cursor"]
            for transport in acknowledged:
                telemetry.state.acknowledge(cursor, transport, clock[0])
            if legacy:
                telemetry.state.db.execute("DELETE FROM telemetry_metadata WHERE key='last_batch_ns'")
        finally:
            telemetry.close()
        clock[0] = BASE_NS + elapsed_ns
        telemetry = Telemetry(config, directory, None, key, clock_ns=lambda: clock[0])
        try:
            telemetry.record("sync", {"outcome": "ok"})
            state = telemetry.inspect(include_wire=True)
            return {
                "subject": {"person_id": "synthetic-owner", **config},
                "public_jwk": public_jwk(key),
                "server_first_ms": BASE_NS // 1_000_000,
                "server_second_ms": clock[0] // 1_000_000,
                "first": first,
                "second": state["records"][0],
                "same_stream": first["batch"]["stream_id"] == state["stream_id"],
                "state": state,
            }
        finally:
            telemetry.close()


if __name__ == "__main__":
    print(json.dumps(scenario(), separators=(",", ":")))
