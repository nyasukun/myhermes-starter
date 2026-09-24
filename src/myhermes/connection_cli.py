"""Company connection guidance and native GitHub operations; no credential arguments."""

from pathlib import Path


def register_connection_commands(sub):
    templates = sub.add_parser("templates", help="Inspect authenticated versioned service templates")
    template_commands = templates.add_subparsers(dest="template_command", required=True)
    listing = template_commands.add_parser("list")
    listing.add_argument("--after")
    show = template_commands.add_parser("show")
    show.add_argument("template_id")
    show.add_argument("--version", required=True)
    connections = sub.add_parser("connections", help="Company setup guides, environment status and GitHub connections")
    commands = connections.add_subparsers(dest="connection_command", required=True)
    commands.add_parser("requirements", help="Fetch current company-prescribed services and setup guides")
    status = commands.add_parser("status", help="Inspect this installation's reported MCP and GitHub connection states")
    status.add_argument("--after")
    guide = commands.add_parser("guide", help="Read a company connection guide and this environment's status")
    guide.add_argument("integration_id")
    listing = commands.add_parser("list")
    listing.add_argument("--after")
    show = commands.add_parser("show")
    show.add_argument("connection_id")
    add = commands.add_parser("add", help="Run in the owner's native terminal; never through a captured PTY")
    add.add_argument("--template-id", required=True)
    add.add_argument("--template-version", required=True)
    add.add_argument("--account-kind", required=True, choices=("company", "client", "personal"))
    add.add_argument("--display-name", required=True)
    add.add_argument("--management-kind", required=True, choices=("company", "client", "individual"))
    add.add_argument("--management-label", required=True)
    add.add_argument("--project-id")
    add.add_argument("--project-label")
    add.add_argument(
        "--resource",
        action="append",
        required=True,
        help="Exact GitHub owner/repository; repeat for multiple resources",
    )
    add.add_argument("--operation", action="append", choices=("repository.read", "issues.read"))
    add.add_argument("--dry-run", action="store_true")
    resume = commands.add_parser("resume")
    resume.add_argument("request_id")
    resume.add_argument("--dry-run", action="store_true")
    cancel = commands.add_parser("cancel", help="Cancel a pending local authorization and remove its unused credential")
    cancel.add_argument("request_id")
    cancel.add_argument("--dry-run", action="store_true")
    authorize = commands.add_parser(
        "authorize", help="Authorize the same logical account on this independent environment"
    )
    authorize.add_argument("connection_id")
    authorize.add_argument("--dry-run", action="store_true")
    test = commands.add_parser("test")
    test.add_argument("connection_id")
    test.add_argument("--dry-run", action="store_true")
    read = commands.add_parser("read")
    read.add_argument("connection_id")
    read.add_argument("--operation", required=True, choices=("repository.read", "issues.read"))
    read.add_argument("--resource", required=True)
    read.add_argument("--issue", type=int)
    read.add_argument("--page", type=int, default=1)
    read.add_argument("--per-page", type=int, default=20)
    read.add_argument("--issue-state", choices=("open", "closed", "all"), default="open")
    read.add_argument(
        "--include-content",
        action="store_true",
        help="Explicit owner-requested content output for a Hermes tool response",
    )
    read.add_argument(
        "--export-dir", type=Path, help="Write private content to a new local JSON artifact instead of stdout"
    )
    read.add_argument("--dry-run", action="store_true")
    forget = commands.add_parser("forget", help="Remove this environment's local credential and mark needs_auth")
    forget.add_argument("connection_id")
    forget.add_argument("--dry-run", action="store_true")
    commands.add_parser("pending", help="Inspect resumable local onboarding metadata only")


def execute_connection_command(args, config, directory, *, owner_api):
    if args.command == "connections" and args.connection_command in ("requirements", "status", "guide"):
        from .connection_directory import ConnectionDirectory
        from .connection_schema import identifier
        from .errors import CompanionError

        client = ConnectionDirectory(owner_api(config))
        if args.connection_command == "requirements":
            return client.catalog()
        value = client.status(getattr(args, "after", None))
        if args.connection_command == "guide":
            identifier(args.integration_id)
            value["integrations"] = [
                row for row in value["integrations"] if row["integration_id"] == args.integration_id
            ]
            if not value["integrations"]:
                raise CompanionError("integration_not_found", "That company connection is not enabled.", 3)
        return value
    from .connections import connection_command

    return connection_command(args, config, directory, owner_api=owner_api)
