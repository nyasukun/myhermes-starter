"""Bounded stdio subprocesses with cancellation and process group cleanup."""

import asyncio
import os
import signal

from .errors import CompanionError

MAX_OUTPUT_BYTES = 1_048_576


async def run_process(command, workspace, environment, stdin, timeout, *, termination_grace=0.1):
    # Loaded only for the optional MCP worker integration. anyio shields cleanup
    # against the MCP SDK's level-triggered cancellation scopes.
    import anyio

    process = None
    tasks = []
    try:
        # Cancellation must not strand a child between spawn and assignment.
        with anyio.CancelScope(shield=True):
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workspace,
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )

        async def feed():
            try:
                process.stdin.write(stdin)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        async def read():
            data = bytearray()
            while chunk := await process.stdout.read(65_536):
                data.extend(chunk)
                if len(data) > MAX_OUTPUT_BYTES:
                    raise CompanionError("mcp_output_limit", "MCP worker output exceeded 1 MiB; task stopped.", 3)
            return bytes(data)

        tasks = [asyncio.create_task(feed()), asyncio.create_task(read()), asyncio.create_task(process.wait())]
        async with asyncio.timeout(timeout):
            _, output, code = await asyncio.gather(*tasks)
        if code != 0:
            raise CompanionError(
                "mcp_cli_failed", "MCP worker CLI failed. Check login and project access in your own terminal.", 3
            )
        return output
    except TimeoutError:
        raise CompanionError("mcp_timeout", "MCP worker request timed out; inspect edits before retrying.", 3) from None
    except OSError:
        raise CompanionError("mcp_process_failed", "MCP worker subprocess could not be started.", 3) from None
    finally:
        with anyio.CancelScope(shield=True):
            if process:
                # Clean descendants even when the CLI leader exited first.
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, sig)
                    except ProcessLookupError:
                        break
                    if sig == signal.SIGTERM:
                        try:
                            await asyncio.wait_for(process.wait(), termination_grace)
                        except TimeoutError:
                            pass
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await process.wait()
