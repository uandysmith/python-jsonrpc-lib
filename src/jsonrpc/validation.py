"""Type validation, conversion, and utility functions for JSON-RPC."""

import types
from dataclasses import MISSING, fields, is_dataclass
from typing import (
    Any,
    Literal,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from .errors import InvalidParamsError, InvalidResultError

MAX_NESTING_DEPTH = 64

# Cache for dataclass introspection data: type_hints, field_list, field_names, required_fields
_params_type_cache: dict[type, tuple[dict[str, Any], tuple[Any, ...], list[str], list[str]]] = {}


def _get_params_type_info(params_type: type) -> tuple[dict[str, Any], tuple[Any, ...], list[str], list[str]]:
    """Get cached introspection data for a params dataclass type."""
    info = _params_type_cache.get(params_type)
    if info is not None:
        return info
    type_hints = get_type_hints(params_type)
    field_list = fields(params_type)
    field_names = [f.name for f in field_list]
    required_fields = [f.name for f in field_list if f.default is MISSING and f.default_factory is MISSING]
    info = (type_hints, field_list, field_names, required_fields)
    _params_type_cache[params_type] = info
    return info


def is_batch(data: Any) -> bool:
    """Check if data represents a batch request.

    Args:
        data: Parsed JSON data

    Returns:
        True if data is a list (batch request)
    """
    return isinstance(data, list)


def _type_name(t: type) -> str:
    """Get human-readable name for a type."""
    origin = get_origin(t)
    if origin is Union:
        args = get_args(t)
        # Handle Optional (T | None)
        if len(args) == 2 and type(None) in args:
            other = args[0] if args[1] is type(None) else args[1]
            return f'{_type_name(other)} | None'
        return ' | '.join(_type_name(a) for a in args)
    if origin is list:
        args = get_args(t)
        if args:
            return f'list[{_type_name(args[0])}]'
        return 'list'
    if origin is dict:
        args = get_args(t)
        if args:
            return f'dict[{_type_name(args[0])}, {_type_name(args[1])}]'
        return 'dict'
    if origin is Literal:
        args = get_args(t)
        return f'Literal{list(args)}'
    if hasattr(t, '__name__'):
        return t.__name__
    return str(t)


def _check_type(value: Any, expected_type: type) -> bool:
    """Check if value matches expected type.

    Args:
        value: Value to check
        expected_type: Expected type annotation

    Returns:
        True if value matches type, False otherwise
    """
    if value is None:
        origin = get_origin(expected_type)
        # Check for Union (typing.Union) or UnionType (T | None syntax)
        if origin is Union or isinstance(expected_type, types.UnionType):
            return type(None) in get_args(expected_type)
        return expected_type is type(None)

    origin = get_origin(expected_type)

    if origin is Union or isinstance(expected_type, types.UnionType):
        args = get_args(expected_type)
        return any(_check_type(value, arg) for arg in args)

    if origin is Literal:
        return value in get_args(expected_type)

    if origin is list:
        if not isinstance(value, list):
            return False
        args = get_args(expected_type)
        if args:
            item_type = args[0]
            return all(_check_type(item, item_type) for item in value)
        return True

    if origin is dict:
        if not isinstance(value, dict):
            return False
        args = get_args(expected_type)
        if args:
            key_type, val_type = args
            return all(_check_type(k, key_type) and _check_type(v, val_type) for k, v in value.items())
        return True

    if expected_type is Any:
        return True

    if expected_type is int:
        # int should not match bool (bool is subclass of int)
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type is float:
        # float accepts int values
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type is bool:
        return isinstance(value, bool)
    if expected_type is str:
        return isinstance(value, str)

    if is_dataclass(expected_type) and isinstance(expected_type, type):
        # Accept both dict (named params) and list (positional params)
        return isinstance(value, (dict, list))  # Will be converted

    try:
        return isinstance(value, expected_type)
    except TypeError:
        raise InvalidParamsError(
            f"Cannot validate type '{_type_name(expected_type)}' for value of type '{type(value).__name__}'. "
            f'Unsupported type annotation.'
        )


def validate_params(
    params: list[Any] | dict[str, Any] | None,
    params_type: type | None,
    _depth: int = 0,
) -> Any:
    """Validate and convert params to dataclass instance.

    Args:
        params: Raw params from JSON-RPC request
        params_type: Target dataclass type (or None for no-params methods)
        _depth: Internal recursion depth counter (do not set manually)

    Returns:
        Validated dataclass instance, or None if params_type is None

    Raises:
        InvalidParamsError: With descriptive message on validation failure
    """
    if _depth > MAX_NESTING_DEPTH:
        raise InvalidParamsError(f'Maximum nesting depth ({MAX_NESTING_DEPTH}) exceeded')

    if params_type is None or params_type is type(None):
        if params is not None and params != [] and params != {}:
            raise InvalidParamsError(f'Method accepts no parameters, but received: {params}')
        return None

    if not is_dataclass(params_type):
        raise InvalidParamsError(f'params_type must be a dataclass, got {type(params_type).__name__}')

    type_hints, field_list, field_names, required_fields = _get_params_type_info(params_type)

    if params is None:
        if required_fields:
            raise InvalidParamsError(f'Missing required parameters: {required_fields}')
        return params_type()

    if isinstance(params, list):
        if len(params) > len(field_names):
            raise InvalidParamsError(f'Too many positional parameters: expected {len(field_names)}, got {len(params)}')
        params = dict(zip(field_names, params))

    if not isinstance(params, dict):
        raise InvalidParamsError(f'Parameters must be object or array, got {type(params).__name__}')

    for field_name in params:
        if field_name not in type_hints:
            raise InvalidParamsError(f"Unknown parameter: '{field_name}'")

    for field_name, value in params.items():
        expected_type = type_hints[field_name]
        if not _check_type(value, expected_type):
            raise InvalidParamsError(
                f"Parameter '{field_name}' expected type '{_type_name(expected_type)}', got '{type(value).__name__}'"
            )

    for f in field_list:
        if f.name not in params:
            if f.default is MISSING and f.default_factory is MISSING:
                raise InvalidParamsError(f"Missing required parameter: '{f.name}'")

    converted_params = {}
    for field_name, value in params.items():
        expected_type = type_hints[field_name]
        converted_params[field_name] = _convert_value(value, expected_type, _depth=_depth)

    return params_type(**converted_params)


def _convert_value(value: Any, expected_type: type, _depth: int = 0) -> Any:
    """Convert value to expected type, handling nested dataclasses."""
    if value is None:
        return None

    origin = get_origin(expected_type)

    if origin is Union or isinstance(expected_type, types.UnionType):
        args = get_args(expected_type)
        # Try non-None types first
        for arg in args:
            if arg is not type(None):
                try:
                    return _convert_value(value, arg, _depth=_depth)
                except (TypeError, ValueError, InvalidParamsError):
                    continue
        return value

    if origin is list and isinstance(value, list):
        args = get_args(expected_type)
        if args:
            item_type = args[0]
            return [_convert_value(item, item_type, _depth=_depth) for item in value]
        return value

    if origin is dict and isinstance(value, dict):
        args = get_args(expected_type)
        if args and len(args) == 2:
            val_type = args[1]
            return {k: _convert_value(v, val_type, _depth=_depth) for k, v in value.items()}
        return value

    if is_dataclass(expected_type) and isinstance(expected_type, type):
        if isinstance(value, (dict, list)):
            # Recursively validate nested dataclass
            return validate_params(value, expected_type, _depth=_depth + 1)

    return value


def validate_result_type(result: Any, result_type: type) -> None:
    """Validate that result matches expected type.

    Args:
        result: Actual return value from execute()
        result_type: Expected type (basic type or dataclass)

    Raises:
        InvalidResultError: If result doesn't match expected type
    """
    if not _check_type(result, result_type):
        raise InvalidResultError(f"Expected return type '{_type_name(result_type)}', got '{type(result).__name__}'")


def _unwrap_optional(type_hint: type) -> type:
    """Unwrap Optional[T] or T | None to T.

    For params, we want the actual type, not Optional wrapper.

    Args:
        type_hint: Type annotation that may be Optional

    Returns:
        Unwrapped type (T from Optional[T] or T | None)

    Examples:
        Optional[AddParams] -> AddParams
        AddParams | None -> AddParams
        AddParams -> AddParams
        None -> type(None)
    """
    origin = get_origin(type_hint)

    # Handle Union types (including T | None syntax)
    if origin is Union or isinstance(type_hint, types.UnionType):
        args = get_args(type_hint)

        # If it's a 2-arg Union with None, return the non-None type
        if len(args) == 2 and type(None) in args:
            result: type = args[0] if args[1] is type(None) else args[1]
            return result

    return type_hint
