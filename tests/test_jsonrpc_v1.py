"""Comprehensive tests for JSON-RPC 1.0 protocol implementation.

JSON-RPC 1.0 specification: https://www.jsonrpc.org/specification_v1

Key differences from 2.0:
- No "jsonrpc" field in requests/responses
- params MUST be an array (not object)
- Responses always have both "result" and "error" fields
- Notifications use id=null instead of omitting id
"""

import asyncio
import json
import unittest

from jsonrpc import JSONRPC, MethodGroup
from jsonrpc.method import Method
from tests.fixtures import (
    AddMethod,
    AsyncDataclassResultMethod,
    AsyncMethod,
    DataclassResultMethod,
    DictDataclassResultMethod,
    EchoMethod,
    ErrorMethod,
    InternalCallMethod,
    ListDataclassResultMethod,
    MathResult,
    MultiplyMethod,
    NestedCompanyMethod,
    NestedDataclassResultMethod,
    NoParamsMethod,
    OptionalMethod,
    SubtractMethod,
    TypedAddMethod,
    WrongTypeMethod,
)


class TestJSONRPCV1Protocol(unittest.TestCase):
    """Tests for JSON-RPC 1.0 protocol compliance."""

    def setUp(self):
        self.rpc = JSONRPC(version='1.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        math_group.register('subtract', SubtractMethod())
        math_group.register('multiply', MultiplyMethod())
        self.rpc.register('math', math_group)

        self.rpc.register('ping', NoParamsMethod())
        self.rpc.register('echo', EchoMethod())
        self.rpc.register('optional', OptionalMethod())
        self.rpc.register('dataclass_result', DataclassResultMethod())
        self.rpc.register('error', ErrorMethod())
        self.rpc.register('process_company', NestedCompanyMethod())

    def test_v1_no_params(self):
        """Test v1.0 with no params (empty array or null)."""
        # Empty array
        request1 = '{"method": "ping", "params": [], "id": 1}'
        response1 = self.rpc.handle(request1)
        data1 = json.loads(response1)
        self.assertEqual(data1['result'], 'pong')

        # Null params
        request2 = '{"method": "ping", "params": null, "id": 2}'
        response2 = self.rpc.handle(request2)
        data2 = json.loads(response2)
        self.assertEqual(data2['result'], 'pong')

    def test_v1_missing_params(self):
        """Test v1.0 with missing params field."""
        request = '{"method": "ping", "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)
        self.assertEqual(data['result'], 'pong')

    def test_v1_notification_with_null_id(self):
        """Test v1.0 notification (id=null)."""
        request = '{"method": "ping", "params": [], "id": null}'
        response = self.rpc.handle(request)

        # v1.0 notification: returns response with id=null
        # (v2.0 notifications return None)
        self.assertIsNotNone(response)
        data = json.loads(response)
        self.assertEqual(data['result'], 'pong')
        self.assertIsNone(data['id'])

    def test_v1_error_response_format(self):
        """Test v1.0 error response format."""
        request = '{"method": "nonexistent", "params": [], "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # v1.0 error response
        self.assertIsNone(data['result'])
        self.assertIsNotNone(data['error'])
        self.assertEqual(data['id'], 1)
        self.assertNotIn('jsonrpc', data)

        # Error object
        self.assertIn('code', data['error'])
        self.assertIn('message', data['error'])

    def test_v1_method_error(self):
        """Test v1.0 response when method raises error."""
        request = '{"method": "error", "params": [], "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Error response
        self.assertIsNone(data['result'])
        self.assertIsNotNone(data['error'])
        self.assertIn('Intentional error', data['error']['message'])

    def test_v1_optional_params(self):
        """Test v1.0 with optional parameters."""
        # With only required param
        request1 = '{"method": "optional", "params": ["value"], "id": 1}'
        response1 = self.rpc.handle(request1)
        data1 = json.loads(response1)
        self.assertEqual(data1['result'], 'value:default')

        # With all params
        request2 = '{"method": "optional", "params": ["value", "custom"], "id": 2}'
        response2 = self.rpc.handle(request2)
        data2 = json.loads(response2)
        self.assertEqual(data2['result'], 'value:custom')

    def test_v1_dataclass_result_serialization(self):
        """Test that v1.0 serializes dataclass results to JSON."""
        request = '{"method": "dataclass_result", "params": [5, 3], "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Dataclass should be serialized
        self.assertIsInstance(data['result'], dict)
        self.assertEqual(data['result']['operation'], 'add')
        self.assertEqual(data['result']['result'], 8)
        self.assertIsNone(data['error'])

    def test_v1_numeric_id(self):
        """Test v1.0 with numeric id."""
        request = '{"method": "ping", "params": [], "id": 42}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        self.assertEqual(data['id'], 42)
        self.assertEqual(data['result'], 'pong')

    def test_v1_nested_dataclass_3_levels(self):
        """Test v1.0 with 3-level nested dataclass (Contact -> Address -> CompanyInfo)."""
        # v1.0 uses positional array params
        # CompanyInfo(name, founded, Address(street, city, Contact(email, phone)))
        request = """{
            "method": "process_company",
            "params": [
                "TechCorp",
                2010,
                ["123 Main St", "San Francisco", ["info@techcorp.com", "+1-555-0100"]]
            ],
            "id": 1
        }"""
        response = self.rpc.handle(request)
        data = json.loads(response)

        # v1.0 response format
        self.assertIsNone(data['error'])
        self.assertEqual(data['id'], 1)
        self.assertNotIn('jsonrpc', data)

        # Verify nested dataclass was correctly parsed and processed
        expected = 'TechCorp founded in 2010, located at 123 Main St, San Francisco, contact: info@techcorp.com'
        self.assertEqual(data['result'], expected)

    def test_v1_nested_dataclass_missing_field(self):
        """Test v1.0 with 3-level nested dataclass with missing field."""
        # Missing the 'phone' field in Contact
        request = """{
            "method": "process_company",
            "params": [
                "IncompleteInc",
                2015,
                ["789 Startup Ave", "Boston", ["contact@incomplete.com"]]
            ],
            "id": 3
        }"""
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Should return error for missing required field
        self.assertIsNone(data['result'])
        self.assertIsNotNone(data['error'])
        self.assertEqual(data['id'], 3)
        self.assertIn('Missing required parameter', data['error']['message'])

    def test_v1_nested_dataclass_wrong_type(self):
        """Test v1.0 with 3-level nested dataclass with wrong type."""
        # 'founded' should be int, not string
        request = """{
            "method": "process_company",
            "params": [
                "BadTypeCo",
                "not_a_year",
                ["100 Error St", "Seattle", ["bad@type.com", "+1-555-0300"]]
            ],
            "id": 4
        }"""
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Should return error for type validation
        self.assertIsNone(data['result'])
        self.assertIsNotNone(data['error'])
        self.assertEqual(data['id'], 4)

    def test_v1_parse_error(self):
        """Test v1.0 with invalid JSON."""
        request = '{invalid json'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Should return parse error
        self.assertIsNone(data['result'])
        self.assertIsNotNone(data['error'])
        self.assertEqual(data['error']['code'], -32700)
        self.assertIn('Invalid JSON', data['error']['message'])

    def test_v1_invalid_request_missing_method(self):
        """Test v1.0 with missing method field."""
        request = '{"params": [], "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Should return invalid request error
        self.assertIsNone(data['result'])
        self.assertIsNotNone(data['error'])
        self.assertEqual(data['error']['code'], -32600)

    def test_v1_list_methods(self):
        """Test listing all methods in v1.0."""
        methods = self.rpc.list_methods()
        self.assertIn('math.add', methods)
        self.assertIn('math.subtract', methods)
        self.assertIn('math.multiply', methods)
        self.assertIn('ping', methods)
        self.assertIn('echo', methods)
        self.assertIn('process_company', methods)


class TestJSONRPCV1Async(unittest.TestCase):
    """Tests for async methods with JSON-RPC 1.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='1.0')
        self.rpc.register('async_test', AsyncMethod())
        self.rpc.register('ping', NoParamsMethod())

    def test_v1_async_method(self):
        """Test v1.0 with async method using handle_async()."""
        request = '{"method": "async_test", "params": [], "id": 1}'
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)

        # v1.0 response format
        self.assertEqual(data['result'], 'async_result')
        self.assertIsNone(data['error'])
        self.assertEqual(data['id'], 1)
        self.assertNotIn('jsonrpc', data)


class TestJSONRPCV1CallMethod(unittest.TestCase):
    """Tests for internal call_method() with v1.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='1.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        self.rpc.register('math', math_group)

    def test_v1_call_method_direct(self):
        """Test call_method() works same way in v1.0 and v2.0."""
        # call_method returns raw result, not JSON response
        result = self.rpc.call_method('math.add', [5, 3])
        self.assertEqual(result, 8)

        # Also works with dict params
        result2 = self.rpc.call_method('math.add', {'a': 10, 'b': 20})
        self.assertEqual(result2, 30)


class TestJSONRPCV1InternalCalls(unittest.TestCase):
    """Tests for internal method-to-method calls with v1.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='1.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        self.rpc.register('math', math_group)

        calc_group = MethodGroup()
        calc_group.register('double_add', InternalCallMethod())
        self.rpc.register('calc', calc_group)

    def test_v1_internal_method_call(self):
        """Test v1.0 method calling another method internally."""
        # InternalCallMethod calls math.add and doubles the result
        result = self.rpc.call_method('calc.double_add', [5, 3])
        self.assertEqual(result, 16)  # (5 + 3) * 2

    def test_v1_internal_method_call_via_handle(self):
        """Test v1.0 internal method call via handle()."""
        request = '{"method": "calc.double_add", "params": [10, 5], "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        self.assertEqual(data['result'], 30)  # (10 + 5) * 2
        self.assertIsNone(data['error'])
        self.assertEqual(data['id'], 1)


class TestJSONRPCV1NestedGroups(unittest.TestCase):
    """Tests for nested group prefixes with v1.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='1.0')

        # Create nested groups using proper nesting (utils -> text -> format)
        format_group = MethodGroup()
        format_group.register('echo', EchoMethod())

        text_group = MethodGroup()
        text_group.register('format', format_group)

        utils_group = MethodGroup()
        utils_group.register('text', text_group)

        self.rpc.register('utils', utils_group)

        # Create deeply nested group (api -> v1 -> users -> admin)
        admin_group = MethodGroup()
        admin_group.register('ping', NoParamsMethod())

        users_group = MethodGroup()
        users_group.register('admin', admin_group)

        v1_group = MethodGroup()
        v1_group.register('users', users_group)

        api_group = MethodGroup()
        api_group.register('v1', v1_group)

        self.rpc.register('api', api_group)

    def test_v1_nested_group_methods_listed(self):
        """Test v1.0 lists nested group methods."""
        methods = self.rpc.list_methods()
        self.assertIn('utils.text.format.echo', methods)
        self.assertIn('api.v1.users.admin.ping', methods)

    def test_v1_call_nested_method(self):
        """Test v1.0 call nested method directly."""
        result = self.rpc.call_method('utils.text.format.echo', ['hello'])
        self.assertEqual(result, 'hello')

    def test_v1_handle_nested_method(self):
        """Test v1.0 handle nested method via JSON-RPC."""
        request = '{"method": "utils.text.format.echo", "params": ["test"], "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        self.assertEqual(data['result'], 'test')
        self.assertIsNone(data['error'])

    def test_v1_handle_deeply_nested_method(self):
        """Test v1.0 handle deeply nested method."""
        request = '{"method": "api.v1.users.admin.ping", "params": [], "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        self.assertEqual(data['result'], 'pong')
        self.assertIsNone(data['error'])


