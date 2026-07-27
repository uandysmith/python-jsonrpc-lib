"""Utility tests for JSON-RPC infrastructure.

This module tests core utility functions and validation logic:
- Batch request handling (is_batch)
- Request parsing and building (parse_request, build_request, build_notification)
- Response parsing and building (build_response, build_error_response, parse_response)
- Parameter validation (validate_params with dataclasses)
- Result type validation (validate_result_type)
"""

import contextlib
import json
import types
import typing
import unittest
from dataclasses import KW_ONLY, dataclass, field, is_dataclass
from typing import Any, ClassVar, Literal, Optional, Union, get_args, get_origin

from jsonrpc import JSONRPC, Method
from jsonrpc.errors import (
    InvalidParamsError,
    InvalidRequestError,
    InvalidResultError,
    ParseError,
    RPCError,
    ServerError,
)
from jsonrpc.request import build_notification, build_request, parse_request
from jsonrpc.response import _dataclass_to_dict, build_error_response, build_response, parse_response
from jsonrpc.types import ErrorResponse, Response
from jsonrpc.validation import (
    MAX_NESTING_DEPTH,
    _check_type,
    _coerce,
    _describe_variant_failures,
    _NestingDepthExceeded,
    _type_name,
    _TypeMismatch,
    _walk_value_depth,
    find_unsupported_annotations,
    is_batch,
    validate_params,
    validate_result_type,
)

# ---------------------------------------------------------------------------
# The reference validator
#
# Until 0.4.0 this pair - _check_type to decide, then _convert_value to build -
# was how params were validated. 0.4.0 merged them into _coerce(), which decides
# and builds in one descent, and the library no longer calls this function at
# all. It lives here rather than in the package because a library should not
# ship code nothing in it runs; it stays because TestTheTwoValidatorsAgree pins
# the merged walk against it, and that comparison is only worth anything if the
# thing being compared to is the code that shipped for three releases.
#
# Do not fix bugs in it. If it and _coerce() disagree, that is the test doing
# its job, and the question is which of the two is right.
# ---------------------------------------------------------------------------


def _convert_value(value: Any, expected_type: type, _depth: int = 0) -> Any:
    """Convert value to expected type, handling nested dataclasses.

    Every container hop increases the depth counter, so the nesting bound is a
    property of the recursion rather than of how the annotation happens to be
    spelled.
    """
    if value is None:
        return None

    if _depth > MAX_NESTING_DEPTH:
        raise _NestingDepthExceeded(f'Maximum nesting depth ({MAX_NESTING_DEPTH}) exceeded')

    if expected_type is int or expected_type is str or expected_type is bool:
        return value
    if expected_type is float:
        # `_check_type` accepts an int for a float field, so without this the
        # method receives an int where its annotation - and mypy - say float.
        return float(value) if type(value) is int else value

    origin = get_origin(expected_type)

    if origin is Union or isinstance(expected_type, types.UnionType):
        args = get_args(expected_type)
        failures: list[tuple[Any, str]] | None = None
        for arg in args:  # non-None variants first
            if arg is not type(None):
                try:
                    return _convert_value(value, arg, _depth=_depth)
                except _NestingDepthExceeded:
                    # The depth guard is not a failed arm - it is the guard this
                    # frame would otherwise disarm.
                    raise
                except (TypeError, ValueError, InvalidParamsError) as e:
                    if failures is None:
                        failures = []
                    failures.append((arg, str(e)))
                    continue
        raise InvalidParamsError(
            _describe_variant_failures(expected_type, failures or []),
            data={'reason': 'no_matching_variant', 'expected': _type_name(expected_type)},
        )

    if origin is list and isinstance(value, list):
        args = get_args(expected_type)
        if args:
            return [_convert_value(item, args[0], _depth=_depth + 1) for item in value]
        _walk_value_depth(value, _depth)
        return value

    if origin is dict and isinstance(value, dict):
        args = get_args(expected_type)
        if args and len(args) == 2:
            return {k: _convert_value(v, args[1], _depth=_depth + 1) for k, v in value.items()}
        _walk_value_depth(value, _depth)
        return value

    if expected_type is Any or expected_type is list or expected_type is dict:
        # Nothing to convert, but the payload still has to respect the bound.
        _walk_value_depth(value, _depth)
        return value

    if is_dataclass(expected_type) and isinstance(expected_type, type):
        if isinstance(value, (dict, list)):
            return validate_params(value, expected_type, _depth=_depth + 1)

    return value


@dataclass
class AddParams:
    a: int
    b: int


@dataclass
class OptionalParams:
    required: str
    optional: str = 'default'


@dataclass
class NestedParams:
    name: str
    address: 'AddressParams'


@dataclass
class AddressParams:
    city: str
    zip_code: str


@dataclass
class Recipient:
    """One variant of a tagged union."""

    kind: Literal['user']
    user_id: int


@dataclass
class Card:
    """The other variant of the same union."""

    kind: Literal['card']
    token: str


@dataclass
class SendUnion:
    to: list[Recipient | Card]


@dataclass
class SendStrict:
    to: list[Recipient]


@dataclass
class OptionalRecipient:
    to: Recipient | None = None


@dataclass
class MappedRecipients:
    by_name: dict[str, Recipient | Card]


# Self-referencing dataclasses have to live at module level: get_type_hints()
# resolves the forward reference against the defining module's namespace.
@dataclass
class OptionalNode:
    name: str
    child: Optional['OptionalNode'] = None


@dataclass
class UnionNode:
    name: str
    child: 'UnionNode | None' = None


@dataclass
class ListNode:
    name: str
    children: list['ListNode'] | None = None


@dataclass
class DictNode:
    name: str
    children: dict[str, 'DictNode'] | None = None


@dataclass
class RecursiveComment:
    """A self-referencing dataclass - a comment tree, a filter, an org node."""

    text: str
    reply: 'RecursiveComment | None' = None


@dataclass
class RecursiveCommentParams:
    comment: RecursiveComment


class TestIsBatch(unittest.TestCase):
    """Tests for batch detection logic."""

    def test_is_batch_with_list(self):
        self.assertTrue(is_batch([]))
        self.assertTrue(is_batch([1, 2, 3]))

    def test_is_batch_with_dict(self):
        self.assertFalse(is_batch({}))
        self.assertFalse(is_batch({'method': 'test'}))

    def test_is_batch_with_other_types(self):
        self.assertFalse(is_batch('string'))
        self.assertFalse(is_batch(123))
        self.assertFalse(is_batch(None))


class TestParseRequest(unittest.TestCase):
    """Tests for parsing from JSON/bytes/dicts, validation, error cases."""

    def test_parse_v2_request(self):
        data = '{"jsonrpc": "2.0", "method": "test", "params": [1, 2], "id": 1}'
        req = parse_request(data)
        self.assertEqual(req.method, 'test')
        self.assertEqual(req.params, [1, 2])
        self.assertEqual(req.id, 1)
        self.assertEqual(req.version, '2.0')

    def test_parse_v2_request_dict_params(self):
        data = '{"jsonrpc": "2.0", "method": "test", "params": {"a": 1}, "id": 1}'
        req = parse_request(data)
        self.assertEqual(req.params, {'a': 1})

    def test_parse_v2_request_no_params(self):
        data = '{"jsonrpc": "2.0", "method": "test", "id": 1}'
        req = parse_request(data)
        self.assertIsNone(req.params)

    def test_parse_v2_notification(self):
        data = '{"jsonrpc": "2.0", "method": "notify", "params": [1]}'
        req = parse_request(data)
        self.assertIsNone(req.id)
        self.assertTrue(req.is_notification)

    def test_parse_v1_request(self):
        data = '{"method": "test", "params": [1, 2], "id": 1}'
        req = parse_request(data)
        self.assertEqual(req.version, '1.0')
        self.assertEqual(req.params, [1, 2])

    def test_parse_v1_notification(self):
        """v1.0 marks a notification with an explicit null id."""
        data = '{"method": "notify", "params": [], "id": null}'
        req = parse_request(data)
        self.assertTrue(req.is_notification)
        self.assertIsNone(req.id)

    def test_parse_v1_request_with_an_id_is_not_a_notification(self):
        req = parse_request('{"method": "notify", "params": [], "id": 1}')
        self.assertFalse(req.is_notification)

    def test_parse_from_bytes(self):
        data = b'{"jsonrpc": "2.0", "method": "test", "id": 1}'
        req = parse_request(data)
        self.assertEqual(req.method, 'test')

    def test_parse_from_dict(self):
        data = {'jsonrpc': '2.0', 'method': 'test', 'id': 1}
        req = parse_request(data)
        self.assertEqual(req.method, 'test')

    def test_parse_batch(self):
        data = '[{"jsonrpc": "2.0", "method": "a", "id": 1}, {"jsonrpc": "2.0", "method": "b", "id": 2}]'
        requests = parse_request(data)
        self.assertIsInstance(requests, list)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].method, 'a')
        self.assertEqual(requests[1].method, 'b')

    def test_parse_invalid_json(self):
        with self.assertRaises(ParseError) as ctx:
            parse_request('{invalid}')
        self.assertIn('Invalid JSON', str(ctx.exception))

    def test_parse_missing_method(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            parse_request('{"jsonrpc": "2.0", "id": 1}')
        self.assertIn("Missing required field: 'method'", str(ctx.exception))

    def test_parse_invalid_method_type(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            parse_request('{"jsonrpc": "2.0", "method": 123, "id": 1}')
        self.assertIn('must be string', str(ctx.exception))

    def test_parse_invalid_params_type(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            parse_request('{"jsonrpc": "2.0", "method": "test", "params": "invalid", "id": 1}')
        self.assertIn('must be array or object', str(ctx.exception))

    def test_parse_invalid_id_type(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            parse_request('{"jsonrpc": "2.0", "method": "test", "id": [1]}')
        self.assertIn('must be string or integer', str(ctx.exception))

    def test_parse_boolean_id_rejected(self):
        """Boolean id must be rejected (bool is subclass of int in Python)."""
        with self.assertRaises(InvalidRequestError) as ctx:
            parse_request('{"jsonrpc": "2.0", "method": "test", "id": true}')
        self.assertIn('must be string or integer', str(ctx.exception))

        with self.assertRaises(InvalidRequestError) as ctx:
            parse_request('{"jsonrpc": "2.0", "method": "test", "id": false}')
        self.assertIn('must be string or integer', str(ctx.exception))

    def test_parse_invalid_version(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            parse_request('{"jsonrpc": "1.0", "method": "test", "id": 1}')
        self.assertIn('Invalid jsonrpc version', str(ctx.exception))

    def test_parse_v1_dict_params_lenient(self):
        """Test v1.0 with dict params - lenient implementation accepts them."""
        req = parse_request('{"method": "test", "params": {"a": 1}, "id": 1}')
        self.assertEqual(req.version, '1.0')
        self.assertEqual(req.params, {'a': 1})

    def test_parse_empty_batch(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            parse_request('[]')
        self.assertIn('Empty batch', str(ctx.exception))

    def test_parse_not_object_or_array(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            parse_request('"string"')
        self.assertIn('must be object or array', str(ctx.exception))


class TestBuildRequest(unittest.TestCase):
    """Tests for request construction for v1/v2."""

    def test_build_v2_request(self):
        req = build_request('test', params=[1, 2], id=1, version='2.0')
        self.assertEqual(
            req,
            {
                'jsonrpc': '2.0',
                'method': 'test',
                'params': [1, 2],
                'id': 1,
            },
        )

    def test_build_v2_request_no_params(self):
        req = build_request('test', id=1, version='2.0')
        self.assertEqual(req, {'jsonrpc': '2.0', 'method': 'test', 'id': 1})

    def test_build_v2_request_dict_params(self):
        req = build_request('test', params={'a': 1}, id=1, version='2.0')
        self.assertEqual(req['params'], {'a': 1})

    def test_build_v1_request(self):
        req = build_request('test', params=[1, 2], id=1, version='1.0')
        self.assertEqual(req, {'method': 'test', 'params': [1, 2], 'id': 1})

    def test_build_v1_request_no_params(self):
        req = build_request('test', id=1, version='1.0')
        self.assertEqual(req['params'], [])


class TestBuildNotification(unittest.TestCase):
    """Tests for notification building."""

    def test_build_v2_notification(self):
        notif = build_notification('notify', params=[1], version='2.0')
        self.assertEqual(notif, {'jsonrpc': '2.0', 'method': 'notify', 'params': [1]})
        self.assertNotIn('id', notif)

    def test_build_v2_notification_no_params(self):
        notif = build_notification('notify', version='2.0')
        self.assertEqual(notif, {'jsonrpc': '2.0', 'method': 'notify'})

    def test_build_v1_notification(self):
        notif = build_notification('notify', params=[1], version='1.0')
        self.assertEqual(notif, {'method': 'notify', 'params': [1], 'id': None})


class TestBuildResponse(unittest.TestCase):
    """Tests for response construction."""

    def test_build_v2_response(self):
        resp = build_response(result=42, id=1, version='2.0')
        self.assertEqual(resp, {'jsonrpc': '2.0', 'result': 42, 'id': 1})

    def test_build_v1_response(self):
        resp = build_response(result=42, id=1, version='1.0')
        self.assertEqual(resp, {'result': 42, 'error': None, 'id': 1})

    def test_build_response_with_complex_result(self):
        result = {'data': [1, 2, 3], 'status': 'ok'}
        resp = build_response(result=result, id=1, version='2.0')
        self.assertEqual(resp['result'], result)


class TestBuildErrorResponse(unittest.TestCase):
    """Tests for error response building."""

    def test_build_v2_error_response(self):
        error = RPCError(code=-32600, message='Invalid Request')
        resp = build_error_response(error=error, id=1, version='2.0')
        self.assertEqual(
            resp,
            {
                'jsonrpc': '2.0',
                'error': {'code': -32600, 'message': 'Invalid Request'},
                'id': 1,
            },
        )

    def test_build_v1_error_response(self):
        error = RPCError(code=-32600, message='Invalid Request')
        resp = build_error_response(error=error, id=1, version='1.0')
        self.assertEqual(
            resp,
            {
                'result': None,
                'error': {'code': -32600, 'message': 'Invalid Request'},
                'id': 1,
            },
        )

    def test_build_error_response_null_id(self):
        error = RPCError(code=-32700, message='Parse error')
        resp = build_error_response(error=error, id=None, version='2.0')
        self.assertIsNone(resp['id'])

    def test_build_error_response_with_data(self):
        error = RPCError(code=-32602, message='Invalid params', data={'field': 'a'})
        resp = build_error_response(error=error, id=1, version='2.0')
        self.assertEqual(resp['error']['data'], {'field': 'a'})


class TestParseResponse(unittest.TestCase):
    """Tests for response parsing."""

    def test_parse_v2_success_response(self):
        data = '{"jsonrpc": "2.0", "result": 42, "id": 1}'
        resp = parse_response(data)
        self.assertIsInstance(resp, Response)
        self.assertEqual(resp.result, 42)
        self.assertEqual(resp.id, 1)
        self.assertEqual(resp.version, '2.0')

    def test_parse_v2_error_response(self):
        data = '{"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid"}, "id": 1}'
        resp = parse_response(data)
        self.assertIsInstance(resp, ErrorResponse)
        self.assertEqual(resp.error.code, -32600)
        self.assertEqual(resp.error.message, 'Invalid')

    def test_parse_v1_success_response(self):
        data = '{"result": 42, "error": null, "id": 1}'
        resp = parse_response(data)
        self.assertIsInstance(resp, Response)
        self.assertEqual(resp.version, '1.0')

    def test_parse_v1_error_response(self):
        data = '{"result": null, "error": {"code": -32600, "message": "Invalid"}, "id": 1}'
        resp = parse_response(data)
        self.assertIsInstance(resp, ErrorResponse)

    def test_parse_from_dict(self):
        data = {'jsonrpc': '2.0', 'result': 42, 'id': 1}
        resp = parse_response(data)
        self.assertEqual(resp.result, 42)

    def test_parse_invalid_json(self):
        with self.assertRaises(ParseError):
            parse_response('{invalid}')

    def test_parse_missing_result_and_error(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            parse_response('{"jsonrpc": "2.0", "id": 1}')
        self.assertIn("must have 'result' or 'error'", str(ctx.exception))


class TestValidateParams(unittest.TestCase):
    """Tests for validate_params() function."""

    def test_validate_dict_params(self):
        result = validate_params({'a': 1, 'b': 2}, AddParams)
        self.assertIsInstance(result, AddParams)
        self.assertEqual(result.a, 1)
        self.assertEqual(result.b, 2)

    def test_validate_list_params(self):
        result = validate_params([1, 2], AddParams)
        self.assertIsInstance(result, AddParams)
        self.assertEqual(result.a, 1)
        self.assertEqual(result.b, 2)

    def test_validate_none_params_type(self):
        result = validate_params(None, None)
        self.assertIsNone(result)

    def test_validate_none_params_type_with_empty_params(self):
        result = validate_params({}, None)
        self.assertIsNone(result)
        result = validate_params([], None)
        self.assertIsNone(result)

    def test_validate_none_params_type_with_params_error(self):
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'a': 1}, None)
        self.assertIn('accepts no parameters', str(ctx.exception))

    def test_validate_missing_required_param(self):
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'a': 1}, AddParams)
        self.assertIn("Missing required parameter: 'b'", str(ctx.exception))

    def test_validate_unknown_param(self):
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'a': 1, 'b': 2, 'c': 3}, AddParams)
        self.assertIn("Unknown parameter: 'c'", str(ctx.exception))

    def test_validate_wrong_type(self):
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'a': 'string', 'b': 2}, AddParams)
        self.assertIn("expected type 'int'", str(ctx.exception))
        self.assertIn("got 'str'", str(ctx.exception))

    def test_validate_too_many_positional(self):
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params([1, 2, 3], AddParams)
        self.assertIn('Too many positional parameters', str(ctx.exception))

    def test_validate_optional_params(self):
        result = validate_params({'required': 'value'}, OptionalParams)
        self.assertEqual(result.required, 'value')
        self.assertEqual(result.optional, 'default')

    def test_validate_nested_dataclass(self):
        result = validate_params(
            {'name': 'John', 'address': {'city': 'NYC', 'zip_code': '10001'}},
            NestedParams,
        )
        self.assertEqual(result.name, 'John')
        self.assertEqual(result.address.city, 'NYC')


