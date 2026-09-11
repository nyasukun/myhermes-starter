"""Owner-only terminal prompts. Never fall back to stdin or an echoing password read."""

import io
import json
import termios

from .errors import CompanionError
from .github import validate_pat
from .terminal_input import read_terminal_line


def terminal_text(value):
    return "".join(character if character.isprintable() else "\\u%04x" % ord(character) for character in value)


class NativeConnectionTerminal:
    def __init__(self):
        self.stream = None
        try:
            # BufferedRandom requires a seekable device. Wrap unbuffered FileIO
            # directly so a real terminal supports both UTF-8 reads and writes.
            self.stream = open("/dev/tty", "r+b", buffering=0)
            self.stream = io.TextIOWrapper(self.stream, encoding="utf-8", line_buffering=True, write_through=True)
            if not self.stream.isatty():
                raise OSError("Not a terminal")
            termios.tcgetattr(self.stream.fileno())
        except Exception:
            if self.stream is not None:
                self.stream.close()
            raise CompanionError(
                "terminal_required",
                "Run connection authorization in your own native terminal, never in a Codex-captured tool or PTY.",
                3,
            ) from None

    def close(self):
        self.stream.close()

    def describe(self, template, context):
        self.stream.write("MyHermes GitHub authorization\n")
        self.stream.write("Template: " + terminal_text(template["template_id"] + "@" + template["version"]) + "\n")
        self.stream.write(
            "Requested repositories and operations:\n"
            + terminal_text(json.dumps(context, ensure_ascii=False, sort_keys=True))
            + "\n"
        )
        self.stream.write(
            "Create an expiring fine-grained PAT for only these repositories at https://github.com/settings/personal-access-tokens .\n"
        )
        self.stream.write("Requested permissions: " + ", ".join(template["auth"]["required_permissions"]) + "\n")

    def read_token(self):
        try:
            token = read_terminal_line(self.stream, "Fine-grained PAT (hidden): ", hidden=True, limit=512)
        except (OSError, termios.error):
            raise CompanionError(
                "terminal_required",
                "Secure terminal input is unavailable; the token was not read through a fallback.",
                3,
            ) from None
        validate_pat(token)
        return token

    def confirm(self, template, account, context):
        self.stream.write(
            "Authenticated account: "
            + terminal_text(account["login"])
            + " (ID "
            + account["provider_account_id"]
            + ")\n"
        )
        self.stream.write("Business-use notice " + terminal_text(template["usage_notice"]["version"]) + ":\n")
        self.stream.write(terminal_text(template["usage_notice"]["text"]) + "\n")
        self.stream.write(
            "Accepted scope: " + terminal_text(json.dumps(context, ensure_ascii=False, sort_keys=True)) + "\n"
        )
        accepted = read_terminal_line(self.stream, "Register this account for that scope? Type yes: ") == "yes"
        if not accepted:
            raise CompanionError(
                "authorization_cancelled",
                "Account registration was cancelled; no credential or server connection was saved.",
                3,
            )
