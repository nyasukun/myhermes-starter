"""RFC 9562 UUIDv7 relay IDs on Python 3.11; no host identifier or retry renewal."""

import secrets
import time
import uuid


def new_request_id(now_ms=None):
    """Create once per logical inference. Keep these bytes for every retry."""
    timestamp = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    if type(timestamp) is not int or not 0 <= timestamp < 1 << 48:
        raise ValueError("Invalid relay request timestamp")
    random = secrets.randbits(74)
    value = (timestamp << 80) | (7 << 76) | ((random >> 62) << 64) | (2 << 62) | (random & ((1 << 62) - 1))
    # Python 3.11 rejects version=7; the explicit bits above implement RFC 9562.
    return str(uuid.UUID(int=value))
