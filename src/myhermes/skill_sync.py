"""Explicit owner skill imports, per-skill causal sync, and company distribution."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import re
import uuid

from .errors import CompanionError
from .skill_packages import package_digest, parse_package, skill_id, skill_version
from .skill_state import package_hash


def revision(value):
    if type(value) is not int or not 0 <= value < 10**15:
        raise CompanionError("skill_response_rejected", "The skill response has an invalid revision.")
    return value


def identifier(value, *, nullable=False):
    if value is None and nullable:
        return None
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value.lower():
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise CompanionError("skill_response_rejected", "Invalid skill identifier metadata.") from None
    return value


def metadata(value, *, company=False, tombstone=True):
    expected = {"skill_id", "revision", "sha256", "version", "description"}
    expected |= {"publication_id", "published_at"} if company else {"installation_id", "created_at", "update_id"}
    if not company and tombstone:
        expected.add("deleted")
    if not isinstance(value, dict) or set(value) != expected:
        raise CompanionError("skill_response_rejected", "The skill metadata does not match the public contract.")
    skill_id(value["skill_id"])
    if revision(value["revision"]) == 0:
        raise CompanionError("skill_response_rejected", "Published skill metadata requires a positive revision.")
    deleted = value["sha256"] is None
    if company and deleted:
        raise CompanionError("skill_response_rejected", "A company publication cannot contain a tombstone.")
    if not company and tombstone and (type(value["deleted"]) is not bool or value["deleted"] != deleted):
        raise CompanionError("skill_response_rejected", "Invalid skill deletion metadata.")
    if deleted:
        if value["version"] is not None or value["description"] is not None:
            raise CompanionError("skill_response_rejected", "A tombstone must not contain package metadata.")
    else:
        if not isinstance(value["sha256"], str) or not re.fullmatch("[a-f0-9]{64}", value["sha256"]):
            raise CompanionError("skill_response_rejected", "Invalid package metadata hash.")
        skill_version(value["version"])
        if not isinstance(value["description"], str) or not 1 <= len(value["description"]) <= 500:
            raise CompanionError("skill_response_rejected", "Invalid package description metadata.")
    if company:
        identifier(value["publication_id"])
    else:
        identifier(value["installation_id"], nullable=True)
        identifier(value["update_id"])
    timestamp = value["published_at" if company else "created_at"]
    if not isinstance(timestamp, str) or not 1 <= len(timestamp) <= 64 or any(ord(char) < 32 for char in timestamp):
        raise CompanionError("skill_response_rejected", "Invalid skill timestamp metadata.")
    return deepcopy(value)


def snapshot(value, name=None, *, company=False):
    keys = {"revision", "skill_id", "package", "sha256"} | ({"publication_id"} if company else set())
    if not isinstance(value, dict) or set(value) != keys:
        raise CompanionError("skill_response_rejected", "The skill snapshot does not match the public contract.")
    revision(value["revision"])
    skill_id(value["skill_id"])
    if name is not None and value["skill_id"] != name:
        raise CompanionError("skill_response_rejected", "The skill response identifies a different package.")
    package = parse_package(value["package"]) if value["package"] is not None else None
    if package is not None and package["skill_id"] != value["skill_id"]:
        raise CompanionError("skill_response_rejected", "The package and snapshot IDs differ.")
    if package_hash(package) != value["sha256"]:
        raise CompanionError("skill_response_rejected", "The skill snapshot hash did not match its complete package.")
    if company:
        try:
            uuid.UUID(value["publication_id"])
        except (ValueError, TypeError, AttributeError):
            raise CompanionError("skill_response_rejected", "Invalid company publication identifier.") from None
    return deepcopy(value)


def renamed_package(package, name, version, *, derived_from=None):
    result = deepcopy(package)
    skill_id(name)
    skill_version(version)
    old_name = result["skill_id"]
    result.update(skill_id=name, version=version)
    if derived_from is not None:
        result["derived_from"] = derived_from
    for file in result["files"]:
        if file["path"] == "SKILL.md":
            raw = base64.b64decode(file["content_base64"]).decode("utf-8")
            raw = raw.replace("name: " + old_name, "name: " + name, 1).encode("utf-8")
            file.update(content_base64=base64.b64encode(raw).decode("ascii"), sha256=hashlib.sha256(raw).hexdigest())
    return parse_package(result)


def same_package_except_version(left, right):
    """A resolution's explicit version label alone does not change its ancestor."""
    if left is None or right is None:
        return left is right
    # Compare every file, capability requirement, description and derivation
    # field. This shallow replacement never changes either stored/wire package.
    return {**left, "version": right["version"]} == right


