"""Utility tests for JSON-RPC infrastructure.

This module tests core utility functions and validation logic:
- Batch request handling (is_batch)
- Request parsing and building (parse_request, build_request, build_notification)
- Response parsing and building (build_response, build_error_response, parse_response)
- Parameter validation (validate_params with dataclasses)
- Result type validation (validate_result_type)
"""

import unittest
from dataclasses import dataclass

from jsonrpc.errors import (
    InvalidParamsError,
    InvalidRequestError,
    InvalidResultError,
    ParseError,
    RPCError,
    ServerError,
)
from jsonrpc.request import build_notification, build_request, parse_request
from jsonrpc.response import build_error_response, build_response, parse_response
from jsonrpc.types import ErrorResponse, Response
from jsonrpc.validation import (
    _check_type,
    _convert_value,
    _type_name,
    is_batch,
    validate_params,
    validate_result_type,
)


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
        """Test v1.0 with id=null - NOT a notification in v1.0."""
        data = '{"method": "notify", "params": [], "id": null}'
        req = parse_request(data)
        # v1.0 has no notification concept - all requests get responses
        self.assertFalse(req.is_notification)
        self.assertIsNone(req.id)

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

    def test_validate_dataclass_result(self):
        # Should not raise (dict can match dataclass in _check_type)
        validate_result_type({'a': 1, 'b': 2}, AddParams)

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
    """Test _convert_value edge cases."""

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

    def test_convert_value_union_all_types_fail_returns_value(self):
        """Union fallback returns original value when all conversions raise exceptions."""
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

        # Both types fail → return original value (line 203)
        value = {'x': 42}
        result = _convert_value(value, TypeA | TypeB)
        self.assertIs(result, value)

    def test_convert_value_plain_list_returns_as_is(self):
        """Test _convert_value with plain list (no args) - line 246."""
        from jsonrpc.validation import _convert_value

        # Plain list type without args should return value as-is
        value = [1, 'mixed', None]
        result = _convert_value(value, list)
        self.assertEqual(result, value)

    def test_convert_value_plain_dict_returns_as_is(self):
        """Test _convert_value with plain dict (no args) - line 254."""
        from jsonrpc.validation import _convert_value

        # Plain dict type without args should return value as-is
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
    """Tests for _convert_value() with unparameterized typing.List / typing.Dict (lines 210, 217)."""

    def test_convert_value_typing_List_returns_value_unchanged(self):
        """_convert_value(list, typing.List) — origin=list, args=() → line 210 return value."""
        import typing

        value = [1, 2, 3]
        result = _convert_value(value, typing.List)  # noqa: UP006, UP035
        self.assertEqual(result, value)

    def test_convert_value_typing_Dict_returns_value_unchanged(self):
        """_convert_value(dict, typing.Dict) — origin=dict, args=() → line 217 return value."""
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


if __name__ == '__main__':
    unittest.main()
