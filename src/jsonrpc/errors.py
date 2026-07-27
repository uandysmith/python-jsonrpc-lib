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

# Longest run of caller-supplied text any error message will quote back.
#
# Some messages have to name what the caller sent - which parameter was unknown,
# which method was not found - or the message helps nobody. That makes the
# response size a function of the request: a 900 KB parameter name produced a
# 1.8 MB response, doubled because 0.4.0 also puts the name in error.data. The
# body limit bounds it, but a 2x amplifier on every rejected request is not
# something to leave for the body limit alone to hold.
#
# 128 is far past any real method or field name and short enough that the quote
# can no longer dominate the answer.
MAX_ECHOED_LENGTH = 128


def clip(text: str, limit: int = MAX_ECHOED_LENGTH) -> str:
    """Shorten caller-supplied text before it goes into an error message.

    The ellipsis is spelled out rather than a single character so the result
    survives any encoding, and so a reader can tell truncation from a name that
    genuinely ends in dots.
    """
    if len(text) <= limit:
        return text
    return f'{text[:limit]}... ({len(text)} characters)'


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
        # Assign the public names, shadowing the class-level defaults. Keeping
        # the real values in _code/_message only meant that the obvious thing to
        # write in a handler - `log.error('%d %s', e.code, e.message)` - reported
        # the class default rather than this error: ServerError('boom',
        # code=-32050) read back as -32000 'Server error'.
        self.message = message if message is not None else self.message
        self.code = code if code is not None else self.code
        self._message = self.message
        self._code = self.code
        self.data = data
        super().__init__(self.message)

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


class _DispatchWiringError(RuntimeError):
    """The server is wired so that this call cannot be served.

    Raised when dispatch is asked to do something the tree cannot do: run an
    async method through the synchronous entry point, or run a chain whose
    middleware only exists for the other direction. It stays a RuntimeError,
    because that is the documented contract of dispatch() and it is a
    programming error rather than a protocol one.

    The distinction from an ordinary exception is that this message is written
    for whoever wired the server and says only what they already know - the
    method path and which entry point to use - so it is exempt from the
    sanitization that keeps arbitrary exception text off the wire.
    """


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