class TestValidateResultType(unittest.TestCase):
    """Tests for validate_result_type() function."""

    def test_validate_int_result(self):
        # Should not raise
        validate_result_type(42, int)

    def test_validate_str_result(self):
        # Should not raise
        validate_result_type('hello', str)

    def test_validate_list_result(self):
        # Should not raise
        validate_result_type([1, 2, 3], list)

    def test_validate_typed_list_result(self):
        # Should not raise
        validate_result_type([1, 2, 3], list[int])

    def test_validate_dict_result(self):
        # Should not raise
        validate_result_type({'a': 1}, dict)

    def test_validate_dataclass_result_accepts_an_instance(self):
        # Should not raise: a method returning its declared dataclass is correct.
        validate_result_type(AddParams(a=1, b=2), AddParams)

    def test_validate_dataclass_result_rejects_a_bare_dict(self):
        """A dict is not the declared dataclass.

        The params direction accepts a dict here because the value is raw wire
        data about to be converted. A result is not: nothing converts it
        afterwards, so accepting any dict makes the check vacuous while a correct
        return value gets rejected.
        """
        with self.assertRaises(InvalidResultError) as ctx:
            validate_result_type({'a': 1, 'b': 2}, AddParams)
        self.assertIn("Expected return type 'AddParams', got 'dict'", str(ctx.exception))

    def test_validate_wrong_type_raises(self):
        with self.assertRaises(InvalidResultError) as ctx:
            validate_result_type('not an int', int)
        self.assertIn('Expected return type', str(ctx.exception))
        self.assertIn('int', str(ctx.exception))
        self.assertIn('str', str(ctx.exception))

    def test_validate_wrong_list_item_type_raises(self):
        with self.assertRaises(InvalidResultError):
            validate_result_type([1, 'two', 3], list[int])

    def test_validate_none_for_optional(self):
        # Should not raise
        validate_result_type(None, int | None)

    def test_validate_value_for_optional(self):
        # Should not raise
        validate_result_type(42, int | None)


class TestAResultDataclassHasItsFieldsChecked(unittest.TestCase):
    """isinstance() answers "is this a Row", which is not what the flag is named after.

    A dataclass enforces nothing at runtime - `Row(n='not a number')` is ordinary
    Python - so checking only the outer type let a string go out under a schema
    promising `number`, with `validate_results=True` set and reporting success.
    Only the top-level return value was ever checked; every field, and every
    dataclass inside a list or dict, was taken on trust.
    """

    def setUp(self):
        @dataclass
        class Row:
            id: int
            score: float

        @dataclass
        class Page:
            rows: list[Row]
            total: int

        self.Row, self.Page = Row, Page

    def test_a_wrong_field_is_reported_with_its_path(self):
        with self.assertRaises(InvalidResultError) as ctx:
            validate_result_type(self.Page(rows=[self.Row(id=1, score=1.0)], total='many'), self.Page)
        self.assertIn("at 'total'", str(ctx.exception))

    def test_a_wrong_field_inside_a_list_is_located(self):
        page = self.Page(rows=[self.Row(id=1, score=1.0), self.Row(id=2, score='high')], total=2)
        with self.assertRaises(InvalidResultError) as ctx:
            validate_result_type(page, self.Page)
        self.assertIn("at 'rows[1].score'", str(ctx.exception))

    def test_a_wrong_value_inside_a_dict_is_located(self):
        with self.assertRaises(InvalidResultError) as ctx:
            validate_result_type({'k': self.Row(id=1, score='x')}, dict[str, self.Row])
        self.assertIn('score', str(ctx.exception))

    def test_the_message_does_not_contradict_itself(self):
        """ "Expected return type 'Page', got 'Page'" is what M-04 was about.

        Now that the outer isinstance passes and a field is the problem, the old
        sentence would have said exactly that again.
        """
        with self.assertRaises(InvalidResultError) as ctx:
            validate_result_type(self.Page(rows=[], total='many'), self.Page)
        self.assertNotIn("'Page', got 'Page'", str(ctx.exception))

    def test_a_correct_result_still_passes(self):
        validate_result_type(self.Page(rows=[self.Row(id=1, score=1.5)], total=1), self.Page)

    def test_an_init_false_field_is_checked_too(self):
        """It is not a parameter, but it is serialized, so it is part of the result."""

        @dataclass
        class Derived:
            raw: str
            parsed: int = field(init=False)

            def __post_init__(self):
                self.parsed = self.raw  # wrong on purpose: str where int is declared

        with self.assertRaises(InvalidResultError) as ctx:
            validate_result_type(Derived(raw='7'), Derived)
        self.assertIn('parsed', str(ctx.exception))

    def test_an_out_of_range_field_is_named(self):
        with self.assertRaises(InvalidResultError) as ctx:
            validate_result_type(self.Row(id=1, score=int('1' * 400)), self.Row)
        self.assertIn('out of range', str(ctx.exception))
        self.assertIn('score', str(ctx.exception))

    def test_a_cyclic_result_is_bounded_rather_than_recursing_forever(self):
        """A result was built in this process, so nothing had bounded it before."""
        with self.assertRaises(InvalidResultError) as ctx:
            first = RecursiveComment(text='a')
            second = RecursiveComment(text='b', reply=first)
            first.reply = second
            validate_result_type(first, RecursiveComment)
        self.assertIn('nests deeper', str(ctx.exception))

    def test_an_unresolvable_annotation_does_not_fail_the_call(self):
        """A forward reference the author never resolved is not this request's problem.

        get_type_hints() raises NameError on it. Letting that out would turn a
        correct response into -32603 for a defect that has nothing to do with the
        value being returned.
        """

        @dataclass
        class Odd:
            n: int
            later: 'NeverDefined' = None  # noqa: F821 - unresolvable on purpose

        validate_result_type(Odd(n=1), Odd)  # must not raise
        validate_result_type(Odd(n='not a number'), Odd)  # nor may it claim to have checked

    def test_a_dataclass_with_a_metaclass_is_not_skipped(self):
        """The fast path in front of get_origin() must not exclude these."""
        import abc

        @dataclass
        class WithMeta(abc.ABC):
            n: int

        validate_result_type(WithMeta(n=1), WithMeta)
        with self.assertRaises(InvalidResultError):
            validate_result_type(WithMeta(n='not a number'), WithMeta)

    def test_it_only_runs_when_asked(self):
        """validate_results is off by default and this must not change that."""

        @dataclass
        class Result:
            n: int

        class Sloppy(Method):
            def execute(self, params: None) -> Result:
                return Result(n='not a number')

        off = JSONRPC()
        off.register('m', Sloppy())
        self.assertEqual(
            json.loads(off.handle('{"jsonrpc":"2.0","method":"m","id":1}'))['result'], {'n': 'not a number'}
        )

        on = JSONRPC(validate_results=True)
        on.register('m', Sloppy())
        self.assertEqual(json.loads(on.handle('{"jsonrpc":"2.0","method":"m","id":1}'))['error']['code'], -32001)


class TestValidateParamsEdgeCases(unittest.TestCase):
    """Test edge cases in parameter validation."""

    def test_validate_params_non_dataclass_type(self):
        """Test validate_params with non-dataclass type raises error."""
        with self.assertRaises(InvalidParamsError) as cm:
            validate_params({'value': 42}, int)
        self.assertIn('must be a dataclass', str(cm.exception))

    def test_validate_params_none_with_required_fields(self):
        """Test validate_params with None when required fields exist."""
        with self.assertRaises(InvalidParamsError) as cm:
            validate_params(None, AddParams)
        self.assertIn('Missing required parameters', str(cm.exception))

    def test_validate_params_wrong_type_not_dict_or_list(self):
        """Test validate_params with params not dict/list."""
        with self.assertRaises(InvalidParamsError) as cm:
            validate_params('wrong', AddParams)
        self.assertIn('must be object or array', str(cm.exception))


class TestTypeNameFunction(unittest.TestCase):
    """Test _type_name edge cases."""

    def test_type_name_union_multiple_types(self):
        """Test _type_name with Union of >2 types."""
        from typing import Union

        result = _type_name(Union[int, str, float])  # noqa: UP007
        # Should show all types separated by |
        self.assertIn('int', result)
        self.assertIn('str', result)
        self.assertIn('float', result)

    def test_type_name_dict_with_args(self):
        """Test _type_name with dict[str, int]."""
        result = _type_name(dict[str, int])
        self.assertEqual(result, 'dict[str, int]')

    def test_type_name_literal(self):
        """Test _type_name with Literal."""
        from typing import Literal

        result = _type_name(Literal['a', 'b', 'c'])
        self.assertIn('Literal', result)

    def test_type_name_optional(self):
        """Test _type_name with Optional (int | None)."""
        result = _type_name(int | None)
        self.assertIn('int', result)
        self.assertIn('None', result)

    def test_type_name_list_no_args(self):
        """Test _type_name with plain list (no type args)."""
        result = _type_name(list)
        self.assertEqual(result, 'list')

    def test_type_name_dict_no_args(self):
        """Test _type_name with plain dict (no type args)."""
        result = _type_name(dict)
        self.assertEqual(result, 'dict')

    def test_type_name_unknown_type_fallback(self):
        """Test _type_name falls back to str() for unknown types."""
        # Custom complex type that doesn't have __name__
        from collections.abc import Callable

        result = _type_name(Callable)
        # Should fall back to str(t)
        self.assertIsInstance(result, str)


