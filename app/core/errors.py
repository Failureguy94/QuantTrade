"""Domain exceptions shared by the service layer.

Services raise these instead of ``HTTPException`` so they stay usable outside a
request (CLI, workers, tests). ``app.api.errors`` maps them to HTTP responses.
"""

from typing import Any


class DomainError(Exception):
    """Base class for expected, client-correctable failures.

    ``code`` is a stable machine-readable identifier returned next to the message,
    so clients can branch on it without parsing text.
    """

    status_code: int = 400
    code: str = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}


class UnauthorizedError(DomainError):
    """Caller is not authenticated, or the credential presented is not valid."""

    status_code = 401
    code = "unauthorized"


class ForbiddenError(DomainError):
    """Caller is authenticated but may not do this."""

    status_code = 403
    code = "forbidden"


class NotFoundError(DomainError):
    """Resource does not exist or is not owned by the caller."""

    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    """Request conflicts with the current state of a resource."""

    status_code = 409
    code = "conflict"


class UnprocessableError(DomainError):
    """Request is well-formed but cannot be carried out."""

    status_code = 422
    code = "unprocessable"
