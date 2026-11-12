"""Domain exceptions shared by the service layer.

Services raise these instead of ``HTTPException`` so they stay usable outside a
request (CLI, workers, tests). ``app.main`` maps them to HTTP responses.
"""

from typing import Any


class DomainError(Exception):
    """Base class for expected, client-correctable failures."""

    status_code: int = 400

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    """Resource does not exist or is not owned by the caller."""

    status_code = 404


class ConflictError(DomainError):
    """Request conflicts with the current state of a resource."""

    status_code = 409


class UnprocessableError(DomainError):
    """Request is well-formed but cannot be carried out."""

    status_code = 422