class TestCheckTypeFunction(unittest.TestCase):
    """Test _check_type edge cases."""

    def test_check_type_none_for_non_optional(self):
        """Test _check_type with None for non-Optional type."""
        result = _check_type(None, int)
        self.assertFalse(result)

    def test_check_type_literal_valid(self):
        """Test _check_type with Literal - valid value."""
        from typing import Literal

        result = _check_type('a', Literal['a', 'b', 'c'])
        self.assertTrue(result)

    def test_check_type_literal_invalid(self):
        """Test _check_type with Literal - invalid value."""
        from typing import Literal

        result = _check_type('d', Literal['a', 'b', 'c'])
        self.assertFalse(result)

    def test_check_type_list_with_validation(self):
        """Test _check_type validates all list items."""
        result = _check_type([1, 2, 3], list[int])
        self.assertTrue(result)

        result = _check_type([1, '2', 3], list[int])
        self.assertFalse(result)

    def test_check_type_dict_with_validation(self):
        """Test _check_type validates dict keys and values."""
        result = _check_type({'a': 1, 'b': 2}, dict[str, int])
        self.assertTrue(result)

        result = _check_type({'a': 1, 'b': 'wrong'}, dict[str, int])
        self.assertFalse(result)

    def test_check_type_any(self):
        """Test _check_type with Any always returns True."""
        from typing import Any

        self.assertTrue(_check_type(42, Any))
        self.assertTrue(_check_type('str', Any))
        self.assertTrue(_check_type([1, 2], Any))
        self.assertTrue(_check_type({'key': 'value'}, Any))

    def test_check_type_float_accepts_int(self):
        """Test _check_type with float accepts int values."""
        self.assertTrue(_check_type(42, float))
        self.assertTrue(_check_type(42.5, float))

    def test_check_type_bool_not_confused_with_int(self):
        """Test _check_type with bool not confused with int."""
        self.assertTrue(_check_type(True, bool))
        self.assertFalse(_check_type(1, bool))
        self.assertFalse(_check_type(True, int))

    def test_check_type_list_wrong_value_type(self):
        """Test _check_type with value that's not a list for list[T]."""
        self.assertFalse(_check_type('not a list', list[int]))
        self.assertFalse(_check_type(42, list[str]))

    def test_check_type_dict_wrong_value_type(self):
        """Test _check_type with value that's not a dict for dict[K,V]."""
        self.assertFalse(_check_type('not a dict', dict[str, int]))
        self.assertFalse(_check_type([1, 2], dict[str, int]))

    def test_check_type_empty_list(self):
        """Test _check_type with empty list passes validation."""
        self.assertTrue(_check_type([], list[int]))
        self.assertTrue(_check_type([], list[AddParams]))

    def test_check_type_empty_dict(self):
        """Test _check_type with empty dict passes validation."""
        self.assertTrue(_check_type({}, dict[str, int]))
        self.assertTrue(_check_type({}, dict[str, AddParams]))

    def test_check_type_list_no_args(self):
        """Test _check_type with plain list (no type args)."""
        self.assertTrue(_check_type([1, 'mixed', None], list))
        self.assertTrue(_check_type([], list))

    def test_check_type_dict_no_args(self):
        """Test _check_type with plain dict (no type args)."""
        self.assertTrue(_check_type({'any': 1, 'values': 'ok'}, dict))
        self.assertTrue(_check_type({}, dict))

    def test_check_type_unknown_type_fallback(self):
        """Test _check_type fallback to isinstance for unknown types."""

        # Custom class without special handling
        class CustomClass:
            pass

        obj = CustomClass()
        # Should fall back to isinstance
        self.assertTrue(_check_type(obj, CustomClass))


class TestConvertValueFunction(unittest.TestCase):
    """The reference validator itself - the agreement test is only as good as it is."""

    def test_convert_value_none(self):
        """Test _convert_value with None returns None."""
        result = _convert_value(None, int | None)
        self.assertIsNone(result)

    def test_convert_value_list_dataclass(self):
        """Test _convert_value with list[Dataclass]."""
        data = [{'a': 1, 'b': 2}, {'a': 3, 'b': 4}]
        result = _convert_value(data, list[AddParams])

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], AddParams)
        self.assertEqual(result[0].a, 1)

    def test_convert_value_dict_dataclass(self):
        """Test _convert_value with dict[str, Dataclass]."""
        data = {'first': {'a': 1, 'b': 2}, 'second': {'a': 3, 'b': 4}}
        result = _convert_value(data, dict[str, AddParams])

        self.assertIsInstance(result, dict)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result['first'], AddParams)
        self.assertEqual(result['first'].a, 1)

    def test_convert_value_is_bounded_by_the_nesting_limit(self):
        value = 1
        for _ in range(MAX_NESTING_DEPTH + 10):
            value = [value]

        with self.assertRaises(InvalidParamsError) as ctx:
            _convert_value(value, list)
        self.assertIn('nesting depth', str(ctx.exception))

    def test_a_union_does_not_swallow_the_depth_guard(self):
        """A union frame is the direct parent of the recursion the guard bounds.

        Its handler catches InvalidParamsError to try the next variant, so a
        plain one would let the guard's own exception disarm the guard.
        """
        value = 1
        for _ in range(MAX_NESTING_DEPTH + 10):
            value = [value]

        with self.assertRaises(InvalidParamsError) as ctx:
            _convert_value(value, list | str)
        self.assertIn('nesting depth', str(ctx.exception))

    def test_convert_value_union_tries_types(self):
        """Test _convert_value with Union tries different types."""
        from typing import Union

        # Should try to convert with first non-None type
        result = _convert_value({'a': 1, 'b': 2}, Union[AddParams, str, None])  # noqa: UP007
        self.assertIsInstance(result, AddParams)

    def test_convert_value_empty_list(self):
        """Test _convert_value with empty list."""
        result = _convert_value([], list[AddParams])
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    def test_convert_value_empty_dict(self):
        """Test _convert_value with empty dict."""
        result = _convert_value({}, dict[str, AddParams])
        self.assertIsInstance(result, dict)
        self.assertEqual(len(result), 0)


class TestParseResponseEdgeCases(unittest.TestCase):
    """Test parse_response edge cases."""

    def test_parse_response_not_dict(self):
        """Test parse_response with non-dict raises error."""
        with self.assertRaises(InvalidRequestError) as cm:
            parse_response('["array"]')
        self.assertIn('must be object', str(cm.exception))

    def test_parse_response_invalid_jsonrpc_version(self):
        """Test parse_response with invalid jsonrpc version."""
        data = '{"jsonrpc":"1.0","result":42,"id":1}'
        with self.assertRaises(InvalidRequestError) as cm:
            parse_response(data)
        self.assertIn('Invalid jsonrpc version', str(cm.exception))

    def test_parse_response_error_code_not_integer(self):
        """Test parse_response with error.code not integer."""
        data = '{"jsonrpc":"2.0","error":{"code":"wrong","message":"error"},"id":1}'
        with self.assertRaises(InvalidRequestError) as cm:
            parse_response(data)
        self.assertIn("'code' must be integer", str(cm.exception))

    def test_parse_response_error_message_not_string(self):
        """Test parse_response with error.message not string."""
        data = '{"jsonrpc":"2.0","error":{"code":-32600,"message":123},"id":1}'
        with self.assertRaises(InvalidRequestError) as cm:
            parse_response(data)
        self.assertIn("'message' must be string", str(cm.exception))

    def test_parse_response_missing_result_and_error(self):
        """Test parse_response missing both result and error."""
        data = '{"jsonrpc":"2.0","id":1}'
        with self.assertRaises(InvalidRequestError) as cm:
            parse_response(data)
        self.assertIn("must have 'result' or 'error'", str(cm.exception))

    def test_parse_response_success_without_id(self):
        """Test parse_response with result but no id."""
        data = '{"jsonrpc":"2.0","result":42}'
        with self.assertRaises(InvalidRequestError) as cm:
            parse_response(data)
        self.assertIn("must have 'id'", str(cm.exception))


class TestServerErrorValidation(unittest.TestCase):
    """Test ServerError code validation."""

    def test_server_error_code_valid_range(self):
        """Test ServerError with valid code in range -32099 to -32000."""
        # Should not raise
        error1 = ServerError('Test', code=-32000)
        self.assertEqual(error1.error.code, -32000)

        error2 = ServerError('Test', code=-32099)
        self.assertEqual(error2.error.code, -32099)

        error3 = ServerError('Test', code=-32050)
        self.assertEqual(error3.error.code, -32050)

    def test_server_error_code_too_high(self):
        """Test ServerError with code > -32000 raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            ServerError('Test', code=-31999)
        self.assertIn('must be in range -32099 to -32000', str(cm.exception))

    def test_server_error_code_too_low(self):
        """Test ServerError with code < -32099 raises ValueError."""
        with self.assertRaises(ValueError) as cm:
            ServerError('Test', code=-32100)
        self.assertIn('must be in range -32099 to -32000', str(cm.exception))

    def test_server_error_code_way_out_of_range(self):
        """Test ServerError with code far outside range."""
        with self.assertRaises(ValueError):
            ServerError('Test', code=-40000)

        with self.assertRaises(ValueError):
            ServerError('Test', code=-1000)


class TestUnionTypeOrdering(unittest.TestCase):
    """Test Union type ordering edge cases - lines 31-32, 303-307."""

    def test_type_name_union_none_second_position(self):
        """Test _type_name with Union where None is in args[1] - line 31."""
        # This tests the else clause: args[1] is type(None)

        # Create None | int using types.UnionType (Python 3.10+)
        union_type = type(None) | int
        result = _type_name(union_type)
        self.assertIn('int', result)
        self.assertIn('None', result)

    def test_unwrap_optional_none_first_position(self):
        """Test _unwrap_optional with None | T - lines 303-307."""
        from jsonrpc.validation import _unwrap_optional

        # None | int should unwrap to int
        union_type = type(None) | int
        result = _unwrap_optional(union_type)
        self.assertEqual(result, int)

    def test_unwrap_optional_none_second_position(self):
        """Test _unwrap_optional with T | None - lines 303-307."""
        from jsonrpc.validation import _unwrap_optional

        # int | None should unwrap to int
        union_type = int | type(None)
        result = _unwrap_optional(union_type)
        self.assertEqual(result, int)


class TestAdvancedTypeEdgeCases(unittest.TestCase):
    """Test advanced type system edge cases for 100% coverage."""

    def test_check_type_isinstance_type_error(self):
        """Test _check_type raises InvalidParamsError when isinstance raises TypeError."""

        # Create a type that raises TypeError on isinstance check
        class BadType:
            def __instancecheck__(self, instance):
                raise TypeError('isinstance not supported')

        # The function should raise InvalidParamsError for unsupported types
        with self.assertRaises(InvalidParamsError) as ctx:
            _check_type('anything', BadType())
        self.assertIn('Unsupported type annotation', str(ctx.exception))

    def test_convert_value_union_fallback_on_post_init_error(self):
        """Union fallback: if first type's __post_init__ raises ValueError, try next type."""
        from dataclasses import dataclass

        @dataclass
        class TypeA:
            x: int

            def __post_init__(self):
                raise ValueError('TypeA always fails in __post_init__')

        @dataclass
        class TypeB:
            x: int

        # TypeA.__post_init__ raises ValueError → caught by except, falls through to TypeB
        result = _convert_value({'x': 42}, TypeA | TypeB)
        self.assertIsInstance(result, TypeB)
        self.assertEqual(result.x, 42)

    def test_convert_value_union_all_variants_fail_raises(self):
        """A union that matches no variant fails closed instead of passing the raw value.

        Returning the caller's own object here would give the method the dict
        json.loads() produced, while its annotation promises a validated
        dataclass: unknown keys, field types, required fields and Literal
        membership would all be skipped at once, with a normal success envelope
        on the wire.
        """
        from dataclasses import dataclass

        @dataclass
        class TypeA:
            x: int

            def __post_init__(self):
                raise ValueError('TypeA fails')

        @dataclass
        class TypeB:
            x: int

            def __post_init__(self):
                raise TypeError('TypeB fails')

        value = {'x': 42}
        with self.assertRaises(InvalidParamsError) as ctx:
            _convert_value(value, TypeA | TypeB)
        self.assertIn('does not match any variant', str(ctx.exception))

    def test_convert_value_union_dataclass_field_mismatch(self):
        """Union fallback: if first dataclass has wrong fields (InvalidParamsError), try next."""
        from dataclasses import dataclass

        @dataclass
        class TypeA:
            name: str
            age: int

        @dataclass
        class TypeB:
            x: int
            y: int

        # Value matches TypeB fields but not TypeA → should fall through to TypeB
        result = _convert_value({'x': 1, 'y': 2}, TypeA | TypeB)
        self.assertIsInstance(result, TypeB)
        self.assertEqual(result.x, 1)
        self.assertEqual(result.y, 2)

    def test_convert_value_plain_list_returns_as_is(self):
        """A plain `list` annotation has nothing to descend into."""
        value = [1, 'mixed', None]
        result = _convert_value(value, list)
        self.assertEqual(result, value)

    def test_convert_value_plain_dict_returns_as_is(self):
        """A plain `dict` annotation has nothing to descend into."""
        value = {'any': 1, 'values': 'ok'}
        result = _convert_value(value, dict)
        self.assertEqual(result, value)

    def test_method_init_subclass_non_typeerror_exception(self):
        """Test Method.__init_subclass__ wraps non-TypeError exceptions - lines 393-395."""
        from unittest.mock import patch

        from jsonrpc import Method

        # Mock get_type_hints to raise a non-TypeError
        with patch('jsonrpc.method.get_type_hints', side_effect=ValueError('test error')):
            with self.assertRaises(TypeError) as ctx:

                class BadMethod(Method):
                    name = 'bad'

                    def execute(self, params: None) -> str:
                        return 'ok'

            # Should wrap ValueError as TypeError
            self.assertIn('Failed to infer types', str(ctx.exception))
            self.assertIn('test error', str(ctx.exception))

    def test_dispatch_validate_result_with_none_result_type(self):
        """Test dispatcher skips validation when result_type is None - dispatcher.py:156."""
        from jsonrpc import JSONRPC, MethodGroup
        from tests.fixtures import NoResultTypeMethod

        rpc = JSONRPC()
        group = MethodGroup()
        group.register('no_result_type', NoResultTypeMethod())
        rpc.register('test', group)

        # Call through JSONRPC with validate_result=True but method has result_type=None
        # Should not raise validation error
        result = rpc.call_method('test.no_result_type', None, validate_result=True)
        self.assertIsNotNone(result)

    def test_parse_single_request_non_dict_raises_error(self):
        """Test _parse_single_request with non-dict input - request.py:47."""
        from jsonrpc.errors import InvalidRequestError
        from jsonrpc.request import _parse_single_request

        # Passing non-dict should raise InvalidRequestError
        with self.assertRaises(InvalidRequestError) as ctx:
            _parse_single_request('not a dict')
        self.assertIn('must be object', str(ctx.exception))

    def test_parse_response_error_non_dict(self):
        """Test parse_response with error field as non-dict - response.py:154."""
        from jsonrpc.errors import InvalidRequestError
        from jsonrpc.response import parse_response

        # Response with error that's not an object
        response_json = '{"jsonrpc": "2.0", "error": "not a dict", "id": 1}'
        with self.assertRaises(InvalidRequestError) as ctx:
            parse_response(response_json)
        self.assertIn('must be object', str(ctx.exception))


