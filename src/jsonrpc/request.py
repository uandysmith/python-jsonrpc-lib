"""Request parsing and building for JSON-RPC protocol."""

import json
from typing import Any

from .errors import InvalidParamsError, InvalidRequestError, ParseError
from .types import Request, Version


def parse_request(
    data: str | bytes | dict[str, Any],
    allow_dict_params: bool = True,
    allow_list_params: bool = True,
) -> Request | list[Request]:
    """Parse JSON-RPC request from raw data.

    Args:
        data: JSON string, bytes, or already parsed dict
        allow_dict_params: Whether to accept object params (default: True)
        allow_list_params: Whether to accept array params (default: True)

    Returns:
        Single Request or list of Requests (for batch)

    Raises:
        ParseError: If JSON is invalid
        InvalidRequestError: If request structure is invalid
        InvalidParamsError: If params format violates flags
    """
    if isinstance(data, (str, bytes)):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as e:
            raise ParseError(f'Invalid JSON: {e}') from e
    else:
        parsed = data

    if isinstance(parsed, list):
        if not parsed:
            raise InvalidRequestError('Empty batch request')
        return [_parse_single_request(item, allow_dict_params, allow_list_params) for item in parsed]
    elif isinstance(parsed, dict):
        return _parse_single_request(parsed, allow_dict_params, allow_list_params)
    else:
        raise InvalidRequestError(f'Request must be object or array, got {type(parsed).__name__}')


def _parse_single_request(
    data: dict[str, Any],
    allow_dict_params: bool = True,
    allow_list_params: bool = True,
) -> Request:
    """Parse a single JSON-RPC request object."""
    if not isinstance(data, dict):
        raise InvalidRequestError(f'Request must be object, got {type(data).__name__}')

    if 'jsonrpc' in data:
        if data['jsonrpc'] != '2.0':
            raise InvalidRequestError(f"Invalid jsonrpc version: {data['jsonrpc']!r}, expected '2.0'")
        version: Version = '2.0'
    else:
        version = '1.0'

    if 'method' not in data:
        raise InvalidRequestError("Missing required field: 'method'")

    method = data['method']
    if not isinstance(method, str):
        raise InvalidRequestError(f"Field 'method' must be string, got {type(method).__name__}")

    params = data.get('params')
    if params is not None:
        if isinstance(params, dict):
            if not allow_dict_params:
                raise InvalidParamsError('Object params not allowed. Use array params: ["value1", "value2"]')
        elif isinstance(params, list):
            if not allow_list_params:
                raise InvalidParamsError(
                    'Array params not allowed. Use object params: {"param1": "value1", "param2": "value2"}'
                )
        else:
            raise InvalidRequestError(f"Field 'params' must be array or object, got {type(params).__name__}")

    id_was_present = 'id' in data
    request_id = data.get('id')

    if request_id is not None and (isinstance(request_id, bool) or not isinstance(request_id, (str, int))):
        raise InvalidRequestError(f"Field 'id' must be string or integer, got {type(request_id).__name__}")

    return Request(
        method=method,
        params=params,
        id=request_id,
        version=version,
        id_was_present=id_was_present,
    )


def build_request(
    method: str,
    params: list[Any] | dict[str, Any] | None = None,
    id: str | int | None = None,
    version: Version = '2.0',
) -> dict[str, Any]:
    """Build a JSON-RPC request dict.

    Args:
        method: Method name
        params: Method parameters
        id: Request ID
        version: Protocol version

    Returns:
        Request dict ready for JSON serialization
    """
    if version == '2.0':
        result: dict[str, Any] = {
            'jsonrpc': '2.0',
            'method': method,
        }
        if params is not None:
            result['params'] = params
        if id is not None:
            result['id'] = id
    else:
        result = {
            'method': method,
            'params': params if params is not None else [],
            'id': id,
        }
    return result


def build_notification(
    method: str,
    params: list[Any] | dict[str, Any] | None = None,
    version: Version = '2.0',
) -> dict[str, Any]:
    """Build a JSON-RPC notification dict (no response expected).

    Args:
        method: Method name
        params: Method parameters
        version: Protocol version

    Returns:
        Notification dict ready for JSON serialization
    """
    if version == '2.0':
        # v2: notification has no id field
        result: dict[str, Any] = {
            'jsonrpc': '2.0',
            'method': method,
        }
        if params is not None:
            result['params'] = params
    else:
        # v1: notification has id=null
        result = {
            'method': method,
            'params': params if params is not None else [],
            'id': None,
        }
    return result
