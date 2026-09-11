"""Stable exit codes. Error messages never include server bodies or secrets."""


class CompanionError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 2):
        super().__init__(message)
        self.code, self.message, self.exit_code = code, message, exit_code


class OfflineError(CompanionError):
    def __init__(self):
        super().__init__("offline", "Connection unavailable; queued changes remain on this device.", 4)