class TestParseRequestParamFlags(unittest.TestCase):
    """Tests for parse_request() allow_dict_params / allow_list_params flags."""

    def test_parse_request_dict_params_not_allowed(self):
        """Dict params rejected when allow_dict_params=False."""
        with self.assertRaises(InvalidParamsError) as ctx:
            parse_request({'method': 'add', 'params': {'a': 1, 'b': 2}, 'id': 1}, allow_dict_params=False)
        self.assertEqual(str(ctx.exception), 'Object params not allowed. Use array params: ["value1", "value2"]')

    def test_parse_request_list_params_not_allowed(self):
        """List params rejected when allow_list_params=False."""
        with self.assertRaises(InvalidParamsError) as ctx:
            parse_request(
                {'jsonrpc': '2.0', 'method': 'add', 'params': [1, 2], 'id': 1},
                allow_list_params=False,
            )
        self.assertEqual(
            str(ctx.exception),
            'Array params not allowed. Use object params: {"param1": "value1", "param2": "value2"}',
        )


class TestTypeNameTypingModule(unittest.TestCase):
    """Tests for _type_name() with typing.Union / typing.Optional types (lines 30-31, 37, 42)."""

    def test_type_name_typing_optional_covers_lines_30_31(self):
        """_type_name(Optional[int]) hits lines 30-31 (get_origin is typing.Union, not types.UnionType)."""
        import typing

        result = _type_name(typing.Optional[int])  # noqa: UP007, UP045
        self.assertEqual(result, 'int | None')

    def test_type_name_typing_union_none_first_covers_else_branch(self):
        """_type_name(Union[None, int]) — args[1] is int (not NoneType) → else branch of line 30."""
        import typing

        result = _type_name(typing.Union[None, int])  # noqa: UP007
        self.assertEqual(result, 'int | None')

    def test_type_name_typing_List_no_args_covers_line_37(self):
        """_type_name(typing.List) returns 'list' — hits line 37 (origin=list, args=(), no-args branch)."""
        import typing

        result = _type_name(typing.List)  # noqa: UP006, UP035
        self.assertEqual(result, 'list')

    def test_type_name_typing_Dict_no_args_covers_line_42(self):
        """_type_name(typing.Dict) returns 'dict' — hits line 42 (origin=dict, args=(), no-args branch)."""
        import typing

        result = _type_name(typing.Dict)  # noqa: UP006, UP035
        self.assertEqual(result, 'dict')


class TestCheckTypeTypingGenericContainers(unittest.TestCase):
    """Tests for _check_type() with unparameterized typing.List / typing.Dict (lines 84, 93)."""

    def test_check_type_typing_List_no_args_returns_true(self):
        """_check_type(list_value, typing.List) hits line 84 return True (origin=list, args=())."""
        import typing

        self.assertTrue(_check_type([1, 2, 3], typing.List))  # noqa: UP006, UP035
        self.assertTrue(_check_type([], typing.List))  # noqa: UP006, UP035

    def test_check_type_typing_Dict_no_args_returns_true(self):
        """_check_type(dict_value, typing.Dict) hits line 93 return True (origin=dict, args=())."""
        import typing

        self.assertTrue(_check_type({'a': 1}, typing.Dict))  # noqa: UP006, UP035
        self.assertTrue(_check_type({}, typing.Dict))  # noqa: UP006, UP035


class TestValidateParamsAllOptional(unittest.TestCase):
    """Tests for validate_params() when all fields have defaults and params=None (line 153)."""

    def test_validate_params_none_when_all_fields_optional_returns_default_instance(self):
        """validate_params(None, DC) where all fields have defaults returns DC() — hits line 153."""

        @dataclass
        class AllDefaultParams:
            x: int = 0
            y: str = 'default'
            z: float = 1.0

        result = validate_params(None, AllDefaultParams)
        self.assertIsInstance(result, AllDefaultParams)
        self.assertEqual(result.x, 0)
        self.assertEqual(result.y, 'default')
        self.assertEqual(result.z, 1.0)


class TestConvertValueTypingContainers(unittest.TestCase):
    """The reference validator with unparameterized typing.List / typing.Dict."""

    def test_convert_value_typing_List_returns_value_unchanged(self):
        """origin is list with no args: nothing to convert."""
        import typing

        value = [1, 2, 3]
        result = _convert_value(value, typing.List)  # noqa: UP006, UP035
        self.assertEqual(result, value)

    def test_convert_value_typing_Dict_returns_value_unchanged(self):
        """origin is dict with no args: nothing to convert."""
        import typing

        value = {'a': 1, 'b': 2}
        result = _convert_value(value, typing.Dict)  # noqa: UP006, UP035
        self.assertEqual(result, value)


class TestMethodGroupInitSubclassExceptionHandling(unittest.TestCase):
    """Tests for MethodGroup.__init_subclass__ exception wrapping (lines 448-449)."""

    def test_methodgroup_bad_forward_ref_wraps_error_as_type_error(self):
        """MethodGroup subclass with unresolvable forward ref in execute_method raises TypeError.

        get_type_hints() raises NameError for undefined 'NonExistentContextClass',
        caught by except Exception at line 448 and re-raised as TypeError at line 449.
        """
        from jsonrpc.method import MethodGroup

        with self.assertRaises(TypeError) as ctx:

            class BrokenGroup(MethodGroup):
                def execute_method(self, method, params, context: 'NonExistentContextClass') -> None:  # noqa: F821
                    pass

        self.assertEqual(
            str(ctx.exception)[:44],
            'Failed to infer context_type for BrokenGroup',
        )


class TestUnionParamsFailClosed(unittest.TestCase):
    """A payload matching no variant is refused, not passed through raw."""

    def setUp(self):
        self.received = []
        received = self.received

        class Send(Method):
            def execute(self, params: SendUnion) -> str:
                received.append(params)
                return 'sent'

        class SendStrictMethod(Method):
            def execute(self, params: SendStrict) -> str:
                received.append(params)
                return 'sent'

        class SendOptional(Method):
            def execute(self, params: OptionalRecipient) -> str:
                received.append(params)
                return 'sent'

        class SendMapped(Method):
            def execute(self, params: MappedRecipients) -> str:
                received.append(params)
                return 'sent'

        self.rpc = JSONRPC()
        self.rpc.register('send', Send())
        self.rpc.register('send_strict', SendStrictMethod())
        self.rpc.register('send_optional', SendOptional())
        self.rpc.register('send_mapped', SendMapped())

    def _call(self, method, params):
        return json.loads(self.rpc.handle(json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params, 'id': 1})))

    def test_a_list_of_union_variants_refuses_an_unmatched_payload(self):
        data = self._call('send', {'to': [{'kind': 'evil', 'admin': True}]})

        self.assertEqual(data['error']['code'], -32602)
        self.assertEqual(self.received, [])

    def test_the_same_payload_under_a_single_variant_is_also_refused(self):
        """The control: the two annotations must agree.

        A byte-identical execute() body differing only in its annotation used to
        reject the payload under list[Recipient] and accept it under
        list[Recipient | Card].
        """
        data = self._call('send_strict', {'to': [{'kind': 'evil', 'admin': True}]})
        self.assertEqual(data['error']['code'], -32602)

    def test_an_optional_dataclass_field_refuses_an_unmatched_payload(self):
        data = self._call('send_optional', {'to': {'kind': 'user', 'nope': 1}})
        self.assertEqual(data['error']['code'], -32602)
        self.assertEqual(self.received, [])

    def test_a_dict_of_union_variants_refuses_an_unmatched_payload(self):
        data = self._call('send_mapped', {'by_name': {'a': {'kind': 'evil'}}})
        self.assertEqual(data['error']['code'], -32602)

    def test_a_matching_variant_still_converts_to_the_dataclass(self):
        data = self._call('send', {'to': [{'kind': 'card', 'token': 'tok'}]})

        self.assertEqual(data['result'], 'sent')
        self.assertEqual(len(self.received), 1)
        self.assertIsInstance(self.received[0].to[0], Card)

    def test_each_variant_is_tried_in_turn(self):
        data = self._call(
            'send',
            {'to': [{'kind': 'user', 'user_id': 1}, {'kind': 'card', 'token': 'tok'}]},
        )

        self.assertEqual(data['result'], 'sent')
        kinds = [type(item).__name__ for item in self.received[0].to]
        self.assertEqual(kinds, ['Recipient', 'Card'])

    def test_an_explicit_null_is_still_accepted_for_an_optional_field(self):
        data = self._call('send_optional', {'to': None})
        self.assertEqual(data['result'], 'sent')

    def test_scalar_unions_are_unaffected(self):
        @dataclass
        class ScalarUnion:
            value: int | str

        result = validate_params({'value': 'text'}, ScalarUnion)
        self.assertEqual(result.value, 'text')
        result = validate_params({'value': 5}, ScalarUnion)
        self.assertEqual(result.value, 5)


class TestNestingDepth(unittest.TestCase):
    """The depth bound is a property of the recursion, not of the spelling."""

    def _nest(self, depth, key='child'):
        node: dict[str, Any] = {'name': 'leaf'}
        for i in range(depth):
            node = {'name': f'n{i}', key: node}
        return node

    def test_a_plain_optional_chain_is_bounded(self):
        """`Node | None` is the canonical optional spelling.

        The depth guard raises InvalidParamsError, which the union handler used
        to catch as "this variant did not match" - so the frame directly above
        the recursion swallowed the guard protecting it, and the method received
        sixty-four constructed nodes followed by a raw dict tail.
        """
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params(self._nest(MAX_NESTING_DEPTH + 5), OptionalNode)
        self.assertIn('nesting depth', str(ctx.exception))

    def test_a_union_spelling_is_bounded(self):
        with self.assertRaises(InvalidParamsError):
            validate_params(self._nest(MAX_NESTING_DEPTH + 5), UnionNode)

    def test_a_list_chain_is_bounded(self):
        node: dict[str, Any] = {'name': 'leaf'}
        for i in range(MAX_NESTING_DEPTH + 5):
            node = {'name': f'n{i}', 'children': [node]}

        with self.assertRaises(InvalidParamsError):
            validate_params(node, ListNode)

    def test_a_dict_chain_is_bounded(self):
        node: dict[str, Any] = {'name': 'leaf'}
        for i in range(MAX_NESTING_DEPTH + 5):
            node = {'name': f'n{i}', 'children': {'k': node}}

        with self.assertRaises(InvalidParamsError):
            validate_params(node, DictNode)

    def test_the_method_never_receives_a_raw_dict_tail(self):
        """The end-to-end shape: a deep payload must not reach execute() at all."""
        walked = []

        class Walk(Method):
            def execute(self, params: OptionalNode) -> int:
                walked.append(params)
                return 0

        rpc = JSONRPC()
        rpc.register('walk', Walk())
        request = json.dumps({'jsonrpc': '2.0', 'method': 'walk', 'params': self._nest(200), 'id': 1})

        data = json.loads(rpc.handle(request))

        self.assertEqual(data['error']['code'], -32602)
        self.assertEqual(walked, [])

    def test_structures_within_the_bound_still_validate(self):
        result = validate_params(self._nest(5), OptionalNode)
        depth = 0
        current: Any = result
        while current is not None:
            self.assertIsInstance(current, OptionalNode)
            depth += 1
            current = current.child
        self.assertEqual(depth, 6)


class TestLiteralIsTypeStrict(unittest.TestCase):
    """True == 1 in Python; Literal membership must not rely on that."""

    def test_a_bool_does_not_satisfy_a_literal_of_ints(self):
        @dataclass
        class Level:
            level: Literal[1, 2, 3]

        with self.assertRaises(InvalidParamsError):
            validate_params({'level': True}, Level)

    def test_an_int_does_not_satisfy_a_literal_of_bools(self):
        @dataclass
        class Flag:
            flag: Literal[True]

        with self.assertRaises(InvalidParamsError):
            validate_params({'flag': 1}, Flag)

    def test_declared_literal_values_are_accepted(self):
        @dataclass
        class Level:
            level: Literal[1, 2, 3]

        self.assertEqual(validate_params({'level': 2}, Level).level, 2)

    def test_string_literals_are_unaffected(self):
        @dataclass
        class Mode:
            mode: Literal['fast', 'slow']

        self.assertEqual(validate_params({'mode': 'fast'}, Mode).mode, 'fast')
        with self.assertRaises(InvalidParamsError):
            validate_params({'mode': 'other'}, Mode)


