"""One MCP request on the companion main thread, using the managed start lifecycle."""

import argparse
import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import signal
import sys
import time

from .cli import execute, parser as companion_parser
from .errors import CompanionError
from .hermes_mcp import MAX_PROMPT_BYTES, validate_prompt
from .local_config import bound_config_read
from .mcp_process import run_process
from .runtime import UPSTREAM_VERSION, relay_runtime_session


async def prompt_process(command, environment, cwd, prompt, budget, max_turns):
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, task.cancel)
    try:
        return await run_process(
            [
                str(command),
                "chat",
                "--query-file",
                "-",
                "--oneshot",
                "-Q",
                "--max-turns",
                str(max_turns),
                "--run-budget",
                str(budget),
            ],
            cwd,
            environment,
            prompt.encode("utf-8"),
            budget + 15,
        )
    finally:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            loop.remove_signal_handler(sig)


def ask(state_dir, prompt, *, budget, max_turns):
    validate_prompt(prompt)
    answer = None

    def launch(config, *, api=None, state_directory=None, on_activity=None):
        nonlocal answer
        with relay_runtime_session(config, api=api, state_directory=state_directory, on_activity=on_activity) as (
            command,
            environment,
        ):
            started, outcome = time.monotonic(), "failed"
            if on_activity:
                on_activity("runtime_start", {"outcome": "ok"})
            try:
                raw = asyncio.run(
                    prompt_process(command, environment, Path(config["hermes_home"]), prompt, budget, max_turns)
                )
                answer = raw.decode("utf-8").strip()
                if not answer:
                    raise CompanionError("mcp_answer_empty", "Hermes returned no final answer.", 3)
                # Exact relay bearer reflection is never returned to the caller.
                bearer = environment.get("AUXILIARY_MYHERMES_API_KEY")
                if bearer:
                    answer = answer.replace(bearer, "[REDACTED]")
                outcome = "ok"
                return {"status": "stopped", "runtime_exit_code": 0}
            except (KeyboardInterrupt, asyncio.CancelledError):
                outcome = "cancelled"
                raise KeyboardInterrupt from None
            finally:
                if on_activity:
                    on_activity(
                        "runtime_stop",
                        {"outcome": outcome, "duration_ms": min(86_400_000, int((time.monotonic() - started) * 1000))},
                    )

    # Reuse identity admission, companion/home/runtime locks, recovery, pre-sync,
    # skill bootstrap, post-session checkpoint/ACK and metadata-only monitoring.
    # The answer stays in this closure, outside the CLI's monitored result.
    args = companion_parser().parse_args(["--state-dir", str(state_dir), "start"])
    result = execute(args, runtime_launcher=launch)
    if result.get("runtime_interrupted") or result.get("runtime_error") or answer is None:
        raise CompanionError(
            "mcp_hermes_failed", "Hermes did not complete; inspect MyHermes status before retrying.", 3
        )
    states = {
        key: result.get(key, {}).get("status", "unknown") for key in ("persona_after_session", "skills_after_session")
    }
    persona = result.get("persona_after_session", {})
    states["application_ack"] = persona.get("acknowledgement", {}).get("status", "unknown")
    pending = bool(result.get("post_session_exit_code")) or any(
        state in ("failed", "interrupted", "deferred", "conflict", "conflicts", "blocked", "rejected", "unknown")
        for state in states.values()
    )
    pending = pending or bool(persona.get("checkpoint_pending"))
    return {"status": "incomplete" if pending else "completed", "answer": answer, "synchronization": states}


def status(state_dir):
    config = bound_config_read(state_dir)
    return {
        "status": "ready",
        "enrolled": bool(config.get("installation_id")),
        "os": config["os"],
        "hermes_version": UPSTREAM_VERSION,
        "live_authentication": "not_checked",
        "terminal_backend": "docker",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--run-budget", type=int, default=300)
    p.add_argument("--max-turns", type=int, default=12)
    p.add_argument("action", choices=("ask", "status"))
    args = p.parse_args()
    try:
        # Only the final structured response uses stdout. Incidental library
        # output never becomes an MCP response or a persisted debug log.
        with open(os.devnull, "w") as discard, redirect_stdout(discard):
            if args.action == "status":
                result = status(args.state_dir)
            else:
                raw = sys.stdin.buffer.read(MAX_PROMPT_BYTES + 1)
                if len(raw) > MAX_PROMPT_BYTES:
                    raise CompanionError("mcp_prompt_invalid", "Prompt is too large.")
                result = ask(args.state_dir, raw.decode("utf-8"), budget=args.run_budget, max_turns=args.max_turns)
        print(json.dumps(result, ensure_ascii=False))
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise SystemExit(130) from None
    except Exception:
        # Includes errors whose details could contain private config or content.
        print('{"error":"mcp_worker_failed"}', file=sys.stderr)
        raise SystemExit(3) from None


if __name__ == "__main__":
    main()
