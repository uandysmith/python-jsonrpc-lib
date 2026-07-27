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

        The two versions spell it differently, and each spells it exactly one
        way:

        - 2.0: the `id` member is **absent**. An explicit `"id": null` is a
          request, and it is answered with a null id.
        - 1.0: the `id` member is present and **null**. 1.0 has no way to omit
          it, so null is the marker.

        This used to report False for every 1.0 request, while
        `build_notification(version='1.0')` in the same package produced exactly
        the `"id": null` shape - the client half built notifications that the
        server half answered.
        """
        if self.version == '2.0':
            return not self.id_was_present
        return self.id_was_present and self.id is None


@dataclass
class Response:
    """JSON-RPC success response object.

    `id` may be None: a v2.0 request that carries an explicit `"id": null` is a
    request, not a notification, and the answer to it repeats that null id.
    """

    result: Any
    id: str | int | None
    version: Version


@dataclass
class ErrorResponse:
    """JSON-RPC error response object."""

    error: RPCError
    id: str | int | None
    version: Version
