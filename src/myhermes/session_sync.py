"""Managed session completion preserves child outcome and both synchronization results."""

from .errors import CompanionError
from .telemetry_cli import outcome


def finish_managed_session(config, directory, sync, api, *, offline, launch, skills, on_activity=None):
    try:
        result = launch(config, api=api, state_directory=directory, on_activity=on_activity)
    except KeyboardInterrupt:
        result = {"status": "interrupted", "runtime_exit_code": 130, "runtime_interrupted": True}
    except CompanionError as error:
        result = {"status": "runtime_failed", "runtime_error": {"error": error.code, "exit_code": error.exit_code}}
    except Exception:
        result = {"status": "runtime_failed", "runtime_error": {"error": "local_state_error", "exit_code": 2}}

    result["post_session_exit_code"] = 0
    for name, kind, operation in (
        ("persona_after_session", "sync", lambda: sync.flush_session(offline=offline)),
        ("skills_after_session", "skill_change", lambda: skills(config, directory, api=api, offline=offline)),
    ):
        try:
            completed = operation()
        except CompanionError as error:
            completed = {"status": "failed", "error": error.code, "exit_code": error.exit_code}
        except KeyboardInterrupt:
            completed = {"status": "interrupted", "error": "interrupted", "exit_code": 130}
        except Exception:
            completed = {"status": "failed", "error": "local_state_error", "exit_code": 2}
        result[name] = completed
        if result["post_session_exit_code"] == 0:
            result["post_session_exit_code"] = completed.get("exit_code", 0)
        if on_activity is not None:
            try:
                on_activity(kind, {"outcome": outcome(completed)})
            except Exception:
                pass
    return result
