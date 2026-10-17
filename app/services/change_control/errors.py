from __future__ import annotations


class ChangeRefused(Exception):
    def __init__(self, code: str, message: str = "", *, http: int = 422, errors: list | None = None):
        self.code = code
        self.message = message or code
        self.http = http
        self.errors = list(errors or [])
        super().__init__(f"{code}: {message}" if message else code)


class StaleBase(ChangeRefused):
    def __init__(self, message: str = "base version changed"):
        super().__init__("stale_base", message, http=409)


class ChangeFailed(ChangeRefused):

    def __init__(self, code: str, message: str = "", *, errors: list | None = None):
        super().__init__(code, message, http=409, errors=errors)