class TestParamsNamespace(unittest.TestCase):
    """Only real dataclass fields are parameters."""

    def test_a_classvar_is_not_a_parameter(self):
        @dataclass
        class WithClassVar:
            x: int
            CONST: ClassVar[int] = 5

        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'x': 1, 'CONST': 9}, WithClassVar)
        self.assertIn("Unknown parameter: 'CONST'", str(ctx.exception))

    def test_the_kw_only_sentinel_is_not_a_parameter(self):
        @dataclass
        class WithKwOnly:
            _: KW_ONLY
            alpha: int

        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'_': 1, 'alpha': 2}, WithKwOnly)
        self.assertIn("Unknown parameter: '_'", str(ctx.exception))

    def test_a_kw_only_dataclass_still_validates_its_real_fields(self):
        @dataclass
        class WithKwOnly:
            _: KW_ONLY
            alpha: int

        self.assertEqual(validate_params({'alpha': 2}, WithKwOnly).alpha, 2)


class TestErrorMessagesDoNotLeak(unittest.TestCase):
    def test_a_bool_is_not_a_float(self):
        """bool is a subclass of int, so a float field would accept `true` unguarded."""
        self.assertFalse(_check_type(True, float))
        self.assertFalse(_check_type(False, float))
        self.assertTrue(_check_type(1, float))
        self.assertTrue(_check_type(1.5, float))

    def test_type_names_render_typing_constructs_without_an_address(self):
        from typing import ClassVar, Final

        self.assertEqual(_type_name(ClassVar[int]), 'ClassVar')
        self.assertEqual(_type_name(Final[int]), 'Final')

    def test_an_annotation_with_its_own_repr_keeps_it(self):
        """The address guard must not swallow a deliberate, readable repr.

        Annotations that are objects rather than classes reach the fallback; the
        rule is to drop only Python's default repr, which embeds an address.
        """

        class Sentinel:
            def __repr__(self):
                return 'MY_SENTINEL'

        self.assertEqual(_type_name(Sentinel()), 'MY_SENTINEL')

    def test_type_names_never_carry_an_object_address(self):
        """_type_name() fell back to str(annotation).

        For an annotation that is an object rather than a class, that is the
        default repr, which embeds the object's address - and this string is
        sent to the caller in a -32602 message.
        """
        rendered = _type_name(KW_ONLY)
        self.assertNotIn('0x', rendered)
        self.assertNotIn('object at', rendered)

    def test_union_type_names_are_still_readable(self):
        self.assertEqual(_type_name(int | str), 'int | str')
        self.assertEqual(_type_name(int | None), 'int | None')
        self.assertEqual(_type_name(list[int]), 'list[int]')

    def test_a_no_params_method_does_not_echo_the_payload(self):
        rpc = JSONRPC()

        class NoParams(Method):
            def execute(self, params: None) -> str:
                return 'pong'

        rpc.register('ping', NoParams())
        data = json.loads(rpc.handle('{"jsonrpc":"2.0","method":"ping","params":{"secret":"swordfish"},"id":1}'))

        self.assertEqual(data['error']['code'], -32602)
        self.assertNotIn('swordfish', json.dumps(data))


class TestResultValidation(unittest.TestCase):
    """The result direction needs its own dataclass rule."""

    def setUp(self):
        @dataclass
        class Report:
            title: str

        class Correct(Method):
            def execute(self, params: None) -> Report:
                return Report(title='ok')

        class ReturnsDict(Method):
            def execute(self, params: None) -> Report:
                return {'anything': 'goes'}

        self.Report = Report
        self.rpc = JSONRPC(validate_results=True)
        self.rpc.register('correct', Correct())
        self.rpc.register('returns_dict', ReturnsDict())

    def test_a_correct_dataclass_result_is_accepted(self):
        """Under validate_results=True this used to fail on every call, with the
        self-contradicting message "Expected return type 'Report', got 'Report'"."""
        data = json.loads(self.rpc.handle('{"jsonrpc":"2.0","method":"correct","id":1}'))
        self.assertEqual(data['result'], {'title': 'ok'})

    def test_an_unrelated_dict_result_is_refused(self):
        data = json.loads(self.rpc.handle('{"jsonrpc":"2.0","method":"returns_dict","id":1}'))
        self.assertEqual(data['error']['code'], -32001)

    def test_a_list_of_dataclass_results_is_checked_elementwise(self):
        @dataclass
        class Item:
            name: str

        validate_result_type([Item(name='a')], list[Item])
        with self.assertRaises(InvalidResultError):
            validate_result_type([{'name': 'a'}], list[Item])

    def test_an_optional_dataclass_result_accepts_none(self):
        validate_result_type(None, self.Report | None)
        validate_result_type(self.Report(title='x'), self.Report | None)

    def test_an_unsupported_return_annotation_is_reported_as_a_result_error(self):
        """A return type the checker cannot handle is the server's problem.

        The type machinery is shared with the params direction, which reports an
        unusable annotation as -32602 - blaming the caller's params for the
        server's own return annotation.
        """
        with self.assertRaises(InvalidResultError) as ctx:
            validate_result_type((1, 2), tuple[int, ...])
        self.assertIn('Cannot validate return type', str(ctx.exception))

    def test_params_direction_still_accepts_a_dict_for_a_dataclass(self):
        """The two directions genuinely differ; this is not symmetry."""

        @dataclass
        class Inner:
            name: str

        @dataclass
        class Outer:
            inner: Inner

        result = validate_params({'inner': {'name': 'x'}}, Outer)
        self.assertIsInstance(result.inner, Inner)


class TestMaxNestingDepth(unittest.TestCase):
    """Tests for MAX_NESTING_DEPTH recursion protection in validate_params."""

    def test_max_nesting_depth_constant_is_64(self):
        """MAX_NESTING_DEPTH is set to 64."""
        from jsonrpc.validation import MAX_NESTING_DEPTH

        self.assertEqual(MAX_NESTING_DEPTH, 64)

    def test_exceeding_nesting_depth_raises_invalid_params_error(self):
        """validate_params with _depth > MAX_NESTING_DEPTH raises InvalidParamsError."""
        from dataclasses import dataclass

        from jsonrpc.validation import MAX_NESTING_DEPTH, validate_params

        @dataclass
        class SimpleParams:
            x: int

        with self.assertRaises(InvalidParamsError) as ctx:
            # Pass _depth already beyond the limit to trigger the guard directly.
            validate_params({'x': 1}, SimpleParams, _depth=MAX_NESTING_DEPTH + 1)
        self.assertIn('Maximum nesting depth', str(ctx.exception))
        self.assertIn(str(MAX_NESTING_DEPTH), str(ctx.exception))

    def test_nesting_at_limit_is_accepted(self):
        """validate_params with _depth == MAX_NESTING_DEPTH does not raise."""
        from dataclasses import dataclass

        from jsonrpc.validation import MAX_NESTING_DEPTH, validate_params

        @dataclass
        class SimpleParams:
            x: int

        # Exactly at the limit — should succeed.
        result = validate_params({'x': 42}, SimpleParams, _depth=MAX_NESTING_DEPTH)
        self.assertEqual(result.x, 42)


class TestPostInitValidation(unittest.TestCase):
    """A params dataclass validating itself is ordinary Python; -32602 is the answer.

    Letting the rejection reach the generic handler turned a client's bad input
    into -32603 plus a traceback per request - the library reporting its own
    failure for the caller's mistake.

    Which exception you raise decides whether your text travels. That split
    exists because the ValueError raised here is usually not one anybody wrote:
    `datetime.fromisoformat`, `int`, `Decimal`, `UUID` and `json.loads` all embed
    the string they were handed in the message they raise - and that string came
    from the caller, through the channel this library documents as the way to
    validate.
    """

    def _method(self, params_cls):
        class M(Method):
            def execute(self, params: params_cls) -> int:  # type: ignore[valid-type]
                return 1

        rpc = JSONRPC()
        rpc.register('m', M())
        return rpc

    def _call(self, rpc, params):
        return json.loads(rpc.handle(json.dumps({'jsonrpc': '2.0', 'method': 'm', 'params': params, 'id': 1})))

    def test_value_error_becomes_invalid_params(self):
        @dataclass
        class P:
            age: int

            def __post_init__(self):
                if self.age < 0:
                    raise ValueError('age must be positive')

        rpc = self._method(P)
        data = self._call(rpc, {'age': -1})
        self.assertEqual(data['error']['code'], -32602)
        self.assertEqual(data['error']['data']['reason'], 'rejected_by_validator')
        self.assertEqual(self._call(rpc, {'age': 1})['result'], 1)

    def test_a_foreign_value_error_does_not_put_its_text_on_the_wire(self):
        """It is the caller's own string that comes back inside it."""
        from datetime import datetime

        @dataclass
        class P:
            when: str

            def __post_init__(self):
                self.parsed = datetime.fromisoformat(self.when)

        secret = '<script>alert(1)</script>'
        data = self._call(self._method(P), {'when': secret})
        self.assertEqual(data['error']['code'], -32602)
        self.assertNotIn(secret, json.dumps(data))
        self.assertNotIn('isoformat', data['error']['message'])

    def test_an_arithmetic_error_is_the_callers_fault_too(self):
        """Decimal('x') raises InvalidOperation and fromtimestamp raises OverflowError.

        Neither is a ValueError, so both used to reach the generic handler:
        -32603 with a traceback logged at ERROR, once per request, on the very
        conversion the library tells people to write.
        """
        import decimal

        @dataclass
        class P:
            amount: str

            def __post_init__(self):
                self.parsed = decimal.Decimal(self.amount)

        data = self._call(self._method(P), {'amount': 'not-a-number'})
        self.assertEqual(data['error']['code'], -32602)
        self.assertEqual(data['error']['data']['reason'], 'rejected_by_validator')

    def test_the_message_reaches_the_caller_verbatim(self):
        """The whole point of writing your own is that someone reads it."""

        @dataclass
        class P:
            age: int

            def __post_init__(self):
                raise InvalidParamsError('must be between 18 and 120')

        data = self._call(self._method(P), {'age': 5})
        self.assertEqual(data['error']['message'], 'must be between 18 and 120')

    def test_invalid_params_error_is_not_double_wrapped(self):
        @dataclass
        class P:
            age: int

            def __post_init__(self):
                raise InvalidParamsError('age must be positive')

        data = self._call(self._method(P), {'age': -1})
        self.assertEqual(data['error']['message'], 'age must be positive')

    def test_your_own_data_payload_survives(self):
        @dataclass
        class P:
            age: int

            def __post_init__(self):
                raise InvalidParamsError('age must be positive', data={'reason': 'age_range', 'min': 0})

        data = self._call(self._method(P), {'age': -1})
        self.assertEqual(data['error']['data']['reason'], 'age_range')
        self.assertEqual(data['error']['data']['min'], 0)

    def test_assertion_error_stays_internal(self):
        """`assert` disappears under -O, so -32602 would advertise a check that
        is not running in production."""

        @dataclass
        class P:
            age: int

            def __post_init__(self):
                assert self.age >= 0, 'nope'

        data = self._call(self._method(P), {'age': -1})
        self.assertEqual(data['error']['code'], -32603)

    def test_type_error_stays_internal(self):
        """At this call TypeError is how a mis-built params dataclass announces
        itself; mapping it would blame the caller for the server."""

        @dataclass
        class P:
            age: int

            def __post_init__(self):
                raise TypeError('boom')

        data = self._call(self._method(P), {'age': 1})
        self.assertEqual(data['error']['code'], -32603)

    def test_a_frozen_dataclass_behaves_the_same(self):
        @dataclass(frozen=True)
        class P:
            age: int

            def __post_init__(self):
                if self.age < 0:
                    raise ValueError('age must be positive')

        self.assertEqual(self._call(self._method(P), {'age': -1})['error']['code'], -32602)

    def test_a_nested_dataclass_reports_the_same_way(self):
        @dataclass
        class Inner:
            age: int

            def __post_init__(self):
                if self.age < 0:
                    raise ValueError('age must be positive')

        @dataclass
        class Outer:
            user: Inner

        data = self._call(self._method(Outer), {'user': {'age': -1}})
        self.assertEqual(data['error']['code'], -32602)


class TestUnionReportsWhyEveryVariantFailed(unittest.TestCase):
    def setUp(self):
        @dataclass
        class Cat:
            kind: str

            def __post_init__(self):
                if self.kind != 'cat':
                    raise InvalidParamsError('not a cat')

        @dataclass
        class Dog:
            kind: str

            def __post_init__(self):
                if self.kind != 'dog':
                    raise InvalidParamsError('not a dog')

        self.Cat, self.Dog = Cat, Dog

    def test_a_matching_variant_is_still_chosen(self):
        """Swallowing the failures is how the right variant gets picked - the
        defect was only that the reasons vanished when none matched."""
        result = _convert_value({'kind': 'dog'}, self.Cat | self.Dog)
        self.assertIsInstance(result, self.Dog)

    def test_the_reasons_are_reported_when_none_match(self):
        with self.assertRaises(InvalidParamsError) as ctx:
            _convert_value({'kind': 'fish'}, self.Cat | self.Dog)

        message = str(ctx.exception)
        self.assertIn('does not match any variant', message)
        self.assertIn('not a cat', message)
        self.assertIn('not a dog', message)

    def test_the_message_is_truncated_for_wide_unions(self):
        @dataclass
        class A:
            k: int

            def __post_init__(self):
                raise ValueError('nope')

        @dataclass
        class B:
            k: int

            def __post_init__(self):
                raise ValueError('nope')

        @dataclass
        class C:
            k: int

            def __post_init__(self):
                raise ValueError('nope')

        @dataclass
        class D:
            k: int

            def __post_init__(self):
                raise ValueError('nope')

        @dataclass
        class E:
            k: int

            def __post_init__(self):
                raise ValueError('nope')

        with self.assertRaises(InvalidParamsError) as ctx:
            _convert_value({'k': 1}, A | B | C | D | E)
        self.assertIn('and 2 more', str(ctx.exception))


