"""Owner-only terminal prompts. Never fall back to stdin or an echoing password read."""

import json
import termios

from .errors import CompanionError
from .github import validate_pat


def terminal_text(value):
    return "".join(character if character.isprintable() else "\\u%04x" % ord(character) for character in value)


class NativeConnectionTerminal:
    def __init__(self):
        self.stream = None
        try:
            self.stream = open("/dev/tty", "r+", encoding="utf-8", buffering=1)
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
        fd = self.stream.fileno()
        previous = termios.tcgetattr(fd)
        attributes = list(previous)
        attributes[3] &= ~(termios.ECHO | termios.ECHONL)
        try:
            termios.tcsetattr(fd, termios.TCSAFLUSH, attributes)
            self.stream.write("Fine-grained PAT (hidden): ")
            self.stream.flush()
            token = self.stream.readline(514)
        except (OSError, termios.error):
            raise CompanionError(
                "terminal_required",
                "Secure terminal input is unavailable; the token was not read through a fallback.",
                3,
            ) from None
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, previous)
            self.stream.write("\n")
        token = token.rstrip("\r\n")
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
        self.stream.write("Register this account for that scope? Type yes: ")
        self.stream.flush()
        accepted = self.stream.readline(32).strip() == "yes"
        if not accepted:
            raise CompanionError(
                "authorization_cancelled",
                "Account registration was cancelled; no credential or server connection was saved.",
                3,
            )
