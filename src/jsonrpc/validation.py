"""Type validation, conversion, and utility functions for JSON-RPC."""

import logging
import math
import types
import weakref
from dataclasses import MISSING, InitVar, fields, is_dataclass
from typing import (
    Any,
    Literal,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from .errors import InvalidParamsError, InvalidResultError, clip

logger = logging.getLogger('jsonrpc-lib')

MAX_NESTING_DEPTH = 64


class _TypeMismatch(Exception):
    """Raised by the merged walk when a value does not match its annotation.

    Carries no message: the wording belongs to validate_params(), which knows
    the parameter name and the declared type the caller should be told about.
    """


class _NestingDepthExceeded(InvalidParamsError):
    """Raised when params nest deeper than MAX_NESTING_DEPTH.

    A private subclass rather than a plain InvalidParamsError because the union
    handler in _convert_value() swallows InvalidParamsError to try the next arm.
    Since a union frame is the direct parent of the recursion the depth guard
    protects, catching it there would let the guard's own exception disarm the
    guard.
    """


# Cache for dataclass introspection data: type_hints, field_list, field_names,
# required_fields. Keyed weakly: a params type generated at runtime - which is
# what @rpc.method does for every decorated function - would otherwise be kept
# alive by this cache for the life of the process.
_params_type_cache: 'weakref.WeakKeyDictionary[type, tuple[dict[str, Any], tuple[Any, ...], list[str], list[str]]]' = (
    weakref.WeakKeyDictionary()
)


def _get_params_type_info(params_type: type) -> tuple[dict[str, Any], tuple[Any, ...], list[str], list[str]]:
    """Get cached introspection data for a params dataclass type.

    A parameter is a field the caller can actually set through the constructor.
    That excludes more than it looks like: get_type_hints() also reports
    ClassVar entries and the KW_ONLY sentinel, and fields() reports init=False
    fields, which the dataclass computes for itself. Counting any of them as a
    parameter breaks the method three ways - the caller cannot supply one
    (`__init__` rejects the keyword), positional params bind to the wrong
    fields, and one without a default is demanded but unsatisfiable.
    """
    info = _params_type_cache.get(params_type)
    if info is not None:
        return info

    field_list = tuple(f for f in fields(params_type) if f.init)
    all_hints = get_type_hints(params_type)
    type_hints = {f.name: all_hints[f.name] for f in field_list if f.name in all_hints}
    field_names = [f.name for f in field_list]
    required_fields = [f.name for f in field_list if f.default is MISSING and f.default_factory is MISSING]
    info = (type_hints, field_list, field_names, required_fields)
    _params_type_cache[params_type] = info
    return info


# Field name -> annotation for a result dataclass. Separate from the params
# cache because the two directions want different fields: a parameter is one the
# caller can set, so init=False is excluded there, while a result is serialized
# from fields() in full and init=False fields go out with the rest.
_result_type_cache: 'weakref.WeakKeyDictionary[type, tuple[tuple[str, Any], ...]]' = weakref.WeakKeyDictionary()


def _result_field_types(result_type: type) -> tuple[tuple[str, Any], ...]:
    """Every field of a result dataclass, paired with its annotation.

    A field whose annotation cannot be resolved is skipped rather than raised on:
    this runs while answering a request, and a forward reference the author never
    resolved is not a reason to fail a call that is otherwise fine.
    """
    info = _result_type_cache.get(result_type)
    if info is not None:
        return info

    try:
        hints = get_type_hints(result_type)
    except Exception:  # noqa: BLE001 - an unresolvable annotation is not this call's problem
        hints = {}
    info = tuple((f.name, hints[f.name]) for f in fields(result_type) if f.name in hints)
    _result_type_cache[result_type] = info
    return info


# Annotations the params validator can actually satisfy from JSON. Anything
# outside this set is refused when the method class is defined - see
# find_unsupported_annotations().
_SUPPORTED_SCALARS = frozenset({int, str, bool, float, type(None)})


def _a_string_can_be(annotation: Any) -> bool:
    """Whether some string satisfies this annotation.

    Asked only of dict key types, because that is the one position where the
    value is guaranteed to arrive as a string whatever the annotation says.

    Deliberately permissive where the validator is: `dict[int | str, str]` works
    (the str arm takes every key) and so does `dict[Literal['a', 1], str]` (the
    'a' arm does), so neither may be refused - a false alarm here rejects a
    method that would have served every caller correctly.
    """
    if annotation is str or annotation is Any:
        return True

    if get_origin(annotation) is Literal:
        return any(isinstance(member, str) for member in get_args(annotation))

    if get_origin(annotation) is Union or isinstance(annotation, types.UnionType):
        return any(_a_string_can_be(arg) for arg in get_args(annotation))

    return False


def _describe_unsupported(annotation: Any, seen: set[type]) -> str | None:
    """Return a reason if this annotation can never be satisfied from JSON.

    JSON offers strings, numbers, booleans, arrays, objects and null. An
    annotation the validator has no rule for falls through to an isinstance()
    check that a value parsed from JSON can never pass, so the method is
    uncallable - `tuple[int, int]`, `datetime`, `Decimal`, an Enum subclass.
    """
    if annotation in _SUPPORTED_SCALARS or annotation is Any:
        return None

    origin = get_origin(annotation)

    if origin is Literal:
        return None

    if origin is Union or isinstance(annotation, types.UnionType):
        for arg in get_args(annotation):
            reason = _describe_unsupported(arg, seen)
            if reason is not None:
                return reason
        return None

    if annotation is list or annotation is dict:
        return None

    if origin is list:
        for arg in get_args(annotation):
            reason = _describe_unsupported(arg, seen)
            if reason is not None:
                return reason
        return None

    if origin is dict:
        args = get_args(annotation)
        if args:
            key_type, value_type = args
            if not _a_string_can_be(key_type):
                # Not the same question as "is this type supported": `int` is
                # perfectly supported as a *value*. A JSON object key is always a
                # string, so `dict[int, str]` is a field the caller can never
                # populate - it registered without complaint and then answered
                # -32602 to every call, which is exactly the failure this whole
                # function exists to move to class-definition time.
                return f'dict key {_type_name(key_type)} - JSON object keys are always strings'
            return _describe_unsupported(value_type, seen)
        return None

    if is_dataclass(annotation) and isinstance(annotation, type):
        if annotation in seen:
            return None  # already being walked: a self-referencing structure
        seen.add(annotation)
        for field_name, nested in _resolved_field_types(annotation).items():
            reason = _describe_unsupported(nested, seen)
            if reason is not None:
                return f'{annotation.__name__}.{field_name}: {reason}'
        return None

    return _type_name(annotation)


def _resolved_field_types(params_type: type) -> dict[str, Any]:
    """Field name -> resolved annotation, for settable fields only."""
    hints = get_type_hints(params_type)
    return {f.name: hints[f.name] for f in fields(params_type) if f.init and f.name in hints}


def find_unsupported_annotations(params_type: type) -> list[str]:
    """Fields of a params dataclass the validator could never accept a value for.

    Checked when the method class is defined rather than when a request arrives.
    The alternative is what the library used to do: register happily and answer
    -32602 to every call, with a message blaming the caller's string for a type
    that would have refused everything. You would hear about it from a client.
    """
    problems = []
    for field_name, annotation in _resolved_field_types(params_type).items():
        reason = _describe_unsupported(annotation, {params_type})
        if reason is not None:
            problems.append(f'{field_name} ({reason})')
    return problems


def find_initvar_fields(params_type: type) -> list[str]:
    """Names of InitVar pseudo-fields declared on a params dataclass.

    dataclasses.fields() omits InitVar entries, so the library cannot see them:
    the caller is told 'Unknown parameter' if they send one, and `__init__`
    demands one regardless, which makes the method impossible to call by any
    route. Registration refuses such a params type rather than answering -32603
    to every request for the life of the process.
    """
    found: list[str] = []
    for base in reversed(getattr(params_type, '__mro__', [params_type])):
        for name, annotation in getattr(base, '__annotations__', {}).items():
            if isinstance(annotation, InitVar):
                found.append(name)
            elif isinstance(annotation, str) and annotation.replace('dataclasses.', '').startswith('InitVar['):
                # `from __future__ import annotations` leaves them as strings.
                found.append(name)
    return found


def is_batch(data: Any) -> bool:
    """Check if data represents a batch request.

    Args:
        data: Parsed JSON data

    Returns:
        True if data is a list (batch request)
    """
    return isinstance(data, list)


def _fits_in_float(value: int) -> bool:
    """Whether this int can become a float at all.

    JSON has one number type, so a 400-digit integer literal is a perfectly legal
    value to send for a field annotated `float` - and `float(10**400)` raises
    OverflowError, which is an ArithmeticError and so is caught by nothing on the
    validation path. It reached the generic handler in _process_single() and came
    back as `-32603 Internal error` with a full traceback logged at ERROR, once
    per request, for a 420-byte body from an unauthenticated caller.

    The isfinite() check is not redundant with the try: CPython raises
    OverflowError here, but `float(int)` does not promise that, and a build that
    returned inf instead would put a token no JSON parser accepts back on the
    wire - the very thing the NaN/Infinity guard exists to stop.
    """
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _type_name(t: type) -> str:
    """Get human-readable name for a type.

    The fallback never uses repr(): for an annotation that is an object rather
    than a class (the KW_ONLY sentinel, for instance) the default repr carries
    the object's address, and this string is sent to the caller in a -32602
    message.
    """
    if t.__class__ is type:
        # A plain class - int, str, a params dataclass. get_origin() below costs
        # more than everything else on the rejection path put together, and for
        # these it always answers None. Anything with a metaclass, and every
        # typing construct, takes the slow path and is unaffected.
        return t.__name__

    origin = get_origin(t)
    if origin is Union or isinstance(t, types.UnionType):
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
    name = getattr(t, '__name__', None)
    if isinstance(name, str):
        return name

    text = str(t)
    if ' object at 0x' in text:
        # Default object repr: it carries a live address. Name the class instead.
        return type(t).__name__
    return text


def _check_type(value: Any, expected_type: type, _result_side: bool = False, _depth: int = 0) -> bool:
    """Check if value matches expected type.

    Args:
        value: Value to check
        expected_type: Expected type annotation
        _result_side: True when checking a method's return value rather than
            inbound params. The two directions disagree about dataclasses: on the
            way in the value is still raw wire data waiting to be converted, on
            the way out it is an already-constructed instance whose fields are
            checked in turn.
        _depth: Recursion depth, bounded on the result side only - see the
            dataclass branch.

    Returns:
        True if value matches type, False otherwise
    """
    if value is None:
        if expected_type is Any:
            # `Any` means any value, null included. Refusing it produced the
            # self-contradicting "expected type 'Any', got 'NoneType'".
            return True
        origin = get_origin(expected_type)
        # Check for Union (typing.Union) or UnionType (T | None syntax)
        if origin is Union or isinstance(expected_type, types.UnionType):
            return type(None) in get_args(expected_type)
        return expected_type is type(None)

    # Scalars before get_origin(), which costs more than everything else in this
    # function and answers None for every one of them. It matters here as well as
    # in _coerce() now that a result dataclass has each of its fields checked:
    # most fields of most results are scalars.
    if expected_type is Any:
        return True

    if expected_type is int:
        # int should not match bool (bool is subclass of int)
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type is float:
        # float accepts int values. NaN and the infinities are refused: they have
        # no JSON representation, so a response carrying one is not valid JSON,
        # and NaN silently defeats every host-side bound check (`nan > limit` is
        # False, so `if amount > limit: raise` passes).
        if isinstance(value, bool):
            return False
        if isinstance(value, float):
            return math.isfinite(value)
        # An int larger than float can hold is refused for the same reason. It
        # used to pass: `10**400` returned from a method declared `-> float` went
        # out as a 400-digit integer while the generated schema promised
        # `number`, and on the inbound side it reached float() and raised
        # OverflowError from inside the validator.
        return isinstance(value, int) and _fits_in_float(value)
    if expected_type is bool:
        return isinstance(value, bool)
    if expected_type is str:
        return isinstance(value, str)

    # Before get_origin() for the same reason: walking a result means one call
    # per row, and a dataclass is never a typing construct, so nothing below can
    # claim it.
    if isinstance(expected_type, type) and is_dataclass(expected_type):
        if _result_side:
            # A result is an instance already; nothing will convert it later.
            if not isinstance(value, expected_type):
                return False
            # And then every field, because a dataclass enforces nothing at
            # runtime: `Row(n='not a number')` is ordinary Python, and it used to
            # pass this check and go out as a string under a schema promising
            # `number`. isinstance() answered the question "is this a Row", which
            # is not the question `validate_results` is named after.
            if _depth > MAX_NESTING_DEPTH:
                # A result may be cyclic - a parent holding its children holding
                # their parent - and unlike params it was built in this process,
                # so nothing has bounded it yet.
                raise _NestingDepthExceeded(f'Maximum nesting depth ({MAX_NESTING_DEPTH}) exceeded')
            return all(
                _check_type(getattr(value, name), annotation, True, _depth + 1)
                for name, annotation in _result_field_types(expected_type)
            )
        # Inbound: accept both dict (named params) and list (positional params)
        return isinstance(value, (dict, list))  # Will be converted

    origin = get_origin(expected_type)

    if origin is Union or isinstance(expected_type, types.UnionType):
        args = get_args(expected_type)
        return any(_check_type(value, arg, _result_side, _depth) for arg in args)

    if origin is Literal:
        # `type(...) is type(...)`, not `==`: True == 1 in Python, so a plain
        # membership test lets JSON `true` satisfy Literal[1] and hands the
        # method a bool where it declared an int.
        return any(type(value) is type(arg) and value == arg for arg in get_args(expected_type))

    if origin is list:
        if not isinstance(value, list):
            return False
        args = get_args(expected_type)
        if args:
            item_type = args[0]
            return all(_check_type(item, item_type, _result_side, _depth + 1) for item in value)
        return True

    if origin is dict:
        if not isinstance(value, dict):
            return False
        args = get_args(expected_type)
        if args:
            key_type, val_type = args
            return all(
                _check_type(k, key_type, _result_side, _depth) and _check_type(v, val_type, _result_side, _depth + 1)
                for k, v in value.items()
            )
        return True

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
        raise _NestingDepthExceeded(f'Maximum nesting depth ({MAX_NESTING_DEPTH}) exceeded')

    if params_type is None or params_type is type(None):
        if params is not None and params != [] and params != {}:
            # The value itself is not echoed: an error message is the one thing a
            # caller can always read back, and reflecting arbitrary input into it
            # is a needless hazard for whatever renders the message downstream.
            raise InvalidParamsError(
                f'Method accepts no parameters, but received {type(params).__name__}',
                data={'reason': 'no_parameters_accepted', 'received': type(params).__name__},
            )
        return None

    if not is_dataclass(params_type):
        raise InvalidParamsError(f'params_type must be a dataclass, got {type(params_type).__name__}')

    type_hints, field_list, field_names, required_fields = _get_params_type_info(params_type)

    if params is None:
        if required_fields:
            raise InvalidParamsError(
                f'Missing required parameters: {required_fields}',
                data={'reason': 'missing_parameter', 'parameters': list(required_fields)},
            )
        return _construct(params_type, {})

    if isinstance(params, list):
        if len(params) > len(field_names):
            raise InvalidParamsError(
                f'Too many positional parameters: expected {len(field_names)}, got {len(params)}',
                data={'reason': 'too_many_positional', 'expected': len(field_names), 'received': len(params)},
            )
        params = dict(zip(field_names, params))

    if not isinstance(params, dict):
        raise InvalidParamsError(
            f'Parameters must be object or array, got {type(params).__name__}',
            data={'reason': 'invalid_params_container', 'received': type(params).__name__},
        )

    for field_name in params:
        if field_name not in type_hints:
            # The one message that has to quote what the caller sent, so the
            # one that has to be clipped - both here and in `data`, which
            # otherwise repeats it and doubles the response.
            shown = clip(field_name)
            raise InvalidParamsError(
                f"Unknown parameter: '{shown}'",
                data={'reason': 'unknown_parameter', 'parameter': shown},
            )

    converted_params = {}
    for field_name, value in params.items():
        expected_type = type_hints[field_name]
        try:
            converted_params[field_name] = _coerce(value, expected_type, _depth=_depth)
        except _NestingDepthExceeded:
            raise
        except _TypeMismatch:
            # The merged walk reports mismatches without wording them; the name
            # of the parameter and its declared type live here.
            raise _type_mismatch(field_name, expected_type, value) from None
        except InvalidParamsError as e:
            # A nested structure refused the value - a __post_init__ rejecting
            # it, an unknown key one level down. The pre-merge order checked
            # every top-level field and every required field before descending
            # anywhere, so replay that first: with several faults in one payload
            # the caller must still get the complaint they always got.
            _diagnose(params, type_hints, field_list)
            # Name the parameter the fault came in under. Nothing below knows it:
            # a union or a nested dataclass reports what it rejected, not which
            # of the caller's arguments carried it.
            if isinstance(e.data, dict) and 'parameter' not in e.data:
                e.data = {'parameter': field_name, **e.data}
            raise

    for f in field_list:
        if f.name not in params:
            if f.default is MISSING and f.default_factory is MISSING:
                raise InvalidParamsError(
                    f"Missing required parameter: '{f.name}'",
                    data={'reason': 'missing_parameter', 'parameter': f.name},
                )

    return _construct(params_type, converted_params)


def _diagnose(
    params: dict[str, Any],
    type_hints: dict[str, Any],
    field_list: tuple[Any, ...],
) -> None:
    """Report a fault that outranks the one the merged walk already found.

    The pre-merge order checked every top-level field, then every required
    field, and only then descended. So when a nested structure refuses a value,
    those two checks still get to speak first - a request with several faults
    must earn the same complaint it always did.

    It deliberately stops there. Re-running the conversion would rediscover the
    caller's exception, which is already in hand, and it would do so by
    descending into the same nested dataclass - whose own failure would call
    this function again, once per level. That made a rejected payload cost
    2**depth: a 467-byte body with a bad leaf took seven seconds, and the depth
    limit of 64 was a price list rather than a guard. Neither loop below
    recurses: _check_type does not descend into a dataclass, it only asks
    whether the value has the right shape to become one.
    """
    for field_name, value in params.items():
        expected_type = type_hints[field_name]
        if not _check_type(value, expected_type):
            raise _type_mismatch(field_name, expected_type, value)

    for f in field_list:
        if f.name not in params:
            if f.default is MISSING and f.default_factory is MISSING:
                raise InvalidParamsError(
                    f"Missing required parameter: '{f.name}'",
                    data={'reason': 'missing_parameter', 'parameter': f.name},
                )


def _out_of_range_detail(expected_type: Any, value: Any) -> tuple[str, str] | None:
    """Why "expected X, got Y" would be a lie for this pair.

    A `float` field refuses two things whose type is not the problem: a
    non-finite float, and an int too large to become one. Reporting the first as
    "expected type 'float', got 'float'" is self-contradicting, and the second as
    "got 'int'" says the opposite of the truth - an int is exactly what this
    field accepts.

    Returns (sentence fragment, machine reason), or None to use the default.
    """
    if expected_type is float:
        if type(value) is float and not math.isfinite(value):
            return 'must be a finite number', 'not_finite'
        if type(value) is int and not _fits_in_float(value):
            return 'is out of range for float', 'out_of_range'
    return None


def _type_mismatch(field_name: str, expected_type: Any, value: Any) -> InvalidParamsError:
    """The complaint about a value whose type does not match its annotation.

    Message and data are built together because they say the same thing, and
    _type_name() is not cheap: computing it once for the sentence and again for
    the dict cost 17% of the whole rejected-request path.

    Nothing in `data` is new information, and the rejected value itself is never
    included - see the note in validate_params().
    """
    expected = _type_name(expected_type)
    received = type(value).__name__

    detail = _out_of_range_detail(expected_type, value)
    if detail is not None:
        sentence, reason = detail
        return InvalidParamsError(
            f"Parameter '{field_name}' {sentence}",
            data={'reason': reason, 'parameter': field_name, 'expected': expected, 'received': received},
        )

    return InvalidParamsError(
        f"Parameter '{field_name}' expected type '{expected}', got '{received}'",
        data={
            'reason': 'type_mismatch',
            'parameter': field_name,
            'expected': expected,
            'received': received,
        },
    )


def _construct(params_type: type, values: dict[str, Any]) -> Any:
    """Build the params dataclass, mapping __post_init__ rejections to -32602.

    A dataclass validating itself in __post_init__ is ordinary Python, and this
    library recommends it as the way to accept a value JSON cannot express - take
    the field as `str`, convert it here. So a rejection here is the caller's
    mistake, and answering it with `-32603 Internal error` plus a traceback per
    request was the library reporting its own failure for their bad input.

    Two channels, and which one you are in depends on what you raise:

    - `JSONRPCError` (usually `InvalidParamsError`) - the message goes to the
      caller verbatim. That is the point: the whole reason to write
      `raise InvalidParamsError('age must be positive')` is that someone reads
      it. Keep the text fit for a stranger.
    - `ValueError` or `ArithmeticError` - `-32602` with a fixed message; the
      exception's own text goes to the log and no further. It is not your text.
      `datetime.fromisoformat`, `int`, `Decimal`, `UUID` and `json.loads` all
      embed the input they were given in the message they raise, so passing that
      through would reflect the caller's bytes back out through the channel this
      library documents as the way to validate - and into whatever renders an
      error downstream. `ArithmeticError` is here because `Decimal('x')` raises
      `InvalidOperation` and `datetime.fromtimestamp(1e20)` raises
      `OverflowError`; both are ordinary bad input and neither is a `ValueError`.

    Deliberately not caught at all:

    - `TypeError` - at this exact call it is how a mis-built params dataclass
      announces itself (`__init__() got an unexpected keyword argument`). Mapping
      it to -32602 would tell the caller their params are wrong when the server
      is wrong, and send the operator looking in the wrong place.
    - `AssertionError` - `assert` vanishes under `-O`, so -32602 would suggest a
      validation that is not running in production.
    - anything else (`KeyError`, `AttributeError`, `re.error`) - those are a
      method's own bug, not a verdict on the caller's value.
    """
    try:
        return params_type(**values)
    except (ValueError, ArithmeticError) as e:
        logger.info('%s rejected params in __post_init__: %s: %s', params_type.__name__, type(e).__name__, e)
        # No parameter name: __post_init__ sees the whole object and the
        # rejection may well be about a combination of fields. The caller who
        # needs one gets it from the field the payload came in under, which
        # validate_params() adds on the way out.
        raise InvalidParamsError(
            'Invalid params: rejected by the parameter type',
            data={'reason': 'rejected_by_validator'},
        ) from e


def _describe_variant_failures(expected_type: type, failures: list[tuple[Any, str]]) -> str:
    """Compose the message for a union that matched none of its variants.

    Without the individual reasons the caller only learns that nothing matched,
    which for `Cat | Dog` is exactly as useful as silence. Truncated, because a
    ten-variant union would otherwise produce a message nobody reads.
    """
    shown = failures[:3]
    parts = '; '.join(f'{_type_name(arg)} - {reason}' for arg, reason in shown)
    remaining = len(failures) - len(shown)
    if remaining > 0:
        parts += f'; and {remaining} more'
    base = f"Value does not match any variant of '{_type_name(expected_type)}'"
    return f'{base}: {parts}' if parts else base


def _walk_value_depth(value: Any, _depth: int) -> None:
    """Enforce the nesting bound against the *value*, not the annotation.

    The annotation-driven walk only descends where it has something to convert,
    so a field typed `list` or `Any` let a payload of any depth through: no
    get_args(), no recursion, no counter. The bound has to hold for what the
    caller actually sent, whatever the field is called.
    """
    if _depth > MAX_NESTING_DEPTH:
        raise _NestingDepthExceeded(f'Maximum nesting depth ({MAX_NESTING_DEPTH}) exceeded')
    if isinstance(value, list):
        for item in value:
            _walk_value_depth(item, _depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            _walk_value_depth(item, _depth + 1)


def _coerce(value: Any, expected_type: type, _depth: int = 0) -> Any:
    """Check and convert in a single descent.

    The two-pass form walked the whole value once to check it and again to
    convert it, which on a list of a few thousand objects is the dominant cost
    of the request. This does both, raising _TypeMismatch where the check pass
    would have returned False.

    Failures raised by a nested dataclass - a __post_init__ rejecting a value,
    an unknown key one level down - propagate as they are: those messages name
    the inner field and are more useful than anything this frame could say.
    """
    if _depth > MAX_NESTING_DEPTH:
        raise _NestingDepthExceeded(f'Maximum nesting depth ({MAX_NESTING_DEPTH}) exceeded')

    if value is None:
        origin = get_origin(expected_type)
        if origin is Union or isinstance(expected_type, types.UnionType):
            if type(None) in get_args(expected_type):
                return None
            raise _TypeMismatch
        if expected_type is type(None) or expected_type is Any:
            return None
        raise _TypeMismatch

    # Scalars first: most fields in most payloads, and none of them needs any
    # of the machinery below.
    if expected_type is int:
        if type(value) is int:
            return value
        raise _TypeMismatch
    if expected_type is str:
        if type(value) is str:
            return value
        raise _TypeMismatch
    if expected_type is bool:
        if type(value) is bool:
            return value
        raise _TypeMismatch
    if expected_type is float:
        if type(value) is float:
            if math.isfinite(value):
                return value
            raise _TypeMismatch
        if type(value) is int:
            # Not a plain float(value): JSON has one number type, so a 400-digit
            # integer literal is a legal thing to send here, and float() raises
            # OverflowError on it - an ArithmeticError nothing on this path
            # catches. See _fits_in_float().
            if _fits_in_float(value):
                return float(value)
            raise _TypeMismatch
        raise _TypeMismatch
    if expected_type is Any:
        _walk_value_depth(value, _depth)
        return value

    origin = get_origin(expected_type)

    if origin is Union or isinstance(expected_type, types.UnionType):
        args = get_args(expected_type)
        failures: list[tuple[Any, str]] | None = None
        for arg in args:
            if arg is not type(None):
                try:
                    return _coerce(value, arg, _depth=_depth)
                except _NestingDepthExceeded:
                    raise
                except _TypeMismatch:
                    # A plain shape mismatch: this variant has nothing to say
                    # beyond its own name, which the caller can already see in
                    # the annotation.
                    continue
                except (TypeError, ValueError, InvalidParamsError) as e:
                    if failures is None:
                        failures = []
                    failures.append((arg, str(e)))
                    continue

        if failures is None:
            # Every variant simply had the wrong shape. That is an ordinary type
            # mismatch, and the caller is better served by being told the field
            # and the whole union than by a list of variants each saying "not me".
            raise _TypeMismatch
        raise InvalidParamsError(
            _describe_variant_failures(expected_type, failures),
            data={'reason': 'no_matching_variant', 'expected': _type_name(expected_type)},
        )

    if origin is Literal:
        if any(type(value) is type(arg) and value == arg for arg in get_args(expected_type)):
            return value
        raise _TypeMismatch

    if origin is list:
        if not isinstance(value, list):
            raise _TypeMismatch
        args = get_args(expected_type)
        if args:
            item_type = args[0]
            return [_coerce(item, item_type, _depth=_depth + 1) for item in value]
        _walk_value_depth(value, _depth)
        return value

    if origin is dict:
        if not isinstance(value, dict):
            raise _TypeMismatch
        args = get_args(expected_type)
        if args and len(args) == 2:
            key_type, val_type = args
            result = {}
            for k, v in value.items():
                _coerce(k, key_type, _depth=_depth)
                result[k] = _coerce(v, val_type, _depth=_depth + 1)
            return result
        _walk_value_depth(value, _depth)
        return value

    if expected_type is list or expected_type is dict:
        if not isinstance(value, expected_type):
            raise _TypeMismatch
        _walk_value_depth(value, _depth)
        return value

    if is_dataclass(expected_type) and isinstance(expected_type, type):
        if not isinstance(value, (dict, list)):
            raise _TypeMismatch
        return validate_params(value, expected_type, _depth=_depth + 1)

    try:
        if isinstance(value, expected_type):
            return value
    except TypeError:
        raise InvalidParamsError(
            f"Cannot validate type '{_type_name(expected_type)}' for value of type '{type(value).__name__}'. "
            f'Unsupported type annotation.'
        )
    raise _TypeMismatch


def validate_result_type(result: Any, result_type: type) -> None:
    """Validate that result matches expected type.

    Note that this runs after execute() has returned, so it reports a contract
    violation - it cannot prevent one. A method that changed state and then
    returned the wrong type has already changed it; the caller sees -32001 for a
    call that did happen.

    Args:
        result: Actual return value from execute()
        result_type: Expected type (basic type or dataclass)

    Raises:
        InvalidResultError: If result doesn't match expected type
    """
    try:
        matches = _check_type(result, result_type, _result_side=True)
    except _NestingDepthExceeded as e:
        # Must precede the InvalidParamsError arm below - it is a subclass. A
        # result can be cyclic, and unlike params it was built in this process,
        # so this is the first thing that bounds it.
        raise InvalidResultError(f'Return value nests deeper than {MAX_NESTING_DEPTH} levels') from e
    except InvalidParamsError as e:
        # The type machinery is shared with the params direction and reports an
        # annotation it cannot handle as -32602. On this side the annotation is
        # the server's own return type, and blaming the caller's params for it
        # is misleading.
        raise InvalidResultError(
            f"Cannot validate return type '{_type_name(result_type)}': unsupported type annotation"
        ) from e

    if not matches:
        raise InvalidResultError(_describe_result_mismatch(result, result_type))


def _describe_result_mismatch(result: Any, result_type: type, path: str = '') -> str:
    """Say which part of the result is wrong, not just that something is.

    Runs only after _check_type has already said no, so it can afford to walk the
    structure again; the cost is on the failing path only.

    Worth the second walk because the alternative is unreadable. Now that a
    result dataclass has its fields checked, the top-level `isinstance` passes
    and the old sentence became "Expected return type 'Report', got 'Report'" -
    the exact self-contradiction that made this check useless before 0.4.0, back
    again in a new place.
    """
    detail = _out_of_range_detail(result_type, result)
    if detail is not None:
        where = f" at '{path}'" if path else ''
        return f'Return value{where} {detail[0]}'

    if is_dataclass(result_type) and isinstance(result_type, type) and isinstance(result, result_type):
        for name, annotation in _result_field_types(result_type):
            field_value = getattr(result, name)
            if not _check_type(field_value, annotation, True):
                return _describe_result_mismatch(field_value, annotation, f'{path}.{name}' if path else name)

    origin = get_origin(result_type)
    args = get_args(result_type)

    if origin is list and isinstance(result, list) and args:
        for index, item in enumerate(result):
            if not _check_type(item, args[0], True):
                return _describe_result_mismatch(item, args[0], f'{path}[{index}]')

    if origin is dict and isinstance(result, dict) and len(args) == 2:
        for key, item in result.items():
            if not _check_type(item, args[1], True):
                return _describe_result_mismatch(item, args[1], f'{path}[{key!r}]')

    where = f" at '{path}'" if path else ''
    return f"Expected return type '{_type_name(result_type)}'{where}, got '{type(result).__name__}'"


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