class TestNestingBoundHoldsForEveryAnnotation(unittest.TestCase):
    """The bound has to be a property of the payload, not of the field's spelling.

    A field typed `list` or `Any` has no type arguments, so the annotation-driven
    walk had nothing to descend into and never counted a level - any depth got
    through.
    """

    def _nest(self, depth):
        value = 1
        for _ in range(depth):
            value = [value]
        return value

    def test_a_bare_list_annotation_is_bounded(self):
        @dataclass
        class P:
            v: list

        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'v': self._nest(300)}, P)
        self.assertIn('nesting depth', str(ctx.exception))

    def test_an_any_annotation_is_bounded(self):
        @dataclass
        class P:
            v: Any

        with self.assertRaises(InvalidParamsError):
            validate_params({'v': self._nest(300)}, P)

    def test_a_bare_dict_annotation_is_bounded(self):
        @dataclass
        class P:
            v: dict

        payload = {'k': 1}
        for _ in range(300):
            payload = {'k': payload}

        with self.assertRaises(InvalidParamsError):
            validate_params({'v': payload}, P)

    def test_shallow_payloads_still_pass_for_all_of_them(self):
        @dataclass
        class PList:
            v: list

        @dataclass
        class PAny:
            v: Any

        @dataclass
        class PDict:
            v: dict

        self.assertEqual(validate_params({'v': self._nest(10)}, PList).v, self._nest(10))
        self.assertEqual(validate_params({'v': self._nest(10)}, PAny).v, self._nest(10))
        self.assertEqual(validate_params({'v': {'k': {'k': 1}}}, PDict).v, {'k': {'k': 1}})


class TestIntIsConvertedForFloatFields(unittest.TestCase):
    def test_an_int_arrives_as_a_float(self):
        """mypy reads the annotation as float, so the method must get one.
        `int.is_integer()` only exists from 3.12, so on 3.11 the obvious call on
        a "float" would raise AttributeError."""

        @dataclass
        class P:
            amount: float

        result = validate_params({'amount': 10}, P)
        self.assertIs(type(result.amount), float)
        self.assertEqual(result.amount, 10.0)

    def test_a_float_is_untouched(self):
        @dataclass
        class P:
            amount: float

        self.assertEqual(validate_params({'amount': 10.5}, P).amount, 10.5)

    def test_a_bool_is_still_refused(self):
        @dataclass
        class P:
            amount: float

        with self.assertRaises(InvalidParamsError):
            validate_params({'amount': True}, P)

    def test_a_union_still_prefers_the_int_variant(self):
        @dataclass
        class P:
            x: int | float

        self.assertIs(type(validate_params({'x': 10}, P).x), int)


class TestAnIntTooLargeForFloatIsTheCallersMistake(unittest.TestCase):
    """`float(10**400)` raises OverflowError, and nothing caught it.

    JSON has one number type, so a 400-digit integer literal is a legal thing to
    send for a `float` field. OverflowError is an ArithmeticError, which is
    neither _TypeMismatch nor InvalidParamsError, so it went straight past
    validate_params() and dispatch() to the generic handler: `-32603 Internal
    error` for the caller and a full traceback logged at ERROR for the operator,
    once per request, from a 420-byte unauthenticated body.

    The CHANGELOG claimed values overflowing to Infinity were rejected. That was
    true only of *float* literals - `1e400` parses to `inf` and the isfinite
    check caught it. The integer spelling took the other branch.
    """

    HUGE = int('1' * 400)

    def _params(self, annotation):
        return dataclass(type('P', (), {'__annotations__': {'v': annotation}}))

    def test_it_is_invalid_params_not_an_internal_error(self):
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'v': self.HUGE}, self._params(float))

        self.assertEqual(ctx.exception.data['reason'], 'out_of_range')
        self.assertIn('out of range', str(ctx.exception))

    def test_the_message_does_not_claim_the_type_is_wrong(self):
        """ "expected type 'float', got 'int'" says the opposite of the truth.

        An int is exactly what this field accepts - that is the whole point of
        the conversion. Only this one is out of range.
        """
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'v': self.HUGE}, self._params(float))
        self.assertNotIn("got 'int'", str(ctx.exception))

    def test_a_non_finite_float_says_so_rather_than_contradicting_itself(self):
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'v': float('inf')}, self._params(float))

        self.assertEqual(ctx.exception.data['reason'], 'not_finite')
        self.assertEqual(str(ctx.exception), "Parameter 'v' must be a finite number")

    def test_every_place_a_float_can_appear(self):
        """One root cause, nine reachable shapes - the fix has to cover all of them."""
        cases = [
            (float, self.HUGE),
            (float | None, self.HUGE),
            (float | str, self.HUGE),
            (list[float], [self.HUGE]),
            (list[list[float]], [[self.HUGE]]),
            (dict[str, float], {'k': self.HUGE}),
            (float, -self.HUGE),
        ]
        for annotation, value in cases:
            with self.subTest(annotation=annotation):
                with self.assertRaises(InvalidParamsError):
                    validate_params({'v': value}, self._params(annotation))

    def test_it_travels_as_minus_32602_over_the_wire(self):
        @dataclass
        class Amount:
            value: float

        @dataclass
        class Receipt:
            ok: bool

        class Pay(Method):
            def execute(self, params: Amount) -> Receipt:
                return Receipt(ok=True)

        rpc = JSONRPC()
        rpc.register('pay', Pay())
        body = '{"jsonrpc":"2.0","method":"pay","params":{"value":' + '1' * 400 + '},"id":1}'

        error = json.loads(rpc.handle(body))['error']
        self.assertEqual(error['code'], -32602)
        self.assertNotEqual(error['message'], 'Internal error')

    def test_a_value_that_does_fit_is_unaffected(self):
        for value in (0, 1, -1, 10**300, -(10**300)):
            with self.subTest(value=value):
                self.assertEqual(validate_params({'v': value}, self._params(float)).v, float(value))

    def test_the_result_side_refuses_it_too(self):
        """It used to pass validation and go out as a 400-digit integer.

        The generated schema promises `number` for a method declared `-> float`,
        so the response contradicted the document describing it.
        """
        self.assertFalse(_check_type(self.HUGE, float))
        self.assertTrue(_check_type(10**300, float))

        with self.assertRaises(InvalidResultError) as ctx:
            validate_result_type(self.HUGE, float)
        self.assertIn('out of range', str(ctx.exception))

        with self.assertRaises(InvalidResultError) as ctx:
            validate_result_type(float('inf'), float)
        self.assertIn('finite', str(ctx.exception))


class TestNonSettableFieldsAreNotParameters(unittest.TestCase):
    """init=False fields belong to the dataclass, not to the caller.

    Counting them broke the method three ways at once: the caller could not
    supply one, positional params bound to the wrong fields, and one without a
    default was demanded but impossible to satisfy.
    """

    def test_an_init_false_field_is_not_accepted_from_the_wire(self):
        @dataclass
        class P:
            a: int
            computed: int = field(init=False, default=0)

        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'a': 1, 'computed': 9}, P)
        self.assertIn("Unknown parameter: 'computed'", str(ctx.exception))

    def test_positional_params_skip_it(self):
        @dataclass
        class P:
            a: int
            computed: int = field(init=False, default=0)
            b: int = 0

        result = validate_params([1, 2], P)
        self.assertEqual((result.a, result.b), (1, 2))

    def test_it_is_not_required_even_without_a_default(self):
        @dataclass
        class P:
            a: int
            computed: int = field(init=False)

            def __post_init__(self):
                self.computed = self.a * 2

        self.assertEqual(validate_params({'a': 1}, P).computed, 2)


class TestInitVarIsRefusedAtDefinition(unittest.TestCase):
    def test_a_params_dataclass_with_an_initvar_cannot_be_declared(self):
        """An InitVar is invisible to fields(), so the caller is told it is an
        unknown parameter while __init__ demands it - the method is uncallable
        by any route, and every request answered -32603."""
        from dataclasses import InitVar

        @dataclass
        class P:
            a: int
            seed: InitVar[int]

            def __post_init__(self, seed):
                pass

        with self.assertRaises(TypeError) as ctx:

            class M(Method):
                def execute(self, params: P) -> int:
                    return 1

        self.assertIn('InitVar', str(ctx.exception))

    def test_a_params_dataclass_without_one_is_unaffected(self):
        @dataclass
        class P:
            a: int

        class M(Method):
            def execute(self, params: P) -> int:
                return params.a

        self.assertIs(M.params_type, P)


class TestErrorAttributesReportTheActualError(unittest.TestCase):
    """`e.code` used to read back the class default, not this error.

    The obvious handler - `log.error('%d %s', e.code, e.message)` - logged
    -32000 'Server error' for an error raised as ServerError('boom', code=-32050).
    """

    def test_code_and_message_are_the_ones_passed_in(self):
        e = ServerError('boom', code=-32050)
        self.assertEqual(e.code, -32050)
        self.assertEqual(e.message, 'boom')

    def test_the_class_defaults_still_apply_when_nothing_is_passed(self):
        self.assertEqual(ServerError().code, -32000)
        self.assertEqual(ServerError().message, 'Server error')
        self.assertEqual(InvalidParamsError('bad').code, -32602)

    def test_the_wire_form_is_unchanged(self):
        self.assertEqual(ServerError('boom', code=-32050).to_dict(), {'code': -32050, 'message': 'boom'})


class TestResultConversionKeepsItsShape(unittest.TestCase):
    """The hand-rolled walk that replaced asdict() must agree with it."""

    def test_a_dataclass_inside_a_list_inside_a_dict_unrolls(self):
        @dataclass
        class Leaf:
            v: int

        value = {'items': [Leaf(v=1), Leaf(v=2)], 'nested': {'one': Leaf(v=3)}}
        self.assertEqual(
            _dataclass_to_dict(value),
            {'items': [{'v': 1}, {'v': 2}], 'nested': {'one': {'v': 3}}},
        )

    def test_tuples_become_lists(self):
        @dataclass
        class Leaf:
            v: int

        self.assertEqual(_dataclass_to_dict((Leaf(v=1), Leaf(v=2))), [{'v': 1}, {'v': 2}])

    def test_non_dataclass_leaves_pass_through(self):
        leaves = {'a': 1, 'b': 'x', 'c': None, 'd': True}
        self.assertEqual(_dataclass_to_dict(leaves), leaves)


# --------------------------------------------------------------------------
# The two validators must agree
# --------------------------------------------------------------------------


@dataclass
class AgreementLeaf:
    x: int
    label: str = 'l'

    def __post_init__(self):
        if self.x == 13:
            raise ValueError('x must not be 13')


@dataclass
class AgreementCat:
    kind: Literal['cat']
    lives: int = 9


@dataclass
class AgreementDog:
    kind: Literal['dog']
    good: bool = True


class TestTheTwoValidatorsAgree(unittest.TestCase):
    """`_coerce` and `_check_type`/`_convert_value` must accept the same things.

    Params validation runs as a single descent (`_coerce`). Half of the older
    pair is still live in the library - `validate_result_type` calls
    `_check_type`, and so does the replay that decides which of several faults a
    caller hears about. The other half, `_convert_value`, is the reference
    implementation at the top of this file: the code that shipped for three
    releases and defines what the merged walk is allowed to do.

    So the same rules exist twice, and the danger is drift - a rule tightened in
    one and not the other. A caller would see a request refused by the fast path
    and then found blameless by the replay, or the reverse.

    This walks every annotation against every value in the pool - exhaustive
    rather than random, so a failure reproduces exactly. It has already caught
    two real disagreements: the verdict on a scalar union, and `Any` with null.
    """

    ANNOTATIONS = [
        int,
        str,
        bool,
        float,
        Any,
        list,
        dict,
        list[int],
        list[str],
        dict[str, int],
        AgreementLeaf,
        list[AgreementLeaf],
        AgreementCat | AgreementDog,
        str | None,
        int | float,
        Literal['a', 'b'],
        Literal[1, 2],
    ]

    VALUES = [
        None,
        0,
        1,
        13,
        -1,
        True,
        False,
        1.5,
        float('inf'),
        int('1' * 400),  # too large for a float; both sides must refuse it alike
        '',
        'x',
        'a',
        'cat',
        [],
        [1],
        [1, 2],
        ['x'],
        [1, 'x'],
        [[1]],
        {},
        {'k': 1},
        {'k': 'x'},
        {'x': 1},
        {'x': 13},
        {'x': 'bad'},
        {'x': 1, 'nope': 2},
        {'kind': 'cat'},
        {'kind': 'dog'},
        {'kind': 'fish'},
        [{'x': 1}],
        [{'x': 'bad'}],
    ]

    def _legacy(self, value, annotation):
        """(accepted, converted_or_error) under the pre-merge pair."""
        try:
            if not _check_type(value, annotation):
                return (False, None)
        except InvalidParamsError as e:
            return ('raised', str(e))
        try:
            return (True, _convert_value(value, annotation))
        except InvalidParamsError as e:
            return ('raised', str(e))

    def _merged(self, value, annotation):
        """(accepted, converted_or_error) under the single-descent walk."""
        try:
            return (True, _coerce(value, annotation))
        except _TypeMismatch:
            return (False, None)
        except InvalidParamsError as e:
            return ('raised', str(e))

    def test_every_annotation_against_every_value(self):
        disagreements = []

        for annotation in self.ANNOTATIONS:
            for value in self.VALUES:
                legacy_verdict, legacy_result = self._legacy(value, annotation)
                merged_verdict, merged_result = self._merged(value, annotation)

                if legacy_verdict != merged_verdict:
                    disagreements.append(
                        f'{_type_name(annotation)} <- {value!r}: '
                        f'old says {legacy_verdict}, merged says {merged_verdict}'
                    )
                elif legacy_verdict is True and legacy_result != merged_result:
                    disagreements.append(
                        f'{_type_name(annotation)} <- {value!r}: '
                        f'old produced {legacy_result!r}, merged produced {merged_result!r}'
                    )

        self.assertEqual(disagreements, [], f'{len(disagreements)} disagreement(s):\n' + '\n'.join(disagreements))

    def test_the_pool_actually_exercises_both_outcomes(self):
        """A pool that only ever accepts, or only ever rejects, proves nothing."""
        verdicts = {self._merged(value, annotation)[0] for annotation in self.ANNOTATIONS for value in self.VALUES}
        self.assertEqual(verdicts, {True, False, 'raised'})

    def test_converted_values_keep_their_type(self):
        """Agreement on the verdict is not enough - the conversion must match too."""
        self.assertIs(type(_coerce(10, float)), float)
        self.assertIs(type(_convert_value(10, float)), float)
        self.assertIsInstance(_coerce({'x': 1}, AgreementLeaf), AgreementLeaf)
        self.assertIsInstance(_convert_value({'x': 1}, AgreementLeaf), AgreementLeaf)


