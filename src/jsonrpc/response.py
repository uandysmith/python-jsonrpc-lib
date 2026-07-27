"""Response building and parsing for JSON-RPC protocol."""

import json
import weakref
from dataclasses import fields, is_dataclass
from typing import Any

from .errors import (
    InvalidRequestError,
    JSONRPCError,
    ParseError,
    RPCError,
    clip,
)
from .types import ErrorResponse, Response, Version

# Leaf types that need no conversion and no copy. Checked by exact type, which
# is a single hash lookup - the overwhelming majority of values in a result are
# these, and every other branch below costs more to evaluate.
_ATOMIC_TYPES = frozenset({str, int, float, bool, type(None)})

# Field names per dataclass, so a result of N rows does not rebuild the same
# tuple N times. Weak keys: a type generated at runtime stays collectable.
_field_names_cache: 'weakref.WeakKeyDictionary[type, tuple[str, ...]]' = weakref.WeakKeyDictionary()


def _dataclass_field_names(cls: type) -> tuple[str, ...]:
    names = _field_names_cache.get(cls)
    if names is None:
        names = tuple(f.name for f in fields(cls))
        _field_names_cache[cls] = names
    return names


def _dataclass_to_dict(value: Any) -> Any:
    """Convert dataclass instances to dicts recursively for JSON serialization.

    This walks the structure itself rather than calling dataclasses.asdict().
    asdict() deep-copies anything that is not a known-atomic leaf, and the copy
    is pure waste here: the result goes straight into json.dumps() and is then
    discarded. On a result of a few thousand rows that dominated the response.

    Tuples become lists, matching asdict() and what JSON can express.

    Args:
        value: Any value (dataclass, list, dict, or primitive)

    Returns:
        Value with all dataclasses converted to dicts
    """
    if type(value) in _ATOMIC_TYPES:
        return value

    if is_dataclass(value) and not isinstance(value, type):
        return {name: _dataclass_to_dict(getattr(value, name)) for name in _dataclass_field_names(type(value))}

    if isinstance(value, (list, tuple)):
        return [_dataclass_to_dict(item) for item in value]

    if isinstance(value, dict):
        return {key: _dataclass_to_dict(val) for key, val in value.items()}

    return value


def build_response(
    result: Any,
    id: str | int | None,
    version: Version = '2.0',
) -> dict[str, Any]:
    """Build a JSON-RPC success response dict.

    Args:
        result: Method result (can be dataclass, which will be converted to dict)
        id: Request ID
        version: Protocol version

    Returns:
        Response dict ready for JSON serialization
    """
    if version == '2.0':
        return {
            'jsonrpc': '2.0',
            'result': result,
            'id': id,
        }
    else:
        # v1: always has both result and error fields
        return {
            'result': result,
            'error': None,
            'id': id,
        }


def build_error_response(
    error: JSONRPCError | RPCError,
    id: str | int | None,
    version: Version = '2.0',
) -> dict[str, Any]:
    """Build a JSON-RPC error response dict.

    Args:
        error: Error object or exception
        id: Request ID (can be None for parse errors)
        version: Protocol version

    Returns:
        Error response dict ready for JSON serialization
    """
    error_dict = error.to_dict()

    if version == '2.0':
        return {
            'jsonrpc': '2.0',
            'error': error_dict,
            'id': id,
        }
    else:
        # v1: always has both result and error fields
        return {
            'result': None,
            'error': error_dict,
            'id': id,
        }


def parse_response(
    data: str | bytes | dict[str, Any],
) -> Response | ErrorResponse:
    """Parse JSON-RPC response (for client-side usage).

    Args:
        data: JSON string, bytes, or already parsed dict

    Returns:
        Response or ErrorResponse object

    Raises:
        ParseError: If JSON is invalid
        InvalidRequestError: If response structure is invalid
    """
    if isinstance(data, (str, bytes)):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as e:
            raise ParseError(f'Invalid JSON: {e}') from e
    else:
        parsed = data

    if not isinstance(parsed, dict):
        raise InvalidRequestError(f'Response must be object, got {type(parsed).__name__}')

    if 'jsonrpc' in parsed:
        if parsed['jsonrpc'] != '2.0':
            raise InvalidRequestError(f'Invalid jsonrpc version: {clip(repr(parsed["jsonrpc"]))}')
        version: Version = '2.0'
    else:
        version = '1.0'

    response_id = parsed.get('id')

    if 'error' in parsed and parsed['error'] is not None:
        error_data = parsed['error']
        if not isinstance(error_data, dict):
            raise InvalidRequestError(f"Field 'error' must be object, got {type(error_data).__name__}")

        code = error_data.get('code')
        if not isinstance(code, int):
            raise InvalidRequestError(f"Error 'code' must be integer, got {type(code).__name__}")

        message = error_data.get('message', '')
        if not isinstance(message, str):
            raise InvalidRequestError(f"Error 'message' must be string, got {type(message).__name__}")

        error = RPCError(
            code=code,
            message=message,
            data=error_data.get('data'),
        )

        return ErrorResponse(
            error=error,
            id=response_id,
            version=version,
        )

    if 'result' not in parsed:
        raise InvalidRequestError("Response must have 'result' or 'error' field")

    # Absent id and null id are different things: the server answers a request
    # carrying an explicit "id": null with a null id, and rejecting that here
    # would mean the client half cannot read the server half's own output.
    if 'id' not in parsed:
        raise InvalidRequestError("Success response must have 'id' field")

    return Response(
        result=parsed['result'],
        id=response_id,
        version=version,
    )
