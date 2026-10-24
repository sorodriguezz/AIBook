"""Errors the user can act on.

Each carries a stable `code` the app translates into a friendly message; the
English `message` is only a fallback and goes to the logs.
"""


class UserError(Exception):
    status = 400

    def __init__(self, code: str, message: str, status: int | None = None, **params) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params
        if status is not None:
            self.status = status

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "params": self.params}
