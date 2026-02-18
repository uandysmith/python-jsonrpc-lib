"""JSON-RPC error classes per specification.

Error codes:
    -32700: Parse error - Invalid JSON
    -32600: Invalid Request - Not a valid Request object
    -32601: Method not found - Method does not exist
    -32602: Invalid params - Invalid method parameters
    -32603: Internal error - Internal JSON-RPC error
    -32001: Invalid result - Return type mismatch (implementation-defined)
    -32000 to -32099: Server error - Reserved for implementation
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class RPCError:
    """JSON-RPC error object."""

    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-RPC error object dict."""
        result: dict[str, Any] = {
            'code': self.code,
            'message': self.message,
        }
        if self.data is not None:
            result['data'] = self.data
        return result


class JSONRPCError(Exception):
    """Base exception for JSON-RPC errors."""

    code: int = -32603
    message: str = 'Internal error'

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: Any = None,
    ) -> None:
        self._message = message if message is not None else self.message
        self._code = code if code is not None else self.code
        self.data = data
        super().__init__(self._message)

    @property
    def error(self) -> RPCError:
        """Get RPCError object for this exception."""
        return RPCError(
            code=self._code,
            message=self._message,
            data=self.data,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-RPC error object dict."""
        return self.error.to_dict()


class ParseError(JSONRPCError):
    """Invalid JSON was received by the server."""

    code = -32700
    message = 'Parse error'


class InvalidRequestError(JSONRPCError):
    """The JSON sent is not a valid Request object."""

    code = -32600
    message = 'Invalid Request'


class MethodNotFoundError(JSONRPCError):
    """The method does not exist / is not available."""

    code = -32601
    message = 'Method not found'


class InvalidParamsError(JSONRPCError):
    """Invalid method parameter(s)."""

    code = -32602
    message = 'Invalid params'


class InvalidResultError(JSONRPCError):
    """Method result doesn't match declared result_type.

    This is an implementation-defined server error that indicates a contract
    violation between the method implementation and its declared return type.
    Uses code -32001 (in the -32000 to -32099 range reserved for implementation).
    """

    code = -32001
    message = 'Invalid result'


class InternalError(JSONRPCError):
    """Internal JSON-RPC error."""

    code = -32603
    message = 'Internal error'


class ServerError(JSONRPCError):
    """Reserved for implementation-defined server-errors.

    Code must be in range -32000 to -32099.
    """

    code = -32000
    message = 'Server error'

    def __init__(
        self,
        message: str | None = None,
        code: int | None = None,
        data: Any = None,
    ) -> None:
        if code is not None and not (-32099 <= code <= -32000):
            raise ValueError(f'Server error code must be in range -32099 to -32000, got {code}')
        super().__init__(message, code, data)
