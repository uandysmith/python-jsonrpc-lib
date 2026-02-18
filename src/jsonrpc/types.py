"""Core type definitions for JSON-RPC protocol."""

from dataclasses import dataclass
from typing import Any, Literal

from .errors import RPCError

Version = Literal['1.0', '2.0']


@dataclass
class Request:
    """JSON-RPC request object."""

    method: str
    params: list[Any] | dict[str, Any] | None
    id: str | int | None
    version: Version
    id_was_present: bool = True

    @property
    def is_notification(self) -> bool:
        """Check if this request is a notification (no response expected).

        JSON-RPC 2.0: notification when id field is missing (not just null).
        JSON-RPC 1.0: NO notifications - all requests get responses.

        Note: v2.0 distinguishes:
            - Missing id field → notification (no response)
            - id=null → normal request (returns response with id=null)
        """
        if self.version == '2.0':
            return not self.id_was_present
        else:
            return False


@dataclass
class Response:
    """JSON-RPC success response object."""

    result: Any
    id: str | int
    version: Version


@dataclass
class ErrorResponse:
    """JSON-RPC error response object."""

    error: RPCError
    id: str | int | None
    version: Version