class SkillSynchronizer:
    def __init__(self, state, home, api=None, *, requirement_check=None, defer_activation=False):
        self.state, self.home, self.api = state, home, api
        self.requirement_check = requirement_check or self._default_requirements
        self.defer_activation = defer_activation

    @staticmethod
    def _default_requirements(package):
        return [
            "connector:" + row["connector_id"] + "@" + row["version"]
            for row in package["requires"]["connectors"]
            if (row["connector_id"], row["version"]) != ("github", "1.0.0")
        ] + ["connection:" + value for value in package["requires"]["connections"]]

    def _request(self, method, path, payload=None):
        if self.api is None:
            raise CompanionError("not_enrolled", "Enroll this environment before skill library access.", 3)
        return self.api.request(method, path, payload)

    def _get(self, path):
        status, value = self._request("GET", path)
        if status != 200:
            raise CompanionError(
                "skill_library_changed",
                "The library changed during retrieval; retry without overwriting local files.",
                6,
            )
        return value

    def _active(self, name):
        return [
            row for row in self.state.outbox("pending", "conflict", "rejected") if row["payload"]["skill_id"] == name
        ]

    def _working(self, name):
        return self.state.get("working:" + name, {"package": None, "base_revision": 0})

    def _baseline(self, name):
        return self.state.get("baseline:" + name, {"revision": 0, "skill_id": name, "package": None, "sha256": None})

    def _new_update(self, name, package, base_revision, *, resolves=None, expected=None):
        payload = {
            "schema_version": "1",
            "update_id": str(uuid.uuid4()),
            "base_revision": base_revision,
            "skill_id": name,
            "package": deepcopy(package),
        }
        if resolves:
            payload["resolves_update_id"] = resolves
        return {"payload": payload, "expected": deepcopy(expected)}

    def _apply(self, scope, name, package, *, values=(), queued=(), transitions=(), accept_drift=False):
        missing = self.requirement_check(package) if package is not None else []
        if self.defer_activation:
            if scope != "personal":
                raise CompanionError("home_busy", "Company activation waits for the managed session boundary.", 7)
            if self.state.get("journal") is not None:
                raise CompanionError(
                    "skill_recovery_pending", "Recover the interrupted activation before importing another package.", 6
                )
            self.state.transaction(
                values=[
                    *values,
                    ("deferred:" + name, {"adopt_current": accept_drift}),
                    ("missing:personal:" + name, missing),
                ],
                queued=queued,
                transitions=transitions,
            )
            return missing
        self.state.apply(
            self.home,
            scope,
            name,
            package if not missing else None,
            values=[
                *values,
                ("missing:" + scope + ":" + name, missing),
                *(([("deferred:" + name, None)]) if scope == "personal" else []),
            ],
            queued=queued,
            transitions=transitions,
            accept_drift=accept_drift,
        )
        return missing

    def import_package(self, package, *, accept_drift=False):
        package = parse_package(package)
        name = package["skill_id"]
        if not self.defer_activation:
            self.state.recover(self.home)
        working = self._working(name)
        active = self._active(name)
        rejected = [row for row in active if row["status"] == "rejected"]
        # A changed import may replace a locally rejected immutable-version
        # payload. Accepted/conflicted requests retain their original bytes.
        if rejected and any(
            row["response"] == {"error": "skill_version_reused"}
            and package_hash(row["payload"]["package"]) == package_hash(package)
            for row in rejected
        ):
            raise CompanionError("skill_version_reused", "Choose a new explicit version for changed package bytes.", 6)
        transitions = [
            {"update_id": row["update_id"], "status": "resolved", "response": row["response"]} for row in rejected
        ]
        queued = []
        if (
            not any(row["status"] != "rejected" for row in active)
            and package_hash(package) != self._baseline(name)["sha256"]
        ):
            queued = [self._new_update(name, package, working["base_revision"])]
        missing = self._apply(
            "personal",
            name,
            package,
            values=[("working:" + name, {"package": package, "base_revision": working["base_revision"]})],
            queued=queued,
            transitions=transitions,
            accept_drift=accept_drift,
        )
        return {
            "status": "imported",
            "scope": "personal",
            "skill_id": name,
            "version": package["version"],
            "sha256": package_digest(package),
            "queued": bool(queued),
            "missing_requirements": missing,
            "activation": "after_session" if self.defer_activation else "applied",
        }

    def delete(self, name):
        skill_id(name)
        if not self.defer_activation:
            self.state.recover(self.home)
        working = self._working(name)
        active = self._active(name)
        if working["package"] is None and self._baseline(name)["package"] is None and not active:
            return {"status": "already_absent", "skill_id": name, "queued": False}
        rejected = [
            {"update_id": row["update_id"], "status": "resolved", "response": row["response"]}
            for row in active
            if row["status"] == "rejected"
        ]
        queued = (
            []
            if any(row["status"] != "rejected" for row in active) or self._baseline(name)["package"] is None
            else [self._new_update(name, None, working["base_revision"])]
        )
        self._apply(
            "personal",
            name,
            None,
            values=[("working:" + name, {"package": None, "base_revision": working["base_revision"]})],
            queued=queued,
            transitions=rejected,
        )
        return {"status": "deleted_locally", "skill_id": name, "queued": bool(queued)}

    def restore_runtime(self, scope, name):
        skill_id(name)
        original = self.state.get("company:" + name) if scope == "company" else self.state.get("working:" + name)
        package = original["package"] if original else None
        missing = self.requirement_check(package) if package is not None else []
        result = self.state.restore_runtime(
            self.home,
            scope,
            name,
            package if not missing else None,
            values=[("missing:" + scope + ":" + name, missing)],
        )
        return {**result, "missing_requirements": missing}

    def _queue_dirty(self):
        queued = []
        for name, working in self.state.records("working:").items():
            if not self._active(name) and package_hash(working["package"]) != self._baseline(name)["sha256"]:
                queued.append(self._new_update(name, working["package"], working["base_revision"]))
        self.state.transaction(queued=queued)

    def _conflict(self, update_id):
        value = self._get("/v1/skills/personal/conflicts/" + str(uuid.UUID(update_id)))
        expected = {
            "mutation",
            "current",
            "revision",
            "installation_id",
            "created_at",
            "resolved_by",
            "resolved_revision",
        }
        if not isinstance(value, dict) or set(value) != expected or not isinstance(value["mutation"], dict):
            raise CompanionError("skill_response_rejected", "Invalid skill conflict response.")
        mutation = value["mutation"]
        required = {"schema_version", "update_id", "base_revision", "skill_id", "package"}
        if (
            not required.issubset(mutation)
            or set(mutation) - required - {"resolves_update_id"}
            or mutation.get("schema_version") != "1"
            or mutation.get("update_id") != update_id
        ):
            raise CompanionError("skill_response_rejected", "The conflict receipt identifies another update.")
        identifier(mutation["update_id"])
        if "resolves_update_id" in mutation:
            identifier(mutation["resolves_update_id"])
        name = skill_id(mutation.get("skill_id"))
        revision(mutation.get("base_revision"))
        if mutation.get("package") is not None:
            package = parse_package(mutation["package"])
            if package["skill_id"] != name:
                raise CompanionError("skill_response_rejected", "The conflict package identifies another skill.")
        local = next((row for row in self.state.outbox() if row["update_id"] == update_id), None)
        if local is not None and local["payload"] != mutation:
            raise CompanionError(
                "skill_response_rejected", "The retained conflict differs from the immutable local request."
            )
        snapshot(value["current"], name)
        revision(value["revision"])
        if value["resolved_by"] is not None:
            try:
                uuid.UUID(value["resolved_by"])
                revision(value["resolved_revision"])
            except (ValueError, TypeError, AttributeError):
                raise CompanionError("skill_response_rejected", "Invalid resolution receipt.") from None
        return value

    def _reconcile_resolved(self):
        for original in self.state.outbox("conflict"):
            receipt = self._conflict(original["update_id"])
            if receipt["resolved_by"] is None:
                continue
            pending = next(
                (
                    row
                    for row in self.state.outbox("pending")
                    if row["payload"].get("resolves_update_id") == original["update_id"]
                ),
                None,
            )
            if pending and receipt["resolved_by"] == pending["update_id"]:
                continue  # Replay the immutable ACK before considering staleness.
            name = original["payload"]["skill_id"]
            remote = snapshot(self._get("/v1/skills/personal/history/" + str(receipt["resolved_revision"])), name)
            working = self._working(name)
            chosen = working["package"]
            changed = package_hash(chosen) != package_hash(original["payload"]["package"])
            if pending:
                # The portal won against a local explicit choice. Retain that
                # choice (or an even newer import), never silently adopt it at
                # the portal revision and overwrite the remote selection.
                if package_hash(chosen) == package_hash(pending["expected"]):
                    chosen = pending["payload"]["package"]
                changed = package_hash(chosen) != remote["sha256"]
            queued = []
            if changed and package_hash(chosen) != remote["sha256"]:
                queued = [self._new_update(name, chosen, original["payload"]["base_revision"])]
                base = original["payload"]["base_revision"]
            else:
                chosen, base = remote["package"], remote["revision"]
            transitions = [{"update_id": original["update_id"], "status": "resolved", "response": receipt}]
            if pending:
                transitions.append(
                    {
                        "update_id": pending["update_id"],
                        "status": "resolved",
                        "response": {"superseded_by": receipt["resolved_by"]},
                    }
                )
            self._apply(
                "personal",
                name,
                chosen,
                values=[("baseline:" + name, remote), ("working:" + name, {"package": chosen, "base_revision": base})],
                queued=queued,
                transitions=transitions,
            )

    def _send(self, row):
        payload = row["payload"]
        status, result = self._request("POST", "/v1/skills/personal", payload)
        if status == 429 and result == {"error": "skill_capacity"}:
            self.state.transaction(
                transitions=[{"update_id": row["update_id"], "status": "rejected", "response": result}]
            )
            return
        if status == 409 and isinstance(result, dict) and "error" in result:
            code = result["error"]
            if code in ("incomplete_resolution", "conflict_unavailable") and payload.get("resolves_update_id"):
                if code == "conflict_unavailable":
                    self._reconcile_resolved()
                    if self.state.request(row["update_id"])["status"] != "pending":
                        return
                self.state.transaction(
                    transitions=[{"update_id": row["update_id"], "status": "resolved", "response": {"error": code}}]
                )
                raise CompanionError(
                    "skill_resolution_stale",
                    "The library changed before this resolution; candidates remain available. Resolve again explicitly.",
                    6,
                )
            if code == "skill_version_reused":
                self.state.transaction(
                    transitions=[{"update_id": row["update_id"], "status": "rejected", "response": {"error": code}}]
                )
                return
            raise CompanionError(
                "skill_mutation_rejected", "The skill mutation was rejected; its original outbox entry is retained.", 6
            )
        if not isinstance(result, dict) or set(result) != {"status", "revision", "update_id", "skill_id"}:
            raise CompanionError("skill_response_rejected", "Invalid skill mutation acknowledgement.")
        if result["update_id"] != row["update_id"] or result["skill_id"] != payload["skill_id"]:
            raise CompanionError("skill_response_rejected", "The skill acknowledgement identifies another update.")
        revision(result["revision"])
        if status == 409 and result["status"] == "conflict":
            self.state.transaction(
                transitions=[{"update_id": row["update_id"], "status": "conflict", "response": result}]
            )
            return
        if status != 200 or result["status"] != "applied":
            raise CompanionError("skill_response_rejected", "The skill acknowledgement has an unexpected status.")
        name = payload["skill_id"]
        remote = snapshot(self._get("/v1/skills/personal/history/" + str(result["revision"])), name)
        if remote["sha256"] != package_hash(payload["package"]) or remote["revision"] != result["revision"]:
            raise CompanionError(
                "skill_response_rejected", "The immutable history does not match the acknowledged package."
            )
        working = self._working(name)
        chosen = working["package"]
        next_base = remote["revision"]
        if payload.get("resolves_update_id") and package_hash(chosen) == package_hash(row["expected"]):
            chosen = payload["package"]
        elif (
            payload.get("resolves_update_id")
            and package_hash(chosen) != remote["sha256"]
            and not same_package_except_version(row["expected"], payload["package"])
        ):
            # A new import made while an explicit resolution was outstanding
            # never silently replaces the selected remote choice at a fresh base.
            next_base = self.state.request(payload["resolves_update_id"])["payload"]["base_revision"]
        transitions = [{"update_id": row["update_id"], "status": "done", "response": result}]
        if payload.get("resolves_update_id"):
            transitions.append({"update_id": payload["resolves_update_id"], "status": "resolved", "response": result})
        # A local resolution labels the same observed package with an explicit
        # version. Its later import/delete is a valid descendant. A different
        # selected remote package keeps the old causal base above. Other skills
        # retain their own base revision and are never rebased by this ACK.
        self._apply(
            "personal",
            name,
            chosen,
            values=[("baseline:" + name, remote), ("working:" + name, {"package": chosen, "base_revision": next_base})],
            transitions=transitions,
        )

    def _personal_list(self):
        value = self._get("/v1/skills/personal")
        if (
            not isinstance(value, dict)
            or set(value) != {"revision", "skills"}
            or not isinstance(value["skills"], list)
            or len(value["skills"]) > 1000
        ):
            raise CompanionError("skill_response_rejected", "Invalid personal skill library metadata.")
        revision(value["revision"])
        seen = set()
        for row in value["skills"]:
            metadata(row)
            name = skill_id(row.get("skill_id"))
            if (
                name in seen
                or revision(row.get("revision")) > value["revision"]
                or type(row.get("deleted")) is not bool
            ):
                raise CompanionError("skill_response_rejected", "Invalid personal skill metadata.")
            seen.add(name)
        return value

    def _pull_personal(self):
        listing = self._personal_list()
        for item in listing["skills"]:
            name = item["skill_id"]
            if self._active(name) or package_hash(self._working(name)["package"]) != self._baseline(name)["sha256"]:
                continue
            remote = snapshot(self._get("/v1/skills/personal/" + name), name)
            if remote["revision"] != item["revision"] or remote["sha256"] != item["sha256"]:
                raise CompanionError(
                    "skill_library_changed", "A skill changed during retrieval; retry with local state preserved.", 6
                )
            self._apply(
                "personal",
                name,
                remote["package"],
                values=[
                    ("baseline:" + name, remote),
                    ("working:" + name, {"package": remote["package"], "base_revision": remote["revision"]}),
                ],
            )
        self.state.put("revision", listing["revision"])

    def _pull_company(self):
        listing = self._get("/v1/skills/company")
        if (
            not isinstance(listing, dict)
            or set(listing) != {"skills"}
            or not isinstance(listing["skills"], list)
            or len(listing["skills"]) > 1000
        ):
            raise CompanionError("skill_response_rejected", "Invalid company recipient metadata.")
        seen = set()
        for item in listing["skills"]:
            metadata(item, company=True)
            name = skill_id(item.get("skill_id"))
            if name in seen:
                raise CompanionError("skill_response_rejected", "Duplicate company skill identifier.")
            seen.add(name)
            remote = snapshot(self._get("/v1/skills/company/" + name), name, company=True)
            if remote["package"] is None:
                raise CompanionError("skill_response_rejected", "The company publication contains no package.")
            if any(remote[key] != item.get(key) for key in ("revision", "sha256", "publication_id")):
                raise CompanionError(
                    "skill_library_changed", "The company publication changed during retrieval; retry.", 6
                )
            self._apply("company", name, remote["package"], values=[("company:" + name, remote)])
        for name, prior in self.state.records("company:").items():
            if name not in seen and prior is not None:
                self._apply("company", name, None, values=[("company:" + name, None)])

    def check_runtime(self):
        self.state.recover(self.home)
        for scope in ("personal", "company"):
            for name in self.state.records("installed:" + scope + ":"):
                self.state.inspect_runtime(self.home, scope, name)
        # Cached content still observes locally known dependency changes. Check
        # every managed tree before replacing any, so direct edits remain intact.
        for scope, prefix in (("personal", "working:"), ("company", "company:")):
            for name, saved in self.state.records(prefix).items():
                self._apply(scope, name, saved["package"] if saved else None)

    def activate_deferred(self):
        from .skill_state import runtime_package

        self.state.recover(self.home)
        for name, deferred in self.state.records("deferred:").items():
            if deferred is None:
                continue
            package = self._working(name)["package"]
            if deferred["adopt_current"]:
                current = runtime_package(self.state.runtime_path(self.home, "personal", name), package, "personal")
                if package_hash(current) != package_hash(package):
                    raise CompanionError(
                        "skill_runtime_modified",
                        "Managed files changed again after the deferred import; preserve and import explicitly.",
                        6,
                    )
            self._apply("personal", name, package, accept_drift=deferred["adopt_current"])

    def run(self, *, dry_run=False):
        if dry_run:
            return {"status": "dry_run", **self.status()}
        self.activate_deferred()
        self.check_runtime()
        self._reconcile_resolved()
        self._queue_dirty()
        # Each package can gain one residual from a newer explicit local import.
        # A bounded pass avoids an unbounded service loop under corrupt state.
        sent = 0
        while self.state.outbox("pending"):
            row = self.state.outbox("pending")[0]
            self._send(row)
            sent += 1
            if sent > 2000:
                raise CompanionError("skill_capacity", "The outbox requires another bounded synchronization pass.", 3)
            self._queue_dirty()
        self._pull_personal()
        self._pull_company()
        if self.state.outbox("rejected"):
            if any(row["response"] == {"error": "skill_capacity"} for row in self.state.outbox("rejected")):
                raise CompanionError(
                    "skill_capacity",
                    "The library cannot admit another skill ID. Rejected candidates are retained; inspect skills rejected or delete the rejected local import.",
                    3,
                )
            raise CompanionError(
                "skill_version_reused", "An immutable version was rejected; choose a new explicit package version.", 6
            )
        if self.state.outbox("conflict"):
            raise CompanionError(
                "skill_sync_conflict",
                "Concurrent skill packages are retained. Inspect conflicts and resolve explicitly.",
                6,
            )
        if any(self.state.records("missing:").values()):
            raise CompanionError(
                "skill_requirements_missing",
                "Packages are retained but missing requirements prevent activation; inspect skills status.",
                3,
            )
        return {"status": "synchronized", **self.status()}

    def resolve(self, update_id, choice, *, version=None):
        self.state.recover(self.home)
        original = self.state.request(str(uuid.UUID(update_id)))
        if original["status"] != "conflict":
            raise CompanionError("skill_conflict_missing", "This local update is not an unresolved skill conflict.", 3)
        name = original["payload"]["skill_id"]
        if any(row["payload"]["skill_id"] == name for row in self.state.outbox("pending")):
            raise CompanionError(
                "skill_pending_resolution", "Resume synchronization before making another resolution.", 3
            )
        receipt = self._conflict(update_id)
        if receipt["resolved_by"]:
            self._reconcile_resolved()
            return self.run()
        listing = self._personal_list()
        remote = snapshot(self._get("/v1/skills/personal/" + name), name)
        if remote["revision"] > listing["revision"]:
            raise CompanionError("skill_library_changed", "The library changed while choosing a resolution; retry.", 6)
        working = self._working(name)
        if choice == "local":
            chosen = deepcopy(working["package"])
            if chosen is not None:
                if version is None:
                    raise CompanionError(
                        "skill_version_required", "Local package resolution requires an explicit new version.", 3
                    )
                chosen["version"] = skill_version(version)
        elif choice == "remote":
            chosen = remote["package"]
        else:
            raise CompanionError("skill_choice_rejected", "Choose local or remote for skill conflict resolution.")
        queued = self._new_update(name, chosen, listing["revision"], resolves=update_id, expected=working["package"])
        rejected = [
            {"update_id": row["update_id"], "status": "resolved", "response": row["response"]}
            for row in self.state.outbox("rejected")
            if row["payload"].get("resolves_update_id") == update_id
        ]
        self.state.transaction(queued=[queued], transitions=rejected)
        return self.run()

    def status(self):
        personal = []
        for name, working in self.state.records("working:").items():
            package = working["package"]
            personal.append(
                {
                    "skill_id": name,
                    "version": package["version"] if package else None,
                    "sha256": package_hash(package),
                    "deleted": package is None,
                    "base_revision": working["base_revision"],
                    "missing_requirements": self.state.get("missing:personal:" + name, []),
                }
            )
        company = [
            {
                "skill_id": name,
                "revision": row["revision"],
                "version": row["package"]["version"],
                "publication_id": row["publication_id"],
                "missing_requirements": self.state.get("missing:company:" + name, []),
            }
            for name, row in self.state.records("company:").items()
            if row is not None
        ]
        return {
            "revision": self.state.get("revision", 0),
            "personal": personal,
            "company": company,
            "pending": len(self.state.outbox("pending")),
            "conflicts": len(self.state.outbox("conflict")),
            "rejected": len(self.state.outbox("rejected")),
            "recovery_pending": self.state.get("journal") is not None,
            "activation_pending": sum(row is not None for row in self.state.records("deferred:").values()),
        }