class TestJSONRPCV1ResultValidation(unittest.TestCase):
    """Tests for result type validation with v1.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='1.0')
        math = MethodGroup()
        math.register('typed_add', TypedAddMethod())
        math.register('wrong_type', WrongTypeMethod())
        self.rpc.register('math', math)

    def test_v1_call_method_without_validation(self):
        """Test v1.0 succeeds with wrong type when validation off."""
        result = self.rpc.call_method('math.wrong_type')
        self.assertEqual(result, 'not an int')

    def test_v1_call_method_with_per_call_validation(self):
        """Test v1.0 succeeds with correct type and validation."""
        result = self.rpc.call_method('math.typed_add', [1, 2], validate_result=True)
        self.assertEqual(result, 3)

    def test_v1_call_method_with_per_call_validation_wrong_type(self):
        """Test v1.0 fails with wrong type and validation."""
        from jsonrpc import InvalidResultError

        with self.assertRaises(InvalidResultError):
            self.rpc.call_method('math.wrong_type', validate_result=True)

    def test_v1_global_validation_validates_results(self):
        """Test v1.0 global validation works."""
        from jsonrpc import InvalidResultError

        rpc = JSONRPC(version='1.0', validate_results=True)
        math = MethodGroup()
        math.register('typed_add', TypedAddMethod())
        math.register('wrong_type', WrongTypeMethod())
        rpc.register('math', math)

        # Correct type should work
        result = rpc.call_method('math.typed_add', [1, 2])
        self.assertEqual(result, 3)

        # Wrong type should fail
        with self.assertRaises(InvalidResultError):
            rpc.call_method('math.wrong_type')

    def test_v1_handle_with_validation_error(self):
        """Test v1.0 handle() returns error for validation failure."""
        rpc = JSONRPC(version='1.0', validate_results=True)
        math = MethodGroup()
        math.register('wrong_type', WrongTypeMethod())
        rpc.register('math', math)

        request = '{"method": "math.wrong_type", "params": [], "id": 1}'
        response = rpc.handle(request)
        data = json.loads(response)

        # Should return error response
        self.assertIsNone(data['result'])
        self.assertIsNotNone(data['error'])
        self.assertEqual(data['error']['code'], -32001)  # InvalidResultError


class TestJSONRPCV1DataclassResult(unittest.TestCase):
    """Tests for methods returning dataclass results with v1.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='1.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        math_group.register('subtract', SubtractMethod())
        test_group = MethodGroup()
        test_group.register('dataclass_result', DataclassResultMethod())
        test_group.register('async_dataclass_add', AsyncDataclassResultMethod())
        test_group.register('nested_user', NestedDataclassResultMethod())
        test_group.register('list_results', ListDataclassResultMethod())
        test_group.register('dict_results', DictDataclassResultMethod())
        self.rpc.register('test', test_group)
        self.rpc.register('math', math_group)

    def test_v1_handle_async_dataclass_result(self):
        """Test v1.0 async method with dataclass result via handle_async()."""
        request = '{"method": "test.async_dataclass_add", "params": [7, 2], "id": 1}'
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)

        # Should serialize dataclass to dict
        self.assertEqual(data['result']['operation'], 'add')
        self.assertEqual(data['result']['result'], 9)
        self.assertIsNone(data['error'])
        self.assertEqual(data['id'], 1)

    def test_v1_call_method_async_returns_dataclass_object(self):
        """Test v1.0 call_method_async() returns dataclass object."""
        result = asyncio.run(self.rpc.call_method_async('test.async_dataclass_add', [20, 10]))

        # Should return actual dataclass instance
        self.assertIsInstance(result, MathResult)
        self.assertEqual(result.operation, 'add')
        self.assertEqual(result.result, 30)

    def test_v1_handle_nested_dataclass_result(self):
        """Test v1.0 nested dataclass result serialization."""
        request = '{"method": "test.nested_user", "params": [], "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Nested dataclass should be serialized to nested dict
        self.assertEqual(data['result']['name'], 'Jakub')
        self.assertEqual(data['result']['age'], 25)
        self.assertEqual(data['result']['address']['city'], 'Krakow')
        self.assertEqual(data['result']['address']['country'], 'Poland')
        self.assertIsNone(data['error'])

    def test_v1_handle_list_dataclass_result(self):
        """Test v1.0 list of dataclass results serialization."""
        request = '{"method": "test.list_results", "params": [], "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # List of dataclass should be serialized to list of dicts
        self.assertIsInstance(data['result'], list)
        self.assertEqual(len(data['result']), 2)
        self.assertEqual(data['result'][0]['operation'], 'add')
        self.assertEqual(data['result'][0]['result'], 5)
        self.assertEqual(data['result'][1]['operation'], 'sub')
        self.assertEqual(data['result'][1]['result'], 3)
        self.assertIsNone(data['error'])

    def test_v1_handle_dict_dataclass_result(self):
        """Test v1.0 dict with dataclass values serialization."""
        request = '{"method": "test.dict_results", "params": [], "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Dict with dataclass values should be serialized
        self.assertIsInstance(data['result'], dict)
        self.assertEqual(data['result']['first']['operation'], 'add')
        self.assertEqual(data['result']['first']['result'], 10)
        self.assertEqual(data['result']['second']['operation'], 'mul')
        self.assertEqual(data['result']['second']['result'], 20)
        self.assertIsNone(data['error'])


