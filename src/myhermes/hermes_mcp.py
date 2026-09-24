"""Claude Code/Codex -> managed MyHermes over local stdio MCP."""

import argparse
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import sys

from .errors import CompanionError
from .files import safe_path
from .mcp_process import run_process

MAX_PROMPT_BYTES = 32_768


def validate_prompt(prompt):
    try:
        valid = isinstance(prompt, str) and prompt.strip() and "\x00" not in prompt
        valid = valid and len(prompt.encode("utf-8")) <= MAX_PROMPT_BYTES
    except UnicodeError:
        valid = False
    if not valid:
        raise CompanionError("mcp_prompt_invalid", "Task must be nonempty UTF-8 text up to 32768 bytes.")


def worker_environment():
    # CLI client credentials, provider overrides and instrumentation are not
    # inherited. MyHermes obtains its own environment identity from native storage.
    allowed = {
        "HOME",
        "PATH",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "USER",
        "LOGNAME",
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_RUNTIME_DIR",
        "DISPLAY",
    }
    return {k: v for k, v in os.environ.items() if k in allowed}


@dataclass(frozen=True)
class HermesClient:
    state_dir: Path
    run_budget: int = 300
    max_turns: int = 12

    def __post_init__(self):
        if not Path(self.state_dir).is_absolute():
            raise CompanionError("mcp_state_invalid", "Select an absolute MyHermes state directory.")
        object.__setattr__(self, "state_dir", safe_path(self.state_dir))
        if type(self.run_budget) is not int or not 10 <= self.run_budget <= 1800:
            raise CompanionError("mcp_config_invalid", "Run budget must be between 10 and 1800 seconds.")
        if type(self.max_turns) is not int or not 1 <= self.max_turns <= 100:
            raise CompanionError("mcp_config_invalid", "Maximum turns must be between 1 and 100.")

    def command(self, action):
        return [
            sys.executable,
            "-m",
            "myhermes.hermes_mcp_worker",
            "--state-dir",
            str(self.state_dir),
            "--run-budget",
            str(self.run_budget),
            "--max-turns",
            str(self.max_turns),
            action,
        ]

    async def request(self, action, prompt=None):
        if action == "ask":
            validate_prompt(prompt)
        elif action != "status":
            raise CompanionError("mcp_action_invalid", "Unsupported MyHermes action.")
        raw = await run_process(
            self.command(action),
            self.state_dir.parent,
            worker_environment(),
            prompt.encode() if prompt else b"",
            self.run_budget + 120,
            termination_grace=45,
        )
        try:
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("status") not in ("ready", "completed", "incomplete"):
                raise ValueError
            if action == "ask" and not isinstance(result.get("answer"), str):
                raise ValueError
        except (ValueError, TypeError):
            raise CompanionError("mcp_result_invalid", "MyHermes worker returned an invalid result.", 3) from None
        return result


def create_server(client):
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.exceptions import ToolError
        from mcp.types import ToolAnnotations
    except ImportError:
        raise CompanionError("mcp_sdk_missing", "Install myhermes-companion with the [mcp] extra.", 3) from None
    server = FastMCP(
        "myhermes",
        log_level="ERROR",
        instructions=(
            "Ask the owner's Hermes assistant through its existing MyHermes installation. "
            "Hermes shares its existing persona and memory and uses its own configured accounts and model relay. "
            "Each call starts a new managed session, synchronizes before and after, and may use tools or update memory. "
            "Send only the context needed for the task, never client credentials or the entire conversation. "
            "The state directory and limits are fixed by the owner. Do not recursively call this server from Hermes."
        ),
    )

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def hermes_status() -> dict:
        """Check local MyHermes setup metadata without reading memory or making a model request."""
        try:
            return await client.request("status")
        except CompanionError as error:
            raise ToolError(error.code + ": " + error.message) from None

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
    async def hermes_ask(prompt: str) -> dict:
        """Ask Hermes to perform the owner's task using its configured tools, memory and accounts."""
        try:
            return await client.request("ask", prompt)
        except CompanionError as error:
            raise ToolError(error.code + ": " + error.message) from None

    return server


async def serve(client):
    server = create_server(client)
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await server.run_stdio_async()
    finally:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            loop.remove_signal_handler(sig)


def client_config(client, target):
    args = [
        "-m",
        "myhermes.hermes_mcp",
        "serve",
        "--state-dir",
        str(client.state_dir),
        "--run-budget",
        str(client.run_budget),
        "--max-turns",
        str(client.max_turns),
    ]
    if target == "claude-code":
        return (
            json.dumps(
                {"mcpServers": {"myhermes": {"type": "stdio", "command": sys.executable, "args": args}}},
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
    if target == "codex":
        return (
            "[mcp_servers.myhermes]\ncommand = "
            + json.dumps(sys.executable)
            + "\nargs = "
            + json.dumps(args)
            + "\nstartup_timeout_sec = 30\ntool_timeout_sec = "
            + str(client.run_budget + 180)
            + "\n"
        )
    raise CompanionError("mcp_config_invalid", "Unknown MCP client.")


def write_config(path, content):
    path = safe_path(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise CompanionError("mcp_config_exists", "Output exists; choose a new fragment path.", 3) from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def parser():
    root = argparse.ArgumentParser(prog="myhermes-mcp")
    sub = root.add_subparsers(dest="command", required=True)
    for name in ("serve", "config"):
        p = sub.add_parser(name)
        p.add_argument("--state-dir", type=Path, required=True)
        p.add_argument("--run-budget", type=int, default=300)
        p.add_argument("--max-turns", type=int, default=12)
        if name == "config":
            p.add_argument("--client", choices=("claude-code", "codex"), required=True)
            p.add_argument("--output", type=Path, required=True)
    return root


def main():
    args = parser().parse_args()
    try:
        client = HermesClient(args.state_dir, args.run_budget, args.max_turns)
        if args.command == "config":
            write_config(args.output, client_config(client, args.client))
            print(json.dumps({"status": "created", "client": args.client}))
            return
        asyncio.run(serve(client))
    except CompanionError as error:
        print(json.dumps({"error": error.code}), file=sys.stderr)
        raise SystemExit(error.exit_code) from None
    except (OSError, ValueError):
        print('{"error":"mcp_local_io_failed"}', file=sys.stderr)
        raise SystemExit(3) from None
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