class TestMultipleFaultsKeepTheirPrecedence(unittest.TestCase):
    """With several things wrong at once, the caller gets the same complaint as before.

    The fast path is a single descent, so a nested failure surfaces earlier than
    it used to - before the top-level type checks and the required-field check
    have run. When that happens the original two-pass order is replayed, and
    these are the cases where the replay changes the answer.
    """

    def setUp(self):
        @dataclass
        class Inner:
            x: int

            def __post_init__(self):
                if self.x == 13:
                    raise InvalidParamsError('x must not be 13')

        @dataclass
        class Outer:
            inner: Inner
            tail: str
            required: int

        self.Inner, self.Outer = Inner, Outer

    def test_a_missing_field_outranks_a_nested_rejection(self):
        """The required-field check ran before anything descended, so it wins."""
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'inner': {'x': 13}, 'tail': 't'}, self.Outer)
        self.assertEqual(str(ctx.exception), "Missing required parameter: 'required'")

    def test_a_top_level_type_error_outranks_a_nested_rejection(self):
        """Every top-level field was checked before any descent, so it wins too."""
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'inner': {'x': 13}, 'tail': 5, 'required': 1}, self.Outer)
        self.assertEqual(str(ctx.exception), "Parameter 'tail' expected type 'str', got 'int'")

    def test_a_lone_nested_rejection_is_reported_as_itself(self):
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'inner': {'x': 13}, 'tail': 't', 'required': 1}, self.Outer)
        self.assertEqual(str(ctx.exception), 'x must not be 13')

    def test_a_deep_payload_is_still_bounded_through_the_replay(self):
        """The depth guard must survive the fallback rather than be re-triggered."""

        @dataclass
        class Holder:
            v: Any
            required: int

        deep = 1
        for _ in range(200):
            deep = [deep]

        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({'v': deep}, Holder)
        self.assertIn('nesting depth', str(ctx.exception))


class TestUnfillableParamsTypesAreRefusedAtDefinition(unittest.TestCase):
    """A field JSON cannot express makes the method uncallable, so it is refused.

    The validator has no rule for `tuple`, `set`, `Enum`, `datetime` and friends,
    so the value falls through to an isinstance() check that nothing parsed from
    JSON can pass. The class used to register happily and then answer -32602 to
    every call - blaming the caller's string for a type that would have refused
    everything they could possibly send.
    """

    def test_the_unfillable_types_are_named(self):
        import datetime
        import decimal
        import enum
        import uuid

        class Colour(enum.Enum):
            RED = 'red'

        for annotation, expected in (
            (tuple[int, int], 'tuple'),
            (set[int], 'set'),
            (frozenset[int], 'frozenset'),
            (Colour, 'Colour'),
            (datetime.datetime, 'datetime'),
            (datetime.date, 'date'),
            (uuid.UUID, 'UUID'),
            (decimal.Decimal, 'Decimal'),
            (bytes, 'bytes'),
        ):
            with self.subTest(annotation=annotation):
                params_type = dataclass(type('P', (), {'__annotations__': {'v': annotation}}))
                problems = find_unsupported_annotations(params_type)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(expected, problems[0])
                self.assertTrue(problems[0].startswith('v ('))

    def test_the_types_json_does_express_are_accepted(self):
        @dataclass
        class Inner:
            n: int

        for annotation in (
            int,
            str,
            bool,
            float,
            type(None),
            Any,
            list,
            dict,
            list[int],
            dict[str, int],
            list[Inner],
            int | None,
            int | str,
            Optional[Inner],  # noqa: UP045 - both spellings have to be accepted
            Literal['a', 'b'],
        ):
            with self.subTest(annotation=annotation):
                params_type = dataclass(type('P', (), {'__annotations__': {'v': annotation}}))
                self.assertEqual(find_unsupported_annotations(params_type), [])

    def test_the_walk_descends_into_nested_dataclasses(self):
        @dataclass
        class Deep:
            when: 'set[int]'

        @dataclass
        class Middle:
            deep: Deep

        @dataclass
        class Outer:
            middle: Middle

        problems = find_unsupported_annotations(Outer)
        self.assertEqual(len(problems), 1)
        self.assertIn('Deep.when', problems[0])

    def test_a_bad_type_inside_a_container_or_union_is_found(self):
        @dataclass
        class InList:
            v: 'list[set[int]]'

        @dataclass
        class InDict:
            v: 'dict[str, tuple[int]]'

        @dataclass
        class InUnion:
            v: 'int | set[int]'

        for params_type in (InList, InDict, InUnion):
            with self.subTest(params_type=params_type.__name__):
                self.assertEqual(len(find_unsupported_annotations(params_type)), 1)

    def test_a_dict_key_no_string_can_satisfy_is_refused(self):
        """A JSON object key is always a string, whatever the annotation says.

        `int` is a perfectly good *value* type, so the generic support check
        passed it and `dict[int, str]` registered without complaint - then
        refused every populated payload, because `_coerce('1', int)` cannot
        succeed. Nine annotation shapes reached the wire in that state.
        """
        for annotation in (
            dict[int, str],
            dict[float, str],
            dict[bool, str],
            dict[Literal[1], str],
            dict[type(None), str],
            dict[list[str], str],
            list[dict[int, str]],
            dict[str, dict[int, str]],
        ):
            with self.subTest(annotation=annotation):
                params_type = dataclass(type('P', (), {'__annotations__': {'m': annotation}}))
                problems = find_unsupported_annotations(params_type)
                self.assertEqual(len(problems), 1, f'{annotation} was accepted: {problems}')
                self.assertIn('keys are always strings', problems[0])

    def test_a_dict_key_some_string_can_satisfy_is_accepted(self):
        """Refusing any of these would break a method that serves every caller.

        `dict[int | str, str]` works because the str arm takes every key, and
        `dict[Literal['a', 1], str]` because the 'a' arm does. A rule that only
        allowed a bare `str` would reject both.
        """
        cases = [
            (dict[str, str], {'a': 'x'}),
            (dict[Any, str], {'a': 'x'}),
            (dict[int | str, str], {'a': 'x'}),
            (dict[Literal['a', 'b'], str], {'a': 'x'}),
            (dict[Literal['a', 1], str], {'a': 'x'}),
            (dict, {'a': 1}),
            (typing.Dict, {'a': 1}),  # noqa: UP006, UP035 - unparameterized, so no key type to check
        ]
        for annotation, payload in cases:
            with self.subTest(annotation=annotation):
                params_type = dataclass(type('P', (), {'__annotations__': {'m': annotation}}))
                self.assertEqual(find_unsupported_annotations(params_type), [])
                self.assertIsNotNone(validate_params({'m': payload}, params_type))

    def test_a_dead_union_arm_is_reported_even_though_another_arm_works(self):
        """`Leaf | dict[int, str]` accepts a Leaf, so something gets through.

        It is still refused: the author believes they can also receive that
        mapping and never will, and nothing else would ever tell them. A dead arm
        is the same silent failure as a dead field.
        """

        @dataclass
        class Leaf:
            n: int

        params_type = dataclass(type('P', (), {'__annotations__': {'v': Leaf | dict[int, str]}}))
        self.assertEqual(len(find_unsupported_annotations(params_type)), 1)

    def test_a_self_referencing_dataclass_terminates(self):
        """The walk has to notice it is back where it started."""
        self.assertEqual(find_unsupported_annotations(RecursiveCommentParams), [])
        self.assertEqual(find_unsupported_annotations(RecursiveComment), [])

    def test_defining_a_method_with_one_raises(self):
        import datetime

        @dataclass
        class Booking:
            starts_at: datetime.datetime

        @dataclass
        class Result:
            ok: bool

        with self.assertRaises(TypeError) as ctx:

            class Book(Method):
                def execute(self, params: Booking) -> Result:
                    return Result(ok=True)

        message = str(ctx.exception)
        self.assertIn('cannot be filled from JSON', message)
        self.assertIn('starts_at', message)
        self.assertIn('__post_init__', message)

    def test_the_documented_workaround_is_accepted(self):
        """Take it as the wire type and convert it where a rejection is a -32602."""
        import datetime

        @dataclass
        class Booking:
            starts_at: str

            def __post_init__(self):
                try:
                    self.when = datetime.datetime.fromisoformat(self.starts_at)
                except ValueError:
                    raise InvalidParamsError('starts_at must be an ISO-8601 timestamp') from None

        @dataclass
        class Result:
            ok: bool

        class Book(Method):
            def execute(self, params: Booking) -> Result:
                return Result(ok=True)

        rpc = JSONRPC()
        rpc.register('book', Book())

        good = json.loads(
            rpc.handle(
                json.dumps({'jsonrpc': '2.0', 'method': 'book', 'params': {'starts_at': '2026-01-01T09:00'}, 'id': 1})
            )
        )
        self.assertEqual(good['result'], {'ok': True})

        bad = json.loads(
            rpc.handle(json.dumps({'jsonrpc': '2.0', 'method': 'book', 'params': {'starts_at': 'soon'}, 'id': 1}))
        )
        self.assertEqual(bad['error']['code'], -32602)
        self.assertIn('ISO-8601', bad['error']['message'])


class TestQuotedCallerTextIsBounded(unittest.TestCase):
    """A few messages must name what the caller sent, or they help nobody.

    That makes the response a function of the request: a 900 KB parameter name
    produced a 1.8 MB response, doubled because the name goes in `error.data`
    too. max_request_size bounds it, but a 2x amplifier on every rejected
    request is not something to leave for the body limit alone to hold.
    """

    def setUp(self):
        @dataclass
        class P:
            a: int

        @dataclass
        class R:
            ok: bool

        class M(Method):
            def execute(self, params: P) -> R:
                return R(ok=True)

        self.rpc = JSONRPC(max_request_size=-1)
        self.rpc.register('m', M())

    def _error(self, body):
        return json.loads(self.rpc.handle(json.dumps(body)))['error']

    def test_a_long_parameter_name_is_clipped_in_message_and_data(self):
        error = self._error({'jsonrpc': '2.0', 'method': 'm', 'params': {'X' * 5000: 1}, 'id': 1})
        self.assertLess(len(error['message']), 300)
        self.assertLess(len(error['data']['parameter']), 300)
        self.assertIn('5000 characters', error['data']['parameter'])

    def test_a_long_method_name_is_clipped(self):
        error = self._error({'jsonrpc': '2.0', 'method': 'Y' * 5000, 'id': 1})
        self.assertEqual(error['code'], -32601)
        self.assertLess(len(error['message']), 300)

    def test_a_long_jsonrpc_member_is_clipped(self):
        error = self._error({'jsonrpc': 'Z' * 5000, 'method': 'm', 'params': {'a': 1}, 'id': 1})
        self.assertLess(len(error['message']), 300)

    def test_the_response_stops_growing_with_the_request(self):
        """A 500x larger name must not give a 500x larger answer.

        The few bytes of spread are the decimal digits of the reported length,
        which is the point of reporting it.
        """
        sizes = []
        for length in (1000, 100_000, 500_000):
            body = json.dumps({'jsonrpc': '2.0', 'method': 'm', 'params': {'X' * length: 1}, 'id': 1})
            sizes.append(len(self.rpc.handle(body)))
        self.assertLess(max(sizes) - min(sizes), 10, f'the response still tracks the request: {sizes}')

    def test_a_name_of_ordinary_length_is_left_alone(self):
        """Clipping must not touch the messages anyone actually reads."""
        error = self._error({'jsonrpc': '2.0', 'method': 'm', 'params': {'colour': 1}, 'id': 1})
        self.assertEqual(error['message'], "Unknown parameter: 'colour'")
        self.assertEqual(error['data']['parameter'], 'colour')

        error = self._error({'jsonrpc': '2.0', 'method': 'nope', 'id': 1})
        self.assertEqual(error['message'], "Method 'nope' not found")

    def test_the_id_is_still_echoed_in_full(self):
        """The spec requires it, and a client matches the answer to the call by it."""
        long_id = 'i' * 1000
        response = json.loads(
            self.rpc.handle(json.dumps({'jsonrpc': '2.0', 'method': 'm', 'params': {'a': 'x'}, 'id': long_id}))
        )
        self.assertEqual(response['id'], long_id)


