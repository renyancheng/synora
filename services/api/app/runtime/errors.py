from __future__ import annotations


class LLMServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        debug_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.debug_message = debug_message or message

    def to_payload(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