class TestJSONRPCV1ErrorHandling(unittest.TestCase):
    """Tests for error handling edge cases in v1.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='1.0')

        # Create a method that raises exception
        class BrokenMethod(Method):
            name = 'broken'

            def execute(self, params: None) -> str:
                raise RuntimeError('Unexpected error!')

        # Create async broken method
        class AsyncBrokenMethod(Method):
            name = 'async_broken'

            async def execute(self, params: None) -> str:
                raise RuntimeError('Async unexpected error!')

        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        self.rpc.register('math', math_group)

        test_group = MethodGroup()
        test_group.register('broken', BrokenMethod())
        test_group.register('async_broken', AsyncBrokenMethod())
        test_group.register('ping', NoParamsMethod())
        self.rpc.register('test', test_group)

    def test_handle_unexpected_exception_returns_internal_error(self):
        """Test handle() with unexpected exception returns InternalError."""
        request = '{"method":"test.broken","params":[],"id":1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        self.assertIsNotNone(data['error'])
        self.assertEqual(data['error']['code'], -32603)  # Internal error
        self.assertIn('Unexpected error', data['error']['message'])

    def test_handle_async_unexpected_exception(self):
        """Test handle_async() with unexpected exception returns InternalError."""
        request = '{"method":"test.async_broken","params":[],"id":1}'
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)

        self.assertIsNotNone(data['error'])
        self.assertEqual(data['error']['code'], -32603)


class TestJSONRPCV1DefensiveExceptionHandling(unittest.TestCase):
    """Backported defensive exception handling tests for JSON-RPC 1.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='1.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        self.rpc.register('math', math_group)

    def test_handle_json_decode_error_v1(self):
        """Test handle() with JSON decode error in v1.0."""
        response = self.rpc.handle('invalid json{')
        data = json.loads(response)

        # v1.0 format: {"result": null, "error": {...}, "id": null}
        self.assertIsNotNone(data['error'])
        self.assertEqual(data['error']['code'], -32700)  # Parse error
        self.assertIn('Invalid JSON', data['error']['message'])

    def test_handle_with_internal_exception_in_dispatcher_v1(self):
        """Test handle() when root_group raises unexpected exception in v1.0."""
        from unittest.mock import patch

        with patch.object(self.rpc._root_group, 'dispatch', side_effect=RuntimeError('v1 dispatcher error')):
            response = self.rpc.handle('{"method":"math.add","params":[1,2],"id":1}')
            data = json.loads(response)

            self.assertIsNotNone(data['error'])
            self.assertEqual(data['error']['code'], -32603)  # Internal error
            self.assertIn('v1 dispatcher error', data['error']['message'])