class TestInvalidParamsCarriesMachineReadableData(unittest.TestCase):
    """-32602 says which parameter in a field, not only in an English sentence.

    The spec provides `data` for exactly this. Without it a client that wants to
    highlight the offending field has to parse "Parameter 'age' expected type
    'int', got 'str'" - and any wording change silently breaks it.

    Nothing here is new information: every value in `data` already appears in the
    message, and the rejected value itself is still never echoed.
    """

    def setUp(self):
        @dataclass
        class Address:
            city: str
            zip: str

            def __post_init__(self):
                if len(self.zip) != 5:
                    raise ValueError('zip must be five digits')

        @dataclass
        class Person:
            name: str
            age: int
            address: Address
            nickname: str = ''

        self.Address, self.Person = Address, Person
        self.good = {'name': 'a', 'age': 1, 'address': {'city': 'x', 'zip': '12345'}}

    def _data(self, params, params_type=None):
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params(params, params_type or self.Person)
        return ctx.exception.data

    def test_a_type_mismatch_names_the_parameter_and_both_types(self):
        self.assertEqual(
            self._data({**self.good, 'age': 'old'}),
            {'reason': 'type_mismatch', 'parameter': 'age', 'expected': 'int', 'received': 'str'},
        )

    def test_an_unknown_parameter_names_itself(self):
        self.assertEqual(
            self._data({**self.good, 'colour': 'red'}),
            {'reason': 'unknown_parameter', 'parameter': 'colour'},
        )

    def test_a_missing_parameter_names_itself(self):
        self.assertEqual(
            self._data({'name': 'a', 'address': {'city': 'x', 'zip': '12345'}}),
            {'reason': 'missing_parameter', 'parameter': 'age'},
        )

    def test_omitting_params_entirely_lists_every_required_one(self):
        self.assertEqual(
            self._data(None),
            {'reason': 'missing_parameter', 'parameters': ['name', 'age', 'address']},
        )

    def test_too_many_positional_parameters_reports_both_counts(self):
        self.assertEqual(
            self._data([1, 2, 3, 4, 5]),
            {'reason': 'too_many_positional', 'expected': 4, 'received': 5},
        )

    def test_a_rejection_from_post_init_names_the_parameter_it_came_in_under(self):
        """__post_init__ sees the whole object, so only the caller's field is known."""
        self.assertEqual(
            self._data({**self.good, 'address': {'city': 'x', 'zip': '1'}}),
            {'parameter': 'address', 'reason': 'rejected_by_validator'},
        )

    def test_a_union_that_matched_nothing_reports_the_whole_union(self):
        @dataclass
        class Cat:
            meow: str

        @dataclass
        class Dog:
            woof: str

        @dataclass
        class Params:
            pet: Cat | Dog

        self.assertEqual(
            self._data({'pet': {'meow': 1}}, Params),
            {'parameter': 'pet', 'reason': 'no_matching_variant', 'expected': 'Cat | Dog'},
        )

    def test_the_data_never_contradicts_the_message(self):
        """Both are read by someone; they must not disagree about the field."""
        for params in (
            {**self.good, 'age': 'old'},
            {**self.good, 'colour': 'red'},
            {'name': 'a', 'address': {'city': 'x', 'zip': '12345'}},
            {**self.good, 'address': {'city': 1, 'zip': '12345'}},
        ):
            with self.subTest(params=params):
                with self.assertRaises(InvalidParamsError) as ctx:
                    validate_params(params, self.Person)
                named = ctx.exception.data.get('parameter')
                self.assertIsNotNone(named)
                self.assertIn(f"'{named}'", str(ctx.exception))

    def test_the_data_never_carries_the_rejected_value(self):
        """An error message is the one thing a caller always reads back."""
        secret = 'sk-live-0123456789'
        with self.assertRaises(InvalidParamsError) as ctx:
            validate_params({**self.good, 'age': secret}, self.Person)
        self.assertNotIn(secret, json.dumps(ctx.exception.data))
        self.assertNotIn(secret, str(ctx.exception))

    def test_the_data_reaches_the_caller_over_the_wire(self):
        @dataclass
        class Result:
            ok: bool

        Person = self.Person

        class Register(Method):
            def execute(self, params: Person) -> Result:
                return Result(ok=True)

        bad = {**self.good, 'age': 'old'}
        # 1.0 takes positional params only, so the same fault has to be sent
        # in the shape that version accepts.
        requests = {
            '2.0': {'jsonrpc': '2.0', 'method': 'register', 'params': bad, 'id': 1},
            '1.0': {'method': 'register', 'params': ['a', 'old', {'city': 'x', 'zip': '12345'}], 'id': 1},
        }

        for version, request in requests.items():
            with self.subTest(version=version):
                rpc = JSONRPC(version=version)
                rpc.register('register', Register())

                error = json.loads(rpc.handle(json.dumps(request)))['error']
                self.assertEqual(error['code'], -32602)
                self.assertEqual(error['data']['parameter'], 'age')

    def test_every_invalid_params_error_carries_a_reason(self):
        """A client can branch on data['reason'] without checking for its absence."""

        @dataclass
        class Result:
            ok: bool

        Person = self.Person

        class Register(Method):
            def execute(self, params: Person) -> Result:
                return Result(ok=True)

        rpc = JSONRPC(version='2.0')
        rpc.register('register', Register())

        faults = [
            {**self.good, 'age': 'old'},
            {**self.good, 'colour': 'red'},
            {'name': 'a'},
            [1, 2, 3, 4, 5],
            {**self.good, 'address': {'city': 'x', 'zip': '1'}},
        ]
        for params in faults:
            with self.subTest(params=params):
                body = json.dumps({'jsonrpc': '2.0', 'method': 'register', 'params': params, 'id': 1})
                error = json.loads(rpc.handle(body))['error']
                self.assertEqual(error['code'], -32602)
                self.assertIn('reason', error.get('data', {}))

        # A version refusing the other params shape is a -32602 too.
        v1 = JSONRPC(version='1.0')
        v1.register('register', Register())
        error = json.loads(v1.handle(json.dumps({'method': 'register', 'params': self.good, 'id': 1})))['error']
        self.assertEqual(error['code'], -32602)
        self.assertEqual(error['data']['reason'], 'params_shape_not_allowed')

    def test_a_valid_call_is_unaffected(self):
        person = validate_params(self.good, self.Person)
        self.assertEqual(person.age, 1)
        self.assertEqual(person.address.city, 'x')


class TestCoerceEdgeAnnotations(unittest.TestCase):
    """Annotations the merged walk has no special handling for."""

    def test_an_arbitrary_class_annotation_accepts_an_instance(self):
        class Custom:
            pass

        instance = Custom()
        self.assertIs(_coerce(instance, Custom), instance)

    def test_an_arbitrary_class_annotation_rejects_anything_else(self):
        class Custom:
            pass

        with self.assertRaises(_TypeMismatch):
            _coerce(5, Custom)

    def test_an_unusable_annotation_is_reported_as_unsupported(self):
        with self.assertRaises(InvalidParamsError) as ctx:
            _coerce(5, Literal)
        self.assertIn('Unsupported type annotation', str(ctx.exception))

    def test_an_unparameterized_generic_still_bounds_depth(self):
        deep = 1
        for _ in range(200):
            deep = [deep]

        with self.assertRaises(InvalidParamsError):
            _coerce(deep, list)
        with self.assertRaises(InvalidParamsError):
            _coerce({'k': deep}, dict)

    def test_an_unparameterized_generic_accepts_a_shallow_value(self):
        self.assertEqual(_coerce([1, 2], list), [1, 2])
        self.assertEqual(_coerce({'k': 1}, dict), {'k': 1})

    def test_bare_typing_generics_behave_like_the_builtins(self):
        """`typing.List` has a list origin but no arguments - still bounded.

        Deprecated spellings, but plenty of existing code uses them, and they
        take a different branch from the builtins.
        """
        import typing  # noqa: UP035

        bare_list = typing.List  # noqa: UP006
        bare_dict = typing.Dict  # noqa: UP006

        self.assertEqual(_coerce([1, 'x'], bare_list), [1, 'x'])
        self.assertEqual(_coerce({'k': 1}, bare_dict), {'k': 1})

        deep = 1
        for _ in range(200):
            deep = [deep]
        with self.assertRaises(InvalidParamsError):
            _coerce(deep, bare_list)
        with self.assertRaises(InvalidParamsError):
            _coerce({'k': deep}, bare_dict)

    def test_a_wrong_shape_for_an_unparameterized_generic_is_a_mismatch(self):
        with self.assertRaises(_TypeMismatch):
            _coerce('x', list)
        with self.assertRaises(_TypeMismatch):
            _coerce('x', dict)


class TestInitVarDetectionUnderPostponedAnnotations(unittest.TestCase):
    def test_a_string_annotation_is_recognised(self):
        """`from __future__ import annotations` leaves InitVar as a string."""
        from jsonrpc.validation import find_initvar_fields

        class Pretend:
            __annotations__ = {'a': 'int', 'seed': 'InitVar[int]'}

        self.assertEqual(find_initvar_fields(Pretend), ['seed'])

    def test_the_qualified_string_form_is_recognised(self):
        from jsonrpc.validation import find_initvar_fields

        class Pretend:
            __annotations__ = {'seed': 'dataclasses.InitVar[int]'}

        self.assertEqual(find_initvar_fields(Pretend), ['seed'])


class TestRejectingDeepInputStaysLinear(unittest.TestCase):
    """A rejected payload must cost what an accepted one costs, near enough.

    Refusing a request is the path an attacker controls: they choose the depth
    and they choose the byte that fails. When the fault is at the bottom of a
    recursive structure, each level must be visited a bounded number of times.

    This is measured as work done rather than wall-clock, so it cannot go flaky
    on a loaded machine - and so that a regression reads as "2**40 calls"
    instead of "the test took a while". A version of this validator re-ran the
    conversion on the failing path, which re-entered the same nested dataclass
    and re-ran it again once per level: a 467-byte body took seven seconds, and
    MAX_NESTING_DEPTH stopped being a guard.
    """

    def _nested(self, depth, bad_leaf):
        node = {'text': 123 if bad_leaf else 'leaf'}
        for _ in range(depth):
            node = {'text': 't', 'reply': node}
        return node

    @contextlib.contextmanager
    def _validator_budget(self, budget=10_000):
        """Abort once the validator has been entered more times than allowed.

        The budget is enforced rather than measured. An exponential validator
        would otherwise run longer than anyone will wait, and a hanging test is
        a worse signal than a failing one - especially for a defect whose whole
        point is that it does not terminate.
        """
        import jsonrpc.validation as validation

        class _BudgetExceeded(BaseException):
            """BaseException so that no `except Exception` on the way out eats it."""

        state = {'calls': 0}
        original = validation.validate_params

        def counting(*args, **kwargs):
            state['calls'] += 1
            if state['calls'] > budget:
                raise _BudgetExceeded
            return original(*args, **kwargs)

        validation.validate_params = counting
        try:
            yield state
        except _BudgetExceeded:
            self.fail(f'the validator was entered more than {budget} times for one payload - this is not linear')
        finally:
            validation.validate_params = original

    def _count_validations(self, params, budget=10_000):
        """How many times validate_params is entered for one payload."""
        with self._validator_budget(budget) as state:
            import jsonrpc.validation as validation

            try:
                validation.validate_params(params, RecursiveCommentParams)
            except InvalidParamsError:
                pass
        return state['calls']

    def test_a_rejected_tree_visits_each_level_a_bounded_number_of_times(self):
        depth = 40
        calls = self._count_validations(self._nested(depth, bad_leaf=True))

        # One entry for the params object plus one per level is the ideal; allow
        # a small constant factor. Anything exponential blows past this by many
        # orders of magnitude.
        self.assertLessEqual(
            calls,
            4 * (depth + 2),
            f'rejecting a depth-{depth} payload entered the validator {calls} times',
        )

    def test_the_cost_tracks_depth_rather_than_doubling_with_it(self):
        shallow = self._count_validations(self._nested(10, bad_leaf=True))
        deep = self._count_validations(self._nested(20, bad_leaf=True))

        # Ten more levels should add roughly ten more visits, not multiply by 2**10.
        self.assertLess(deep, shallow * 3, f'{shallow} calls at depth 10, {deep} at depth 20')

    def test_a_rejected_tree_costs_about_what_an_accepted_one_costs(self):
        depth = 40
        accepted = self._count_validations(self._nested(depth, bad_leaf=False))
        rejected = self._count_validations(self._nested(depth, bad_leaf=True))

        self.assertLess(rejected, accepted * 3, f'{accepted} calls accepted, {rejected} rejected')

    def test_the_deepest_allowed_payload_is_answered(self):
        """At the depth limit the answer must still arrive, and be the right one."""
        rpc = JSONRPC(version='2.0')

        class Post(Method):
            def execute(self, params: RecursiveCommentParams) -> int:
                return 1

        rpc.register('post', Post())
        body = json.dumps(
            {
                'jsonrpc': '2.0',
                'method': 'post',
                'params': {'comment': self._nested(MAX_NESTING_DEPTH - 1, bad_leaf=True)},
                'id': 1,
            }
        )

        with self._validator_budget():
            data = json.loads(rpc.handle(body))
        self.assertEqual(data['error']['code'], -32602)

    def test_a_full_batch_of_them_is_answered_too(self):
        """max_batch multiplies whatever one request costs."""
        rpc = JSONRPC(version='2.0')

        class Post(Method):
            def execute(self, params: RecursiveCommentParams) -> int:
                return 1

        rpc.register('post', Post())
        entry = {
            'jsonrpc': '2.0',
            'method': 'post',
            'params': {'comment': self._nested(MAX_NESTING_DEPTH - 1, bad_leaf=True)},
        }
        body = json.dumps([dict(entry, id=i) for i in range(100)])

        with self._validator_budget(budget=100_000):
            data = json.loads(rpc.handle(body))
        self.assertEqual(len(data), 100)
        self.assertTrue(all(entry['error']['code'] == -32602 for entry in data))


if __name__ == '__main__':
    unittest.main()
