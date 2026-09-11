"""Nonfatal, durable delivery of complete local-application revision reports."""

from datetime import datetime
from importlib.resources import files
import json
import re

from .errors import CompanionError


def manifest():
    value = json.loads(files("myhermes").joinpath("sync-status-manifest.json").read_text(encoding="utf-8"))
    if value.get("version") != "1.0.0" or value.get("submitted_fields") != ["schema_version", "revision"]:
        raise CompanionError("sync_ack_manifest_invalid", "The installed application-report manifest is inconsistent.")
    return value


def _revision(value):
    if type(value) is not int or not 0 <= value <= 9_007_199_254_740_991:
        raise ValueError()
    return value


def _receipt(value, *, rejection=False):
    fields = {"error" if rejection else "status", "source", "applied_revision", "received_at"}
    if not isinstance(value, dict) or set(value) != fields or value["source"] != "client_reported":
        raise ValueError()
    _revision(value["applied_revision"])
    stamp = value["received_at"]
    if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", stamp):
        raise ValueError()
    datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if (rejection and value["error"] != "sync_ack_regression") or (
        not rejection and value["status"] not in ("accepted", "duplicate")
    ):
        raise ValueError()
    return value


class SyncAcknowledgements:
    def __init__(self, state, api):
        self.state, self.api = state, api

    def _retire(self, revision, receipt):
        with self.state.db:
            self.state.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('sync_ack_receipt',?)",
                (json.dumps({"installation_id": self.api.installation_id, "receipt": receipt}),),
            )
            # A newer concurrent checkpoint must not be cleared by an older reply.
            self.state.db.execute(
                "DELETE FROM metadata WHERE key='sync_ack_pending' AND value=?", (json.dumps(revision),)
            )

    def flush(self):
        try:
            pending = self.state.get("sync_ack_pending")
            if pending is None:
                return {"status": "none_pending"}
            _revision(pending)
            if pending > self.state.revision:
                raise ValueError()
            status, value = self.api.request("POST", "/v1/sync/ack", {"schema_version": "1", "revision": pending})
            if status == 409 and value == {"error": "sync_ack_future_revision"}:
                result = {"status": "rejected", "error": "sync_ack_future_revision", "reported_revision": pending}
                self._retire(pending, result)
                return result
            if status == 409:
                receipt = _receipt(value, rejection=True)
                if receipt["applied_revision"] <= pending:
                    raise ValueError()
                result = {"status": "superseded", "reported_revision": pending, "retained_report": receipt}
                self._retire(pending, result)
                return result
            if status != 200:
                raise ValueError()
            receipt = _receipt(value)
            if receipt["applied_revision"] != pending:
                raise ValueError()
            self._retire(pending, receipt)
            return receipt
        except CompanionError as error:
            return {"status": "deferred", "error": error.code}
        except Exception:
            return {"status": "deferred", "error": "sync_ack_invalid"}


def inspect_ack(state, installation_id):
    try:
        pending = state.get("sync_ack_pending")
        if pending is not None:
            _revision(pending)
        saved = state.get("sync_ack_receipt")
        last = None
        if saved is not None:
            if not isinstance(saved, dict) or set(saved) != {"installation_id", "receipt"}:
                raise ValueError()
            if saved["installation_id"] == installation_id:
                last = saved["receipt"]
                if not isinstance(last, dict):
                    raise ValueError()
                if last.get("status") in ("accepted", "duplicate"):
                    _receipt(last)
                elif last.get("status") == "rejected":
                    if (
                        set(last) != {"status", "error", "reported_revision"}
                        or last["error"] != "sync_ack_future_revision"
                    ):
                        raise ValueError()
                    _revision(last["reported_revision"])
                elif last.get("status") == "superseded":
                    if set(last) != {"status", "reported_revision", "retained_report"}:
                        raise ValueError()
                    _revision(last["reported_revision"])
                    _receipt(last["retained_report"], rejection=True)
                else:
                    raise ValueError()
        return {"status": "local", "pending_revision": pending, "last_receipt": last}
    except Exception:
        return {"status": "invalid", "error": "sync_ack_invalid"}