class TestJSONRPCV1StrictBatchRejection(unittest.TestCase):
    """Tests for batch rejection in handle() / handle_async() (lines 362-364, 394-396)."""

    def setUp(self):
        self.rpc = JSONRPC(version='1.0')
        self.rpc.register('ping', NoParamsMethod())

    def test_v1_batch_rejected_in_sync_handle(self):
        """v1.0 handle() rejects batch (allow_batch=False by default) with -32600."""
        batch = json.dumps([{'method': 'ping', 'params': [], 'id': 1}])
        response = self.rpc.handle(batch)
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32600)
        self.assertEqual(data['error']['message'], 'Batch requests not allowed')

    def test_v1_batch_rejected_in_async_handle(self):
        """v1.0 handle_async() rejects batch (allow_batch=False by default) with -32600."""
        batch = json.dumps([{'method': 'ping', 'params': [], 'id': 1}])
        response = asyncio.run(self.rpc.handle_async(batch))
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32600)
        self.assertEqual(data['error']['message'], 'Batch requests not allowed')


class TestJSONRPCV1OuterExceptionHandler(unittest.TestCase):
    """Tests for TypeError handling in handle() / handle_async().

    json.loads(None) raises TypeError (not JSONDecodeError),
    now caught by the inner except and returned as ParseError -32700.
    """

    def setUp(self):
        self.rpc = JSONRPC(version='1.0')
        self.rpc.register('ping', NoParamsMethod())

    def test_v1_handle_none_returns_parse_error(self):
        """handle(None) triggers TypeError in json.loads → ParseError -32700."""
        response = self.rpc.handle(None)
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32700)

    def test_v1_handle_async_none_returns_parse_error(self):
        """handle_async(None) triggers TypeError in json.loads → ParseError -32700."""
        response = asyncio.run(self.rpc.handle_async(None))
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32700)


if __name__ == '__main__':
    unittest.main()
