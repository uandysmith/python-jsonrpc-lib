"""Comprehensive tests for JSON-RPC 2.0 protocol implementation.

JSON-RPC 2.0 specification: https://www.jsonrpc.org/specification

Key features of 2.0:
- Required "jsonrpc": "2.0" field in requests/responses
- params can be array (positional) or object (named)
- Responses have either "result" or "error" field (not both)
- Notifications omit the "id" field entirely (no response expected)
"""

import asyncio
import json
import unittest
from dataclasses import dataclass, field

from jsonrpc import JSONRPC, MethodGroup
from jsonrpc.method import Method
from tests.fixtures import (
    AddMethod,
    AddParams,
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


class TestJSONRPCV2Protocol(unittest.TestCase):
    """Tests for JSON-RPC 2.0 protocol compliance."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
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

    def test_v2_no_params(self):
        """Test v2.0 with no params (null)."""
        request = '{"method": "ping", "params": null, "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)
        self.assertEqual(data['result'], 'pong')

    def test_v2_missing_params(self):
        """Test v2.0 with missing params field."""
        request = '{"jsonrpc": "2.0", "method": "ping", "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)
        self.assertEqual(data['result'], 'pong')

    def test_v2_notification_without_id(self):
        """Test v2.0 notification (no id field)."""
        request = '{"jsonrpc": "2.0", "method": "ping"}'
        response = self.rpc.handle(request)
        self.assertEqual(response, None)

    def test_v2_notification_with_id_null(self):
        """Test v2.0 with id=null (NOT a notification, should return response)."""
        request = '{"jsonrpc": "2.0", "method": "ping", "id": null}'
        response = self.rpc.handle(request)
        self.assertIsNotNone(response)
        data = json.loads(response)
        self.assertEqual(data['result'], 'pong')
        self.assertEqual(data['id'], None)
        self.assertEqual(data['jsonrpc'], '2.0')

    def test_v2_error_response_format(self):
        """Test v2.0 error response format."""
        request = '{"jsonrpc": "2.0", "method": "nonexistent", "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)
        self.assertEqual('result' in data, False)
        self.assertEqual('error' in data, True)
        self.assertEqual(data['id'], 1)
        self.assertEqual(data['jsonrpc'], '2.0')
        self.assertEqual('code' in data['error'], True)
        self.assertEqual('message' in data['error'], True)

    def test_v2_method_error(self):
        """Test v2.0 response when method raises error."""
        request = '{"jsonrpc": "2.0", "method": "error", "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)
        self.assertEqual('result' in data, False)
        self.assertEqual('error' in data, True)
        self.assertEqual('Intentional error' in data['error']['message'], True)

    def test_v2_optional_params(self):
        """Test v2.0 with optional parameters."""
        request1 = '{"method": "optional", "params": {"required": "value"}, "id": 1}'
        response1 = self.rpc.handle(request1)
        data1 = json.loads(response1)
        self.assertEqual(data1['result'], 'value:default')

        request2 = '{"method": "optional", "params": {"required": "value", "optional": "custom"}, "id": 2}'
        response2 = self.rpc.handle(request2)
        data2 = json.loads(response2)
        self.assertEqual(data2['result'], 'value:custom')

    def test_v2_dataclass_result_serialization(self):
        """Test that v2.0 serializes dataclass results to JSON."""
        request = '{"jsonrpc": "2.0", "method": "dataclass_result", "params": {"a": 5, "b": 3}, "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)
        self.assertEqual(isinstance(data['result'], dict), True)
        self.assertEqual(data['result']['operation'], 'add')
        self.assertEqual(data['result']['result'], 8)
        self.assertEqual('error' in data, False)

    def test_v2_numeric_id(self):
        """Test v2.0 with numeric id."""
        request = '{"jsonrpc": "2.0", "method": "ping", "id": 42}'
        response = self.rpc.handle(request)
        data = json.loads(response)
        self.assertEqual(data['id'], 42)
        self.assertEqual(data['jsonrpc'], '2.0')
        self.assertEqual(data['result'], 'pong')

    def test_v2_nested_dataclass_3_levels(self):
        """Test v2.0 with 3-level nested dataclass (Contact -> Address -> CompanyInfo)."""
        # v2.0 with dict params
        # CompanyInfo(name, founded, Address(street, city, Contact(email, phone)))
        request = """{
            "jsonrpc": "2.0",
            "method": "process_company",
            "params": {
                "name": "TechCorp",
                "founded": 2010,
                "address": {
                    "street": "123 Main St",
                    "city": "San Francisco",
                    "contact": {
                        "email": "info@techcorp.com",
                        "phone": "+1-555-0100"
                    }
                }
            },
            "id": 1
        }"""
        response = self.rpc.handle(request)
        data = json.loads(response)

        # v2.0 response format
        self.assertEqual('error' in data, False)
        self.assertEqual(data['id'], 1)
        self.assertEqual(data['jsonrpc'], '2.0')

        # Verify nested dataclass was correctly parsed and processed
        expected = 'TechCorp founded in 2010, located at 123 Main St, San Francisco, contact: info@techcorp.com'
        self.assertEqual(data['result'], expected)

    def test_v2_nested_dataclass_dict_params(self):
        """Test v2.0 with 3-level nested dataclass using dict params (lenient)."""
        # Our implementation is lenient and accepts dict params even for v2.0
        request = """{
            "jsonrpc": "2.0",
            "method": "process_company",
            "params": {
                "name": "StartupXYZ",
                "founded": 2020,
                "address": {
                    "street": "456 Tech Blvd",
                    "city": "Austin",
                    "contact": {
                        "email": "hello@startupxyz.com",
                        "phone": "+1-555-0200"
                    }
                }
            },
            "id": 2
        }"""
        response = self.rpc.handle(request)
        data = json.loads(response)

        self.assertNotIn('error', data)
        self.assertEqual(data['id'], 2)

        expected = 'StartupXYZ founded in 2020, located at 456 Tech Blvd, Austin, contact: hello@startupxyz.com'
        self.assertEqual(data['result'], expected)

    def test_v2_nested_dataclass_missing_field(self):
        """Test v2.0 with 3-level nested dataclass with missing field."""
        # Missing the 'phone' field in Contact
        request = """{
            "jsonrpc": "2.0",
            "method": "process_company",
            "params": {
                "name": "IncompleteInc",
                "founded": 2015,
                "address": {
                    "street": "789 Startup Ave",
                    "city": "Boston",
                    "contact": {
                        "email": "contact@incomplete.com"
                    }
                }
            },
            "id": 3
        }"""
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Should return error for missing required field
        self.assertEqual('result' in data, False)
        self.assertEqual('error' in data, True)
        self.assertEqual(data['id'], 3)
        self.assertEqual('Missing required parameter' in data['error']['message'], True)

    def test_v2_nested_dataclass_wrong_type(self):
        """Test v2.0 with 3-level nested dataclass with wrong type."""
        # 'founded' should be int, not string
        request = """{
            "jsonrpc": "2.0",
            "method": "process_company",
            "params": {
                "name": "BadTypeCo",
                "founded": "not_a_year",
                "address": {
                    "street": "100 Error St",
                    "city": "Seattle",
                    "contact": {
                        "email": "bad@type.com",
                        "phone": "+1-555-0300"
                    }
                }
            },
            "id": 4
        }"""
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Should return error for type validation
        self.assertEqual('result' in data, False)
        self.assertEqual('error' in data, True)
        self.assertEqual(data['id'], 4)

    def test_v2_parse_error(self):
        """Test v2.0 with invalid JSON."""
        request = '{invalid json'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Should return parse error
        self.assertEqual('result' in data, False)
        self.assertEqual('error' in data, True)
        self.assertEqual(data['error']['code'], -32700)
        self.assertEqual('Invalid JSON' in data['error']['message'], True)

    def test_v2_invalid_request_missing_method(self):
        """Test v2.0 with missing method field."""
        request = '{"id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Should return invalid request error
        self.assertEqual('result' in data, False)
        self.assertEqual('error' in data, True)
        self.assertEqual(data['error']['code'], -32600)

    def test_v2_list_methods(self):
        """Test listing all methods in v2.0."""
        methods = self.rpc.list_methods()
        self.assertIn('math.add', methods)
        self.assertIn('math.subtract', methods)
        self.assertIn('math.multiply', methods)
        self.assertIn('ping', methods)
        self.assertIn('echo', methods)
        self.assertIn('process_company', methods)


class TestJSONRPCV2Async(unittest.TestCase):
    """Tests for async methods with JSON-RPC 1.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        self.rpc.register('async_test', AsyncMethod())
        self.rpc.register('ping', NoParamsMethod())

    def test_v2_async_method(self):
        """Test v2.0 with async method using handle_async()."""
        request = '{"jsonrpc": "2.0", "method": "async_test", "id": 1}'
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)

        # v2.0 response format
        self.assertEqual(data['result'], 'async_result')
        self.assertEqual('error' in data, False)
        self.assertEqual(data['id'], 1)
        self.assertEqual(data['jsonrpc'], '2.0')


class TestJSONRPCV2Batch(unittest.TestCase):
    """Tests for batch requests with JSON-RPC 1.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        math_group.register('subtract', SubtractMethod())
        self.rpc.register('math', math_group)

        self.rpc.register('ping', NoParamsMethod())

    def test_v2_batch_with_errors(self):
        """Test v2.0 batch with some errors."""
        request = """[
            {"jsonrpc": "2.0", "method": "math.add", "params": {"a": 1, "b": 2}, "id": 1},
            {"jsonrpc": "2.0", "method": "nonexistent", "id": 2},
            {"jsonrpc": "2.0", "method": "math.add", "params": {"a": 3, "b": 4}, "id": 3}
        ]"""
        response = self.rpc.handle(request)
        data = json.loads(response)

        self.assertEqual(len(data), 3)

        # First: success
        self.assertEqual(data[0]['result'], 3)
        self.assertEqual('error' in data[0], False)

        # Second: error
        self.assertEqual('result' in data[1], False)
        self.assertEqual('error' in data[1], True)

        # Third: success
        self.assertEqual(data[2]['result'], 7)
        self.assertEqual('error' in data[2], False)

    def test_v2_empty_batch_error(self):
        """Test v2.0 with empty batch array."""
        request = '[]'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Empty batch is an error
        self.assertEqual('result' in data, False)
        self.assertEqual('error' in data, True)


class TestJSONRPCV2CallMethod(unittest.TestCase):
    """Tests for internal call_method() with v2.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        self.rpc.register('math', math_group)

    def test_v2_call_method_direct(self):
        """Test call_method() works same way in v2.0 and v2.0."""
        # call_method returns raw result, not JSON response
        result = self.rpc.call_method('math.add', {'a': 5, 'b': 3})
        self.assertEqual(result, 8)

        # Also works with dict params
        result2 = self.rpc.call_method('math.add', {'a': 10, 'b': 20})
        self.assertEqual(result2, 30)


class TestJSONRPCV2InternalCalls(unittest.TestCase):
    """Tests for internal method-to-method calls with v2.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        self.rpc.register('math', math_group)

        calc_group = MethodGroup()
        calc_group.register('double_add', InternalCallMethod())
        self.rpc.register('calc', calc_group)

    def test_v2_internal_method_call(self):
        """Test v2.0 method calling another method internally."""
        # InternalCallMethod calls math.add and doubles the result
        result = self.rpc.call_method('calc.double_add', {'a': 5, 'b': 3})
        self.assertEqual(result, 16)  # (5 + 3) * 2

    def test_v2_internal_method_call_via_handle(self):
        """Test v2.0 internal method call via handle()."""
        request = '{"jsonrpc": "2.0", "method": "calc.double_add", "params": {"a": 10, "b": 5}, "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        self.assertEqual(data['result'], 30)  # (10 + 5) * 2
        self.assertEqual('error' in data, False)
        self.assertEqual(data['id'], 1)


class TestJSONRPCV2NestedGroups(unittest.TestCase):
    """Tests for nested group prefixes with v2.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')

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
        admin_group.register('ping', NoParamsMethod())  # ping

        users_group = MethodGroup()
        users_group.register('admin', admin_group)

        v1_group = MethodGroup()
        v1_group.register('users', users_group)

        api_group = MethodGroup()
        api_group.register('v1', v1_group)

        self.rpc.register('api', api_group)

    def test_v2_nested_group_methods_listed(self):
        """Test v2.0 lists nested group methods."""
        methods = self.rpc.list_methods()
        self.assertIn('utils.text.format.echo', methods)
        self.assertIn('api.v1.users.admin.ping', methods)

    def test_v2_call_nested_method(self):
        """Test v2.0 call nested method directly."""
        result = self.rpc.call_method('utils.text.format.echo', {'message': 'hello'})
        self.assertEqual(result, 'hello')

    def test_v2_handle_nested_method(self):
        """Test v2.0 handle nested method via JSON-RPC."""
        request = '{"jsonrpc": "2.0", "method": "utils.text.format.echo", "params": {"message": "test"}, "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        self.assertEqual(data['result'], 'test')
        self.assertEqual('error' in data, False)

    def test_v2_handle_deeply_nested_method(self):
        """Test v2.0 handle deeply nested method."""
        request = '{"jsonrpc": "2.0", "method": "api.v1.users.admin.ping", "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        self.assertEqual(data['result'], 'pong')
        self.assertEqual('error' in data, False)


class TestJSONRPCV2ResultValidation(unittest.TestCase):
    """Tests for result type validation with v2.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        math = MethodGroup()
        math.register('typed_add', TypedAddMethod())
        math.register('wrong_type', WrongTypeMethod())
        self.rpc.register('math', math)

    def test_v2_call_method_without_validation(self):
        """Test v2.0 succeeds with wrong type when validation off."""
        result = self.rpc.call_method('math.wrong_type')
        self.assertEqual(result, 'not an int')

    def test_v2_call_method_with_per_call_validation(self):
        """Test v2.0 succeeds with correct type and validation."""
        result = self.rpc.call_method('math.typed_add', {'a': 1, 'b': 2}, validate_result=True)
        self.assertEqual(result, 3)

    def test_v2_call_method_with_per_call_validation_wrong_type(self):
        """Test v2.0 fails with wrong type and validation."""
        from jsonrpc import InvalidResultError

        with self.assertRaises(InvalidResultError):
            self.rpc.call_method('math.wrong_type', validate_result=True)

    def test_v2_global_validation_validates_results(self):
        """Test v2.0 global validation works."""
        from jsonrpc import InvalidResultError

        rpc = JSONRPC(version='2.0', validate_results=True)
        math = MethodGroup()
        math.register('typed_add', TypedAddMethod())
        math.register('wrong_type', WrongTypeMethod())
        rpc.register('math', math)

        # Correct type should work
        result = rpc.call_method('math.typed_add', {'a': 1, 'b': 2})
        self.assertEqual(result, 3)

        # Wrong type should fail
        with self.assertRaises(InvalidResultError):
            rpc.call_method('math.wrong_type')

    def test_v2_handle_with_validation_error(self):
        """Test v2.0 handle() returns error for validation failure."""
        rpc = JSONRPC(version='2.0', validate_results=True)
        math = MethodGroup()
        math.register('wrong_type', WrongTypeMethod())
        rpc.register('math', math)

        request = '{"jsonrpc": "2.0", "method": "math.wrong_type", "id": 1}'
        response = rpc.handle(request)
        data = json.loads(response)

        # Should return error response
        self.assertEqual('result' in data, False)
        self.assertEqual('error' in data, True)
        self.assertEqual(data['error']['code'], -32001)  # InvalidResultError


class TestJSONRPCV2DataclassResult(unittest.TestCase):
    """Tests for methods returning dataclass results with v2.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
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

    def test_v2_handle_async_dataclass_result(self):
        """Test v2.0 async method with dataclass result via handle_async()."""
        request = '{"jsonrpc": "2.0", "method": "test.async_dataclass_add", "params": {"a": 7, "b": 2}, "id": 1}'
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)

        # Should serialize dataclass to dict
        self.assertEqual(data['result']['operation'], 'add')
        self.assertEqual(data['result']['result'], 9)
        self.assertEqual('error' in data, False)
        self.assertEqual(data['id'], 1)

    def test_v2_call_method_async_returns_dataclass_object(self):
        """Test v2.0 call_method_async() returns dataclass object."""
        result = asyncio.run(self.rpc.call_method_async('test.async_dataclass_add', {'a': 20, 'b': 10}))

        # Should return actual dataclass instance
        self.assertIsInstance(result, MathResult)
        self.assertEqual(result.operation, 'add')
        self.assertEqual(result.result, 30)

    def test_v2_handle_nested_dataclass_result(self):
        """Test v2.0 nested dataclass result serialization."""
        request = '{"jsonrpc": "2.0", "method": "test.nested_user", "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Nested dataclass should be serialized to nested dict
        self.assertEqual(data['result']['name'], 'Jakub')
        self.assertEqual(data['result']['age'], 25)
        self.assertEqual(data['result']['address']['city'], 'Krakow')
        self.assertEqual(data['result']['address']['country'], 'Poland')
        self.assertEqual('error' in data, False)

    def test_v2_handle_list_dataclass_result(self):
        """Test v2.0 list of dataclass results serialization."""
        request = '{"jsonrpc": "2.0", "method": "test.list_results", "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # List of dataclass should be serialized to list of dicts
        self.assertIsInstance(data['result'], list)
        self.assertEqual(len(data['result']), 2)
        self.assertEqual(data['result'][0]['operation'], 'add')
        self.assertEqual(data['result'][0]['result'], 5)
        self.assertEqual(data['result'][1]['operation'], 'sub')
        self.assertEqual(data['result'][1]['result'], 3)
        self.assertEqual('error' in data, False)

    def test_v2_handle_dict_dataclass_result(self):
        """Test v2.0 dict with dataclass values serialization."""
        request = '{"jsonrpc": "2.0", "method": "test.dict_results", "id": 1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        # Dict with dataclass values should be serialized
        self.assertIsInstance(data['result'], dict)
        self.assertEqual(data['result']['first']['operation'], 'add')
        self.assertEqual(data['result']['first']['result'], 10)
        self.assertEqual(data['result']['second']['operation'], 'mul')
        self.assertEqual(data['result']['second']['result'], 20)
        self.assertEqual('error' in data, False)


class TestJSONRPCV2AsyncBatch(unittest.TestCase):
    """Tests for async batch request handling in v2.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        math_group.register('subtract', SubtractMethod())
        self.rpc.register('math', math_group)

        async_group = MethodGroup()
        async_group.register('async_test', AsyncMethod())
        async_group.register('async_dataclass_add', AsyncDataclassResultMethod())
        self.rpc.register('async', async_group)

    def test_async_batch_multiple_async_methods(self):
        """Test async batch with multiple async methods."""
        request = json.dumps(
            [
                {'jsonrpc': '2.0', 'method': 'async.async_test', 'id': 1},
                {'jsonrpc': '2.0', 'method': 'async.async_dataclass_add', 'params': {'a': 10, 'b': 5}, 'id': 2},
            ]
        )
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)

        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['id'], 1)
        self.assertEqual(data[0]['result'], 'async_result')
        self.assertEqual(data[1]['id'], 2)
        self.assertEqual(data[1]['result']['result'], 15)

    def test_async_batch_mixed_sync_async(self):
        """Test async batch with mixed sync and async methods."""
        request = json.dumps(
            [
                {'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 1, 'b': 2}, 'id': 1},
                {'jsonrpc': '2.0', 'method': 'async.async_test', 'id': 2},
            ]
        )
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)

        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['result'], 3)
        self.assertEqual(data[1]['result'], 'async_result')

    def test_async_batch_all_notifications(self):
        """Test async batch with all notifications returns None."""
        request = json.dumps(
            [
                {'jsonrpc': '2.0', 'method': 'async.async_test'},
                {'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 1, 'b': 2}},
            ]
        )
        response = asyncio.run(self.rpc.handle_async(request))

        self.assertIsNone(response)

    def test_async_batch_mixed_notifications_and_requests(self):
        """Test async batch with mixed notifications and regular requests."""
        request = json.dumps(
            [
                {'jsonrpc': '2.0', 'method': 'async.async_test'},  # notification
                {'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 1, 'b': 2}, 'id': 1},  # request
            ]
        )
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)

        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)  # Only non-notification
        self.assertEqual(data[0]['id'], 1)
        self.assertEqual(data[0]['result'], 3)

    def test_async_batch_empty_array(self):
        """Test async batch with empty array returns error."""
        request = '[]'
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)

        self.assertIn('error', data)
        self.assertEqual(data['error']['code'], -32600)  # Invalid Request

    def test_async_batch_with_errors(self):
        """Test async batch where some requests have errors."""
        request = json.dumps(
            [
                {'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 1, 'b': 2}, 'id': 1},
                {'jsonrpc': '2.0', 'method': 'nonexistent', 'id': 2},
                {'jsonrpc': '2.0', 'method': 'async.async_test', 'id': 3},
            ]
        )
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)

        self.assertEqual(len(data), 3)
        # First should succeed
        self.assertEqual(data[0]['result'], 3)
        # Second should have error
        self.assertEqual('error' in data[1], True)
        self.assertEqual(data[1]['error']['code'], -32601)
        # Third should succeed
        self.assertEqual(data[2]['result'], 'async_result')


class TestJSONRPCV2ErrorHandling(unittest.TestCase):
    """Tests for error handling edge cases in v2.0."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')

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

    def test_notification_errors_suppressed(self):
        """Test all notification error types are suppressed."""
        test_cases = [
            ('test.broken', None, 'exception'),  # method throws error
            ('test.ping', {'invalid': 'params'}, 'invalid_params'),  # wrong params
            ('test.nonexistent', None, 'not_found'),  # method not found
        ]

        for method, params, error_type in test_cases:
            with self.subTest(error_type=error_type):
                req = {'jsonrpc': '2.0', 'method': method}
                if params is not None:
                    req['params'] = params
                request = json.dumps(req)
                response = self.rpc.handle(request)
                self.assertIsNone(response)

    def test_async_notification_error_suppressed(self):
        """Test async notification with error returns None (error suppressed)."""
        request = '{"jsonrpc":"2.0","method":"test.async_broken"}'
        response = asyncio.run(self.rpc.handle_async(request))

        # Should return None (notification, no response even on error)
        self.assertIsNone(response)

    def test_handle_unexpected_exception_returns_internal_error(self):
        """Test handle() with unexpected exception returns InternalError."""
        request = '{"jsonrpc":"2.0","method":"test.broken","id":1}'
        response = self.rpc.handle(request)
        data = json.loads(response)

        self.assertEqual('error' in data, True)
        self.assertEqual(data['error']['code'], -32603)  # Internal error
        # Sanitized by default: the exception text is logged, not sent to the caller.
        self.assertEqual(data['error']['message'], 'Internal error')

    def test_handle_async_unexpected_exception(self):
        """Test handle_async() with unexpected exception returns InternalError."""
        request = '{"jsonrpc":"2.0","method":"test.async_broken","id":1}'
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)

        self.assertEqual('error' in data, True)
        self.assertEqual(data['error']['code'], -32603)


class TestJSONRPCV2DefensiveExceptionHandling(unittest.TestCase):
    """Tests for defensive exception handlers in JSONRPC core."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        self.rpc.register('math', math_group)

    def test_handle_batch_processing(self):
        """Test batch request processing works correctly."""
        # This test verifies batch handling works (lines 136, 178 are defensive code)
        batch_request = (
            '[{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1},'
            '{"jsonrpc":"2.0","method":"math.add","params":{"a":3,"b":4},"id":2}]'
        )
        response = self.rpc.handle(batch_request)
        data = json.loads(response)

        # Verify batch response
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['result'], 3)
        self.assertEqual(data[1]['result'], 7)

    def test_handle_json_decode_error(self):
        """Test handle() with JSON decode error - lines 76-81."""
        response = self.rpc.handle('invalid json{')
        data = json.loads(response)

        self.assertEqual('error' in data, True)
        self.assertEqual(data['error']['code'], -32700)  # Parse error
        self.assertEqual('Invalid JSON' in data['error']['message'], True)

    def test_handle_async_json_decode_error(self):
        """Test handle_async() with JSON decode error - lines 107-112."""
        response = asyncio.run(self.rpc.handle_async('invalid json{'))
        data = json.loads(response)

        self.assertEqual('error' in data, True)
        self.assertEqual(data['error']['code'], -32700)
        self.assertEqual('Invalid JSON' in data['error']['message'], True)

    def test_handle_with_internal_exception_in_dispatcher(self):
        """Test handle() when root_group raises unexpected exception."""
        from unittest.mock import patch

        # Patch root_group to raise unexpected exception
        with patch.object(self.rpc._root_group, 'dispatch', side_effect=RuntimeError('dispatcher error')):
            response = self.rpc.handle('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}')
            data = json.loads(response)

            self.assertEqual('error' in data, True)
            self.assertEqual(data['error']['code'], -32603)  # Internal error
            self.assertEqual(data['error']['message'], 'Internal error')

    def test_handle_async_with_internal_exception_in_dispatcher(self):
        """Test handle_async() when root_group raises unexpected exception."""
        from unittest.mock import patch

        with patch.object(self.rpc._root_group, 'dispatch_async', side_effect=RuntimeError('async dispatcher error')):
            response = asyncio.run(
                self.rpc.handle_async('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}')
            )
            data = json.loads(response)

            self.assertEqual('error' in data, True)
            self.assertEqual(data['error']['code'], -32603)
            self.assertEqual(data['error']['message'], 'Internal error')

    def test_handle_outer_except_catches_exception_from_handle_single(self):
        """handle() outer except fires when _handle_single itself raises unexpectedly."""
        from unittest.mock import patch

        # Patch _handle_single to raise — bypasses all internal error handling.
        with patch.object(self.rpc, '_handle_single', side_effect=RuntimeError('internal crash')):
            response = self.rpc.handle('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}')
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)
        self.assertEqual(data['error']['message'], 'Internal error')

    def test_handle_async_outer_except_catches_exception_from_handle_single_async(self):
        """handle_async() outer except fires when _handle_single_async itself raises unexpectedly."""
        from unittest.mock import patch

        with patch.object(self.rpc, '_handle_single_async', side_effect=RuntimeError('async crash')):
            response = asyncio.run(
                self.rpc.handle_async('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}')
            )
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)
        self.assertEqual(data['error']['message'], 'Internal error')


class TestJSONRPCV2SyncBatchEdgeCases(unittest.TestCase):
    """Tests for sync _handle_batch() edge cases (line 518)."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        self.rpc.register('ping', NoParamsMethod())

    def test_v2_sync_batch_all_notifications_returns_none(self):
        """Sync batch where ALL items are notifications (no id) returns None."""
        batch = json.dumps(
            [
                {'jsonrpc': '2.0', 'method': 'ping'},
                {'jsonrpc': '2.0', 'method': 'ping'},
            ]
        )
        result = self.rpc.handle(batch)
        self.assertIsNone(result)

    def test_v2_sync_batch_rejected_when_allow_batch_false(self):
        """handle() with allow_batch=False rejects batch with -32600."""
        rpc = JSONRPC(version='2.0', allow_batch=False)
        rpc.register('ping', NoParamsMethod())
        batch = json.dumps([{'jsonrpc': '2.0', 'method': 'ping', 'id': 1}])
        response = rpc.handle(batch)
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32600)
        self.assertEqual(data['error']['message'], 'Batch requests not allowed')

    def test_v2_async_batch_rejected_when_allow_batch_false(self):
        """handle_async() with allow_batch=False rejects batch with -32600."""
        import asyncio

        rpc = JSONRPC(version='2.0', allow_batch=False)
        rpc.register('ping', NoParamsMethod())
        batch = json.dumps([{'jsonrpc': '2.0', 'method': 'ping', 'id': 1}])
        response = asyncio.run(rpc.handle_async(batch))
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32600)
        self.assertEqual(data['error']['message'], 'Batch requests not allowed')


class TestJSONRPCV2BatchLimits(unittest.TestCase):
    """Tests for max_batch and max_concurrent parameters."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        self.rpc.register('ping', NoParamsMethod())
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        self.rpc.register('math', math_group)

    # --- max_batch ---

    def test_max_batch_rejects_oversized_batch(self):
        """Batch exceeding max_batch is rejected with -32600."""
        rpc = JSONRPC(version='2.0', max_batch=3)
        rpc.register('ping', NoParamsMethod())
        batch = json.dumps([{'jsonrpc': '2.0', 'method': 'ping', 'id': i} for i in range(4)])
        response = rpc.handle(batch)
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32600)
        self.assertIn('Batch too large', data['error']['message'])
        self.assertIn('4', data['error']['message'])
        self.assertIn('3', data['error']['message'])

    def test_max_batch_allows_batch_at_limit(self):
        """Batch exactly at max_batch is processed normally."""
        rpc = JSONRPC(version='2.0', max_batch=3)
        rpc.register('ping', NoParamsMethod())
        batch = json.dumps([{'jsonrpc': '2.0', 'method': 'ping', 'id': i} for i in range(3)])
        response = rpc.handle(batch)
        data = json.loads(response)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 3)

    def test_max_batch_unlimited(self):
        """max_batch=-1 disables the limit."""
        rpc = JSONRPC(version='2.0', max_batch=-1)
        rpc.register('ping', NoParamsMethod())
        batch = json.dumps([{'jsonrpc': '2.0', 'method': 'ping', 'id': i} for i in range(200)])
        response = rpc.handle(batch)
        data = json.loads(response)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 200)

    def test_max_batch_async_rejects_oversized_batch(self):
        """Async batch exceeding max_batch is also rejected."""
        rpc = JSONRPC(version='2.0', max_batch=3)
        rpc.register('ping', NoParamsMethod())
        batch = json.dumps([{'jsonrpc': '2.0', 'method': 'ping', 'id': i} for i in range(5)])
        response = asyncio.run(rpc.handle_async(batch))
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32600)

    # --- max_concurrent ---

    def test_max_concurrent_default_uses_cpu_count(self):
        """Default max_concurrent is None (resolved to os.cpu_count() at runtime)."""
        rpc = JSONRPC(version='2.0')
        self.assertIsNone(rpc.max_concurrent)

    def test_max_concurrent_throttles_async_batch(self):
        """Async batch completes correctly even with max_concurrent=1."""
        import asyncio as _asyncio

        rpc = JSONRPC(version='2.0', max_concurrent=1)
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        rpc.register('math', math_group)

        batch = json.dumps(
            [{'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': i, 'b': i}, 'id': i} for i in range(1, 6)]
        )
        response = _asyncio.run(rpc.handle_async(batch))
        data = json.loads(response)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 5)
        results = {r['id']: r['result'] for r in data}
        for i in range(1, 6):
            self.assertEqual(results[i], i + i)

    def test_max_concurrent_unlimited(self):
        """max_concurrent=-1 disables throttling (old behaviour)."""
        rpc = JSONRPC(version='2.0', max_concurrent=-1)
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        rpc.register('math', math_group)

        batch = json.dumps(
            [{'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': i, 'b': 1}, 'id': i} for i in range(1, 11)]
        )
        response = asyncio.run(rpc.handle_async(batch))
        data = json.loads(response)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 10)


class TestJSONRPCV2SerializationHooks(unittest.TestCase):
    """Tests for overridable deserialize / serialize / serialize_result hooks."""

    def _make_rpc(self, cls=None):
        """Create an RPC instance (default or subclass) with math.add registered."""
        rpc = (cls or JSONRPC)(version='2.0')
        g = MethodGroup()
        g.register('add', AddMethod())
        rpc.register('math', g)
        return rpc

    # --- deserialize ---

    def test_deserialize_override_is_called(self):
        """Custom deserialize() is invoked instead of json.loads."""
        calls = []

        class TrackingRPC(JSONRPC):
            def deserialize(self, data):
                calls.append(data)
                return super().deserialize(data)

        rpc = self._make_rpc(TrackingRPC)
        rpc.handle('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}')
        self.assertEqual(len(calls), 1)

    def test_deserialize_value_error_returns_parse_error(self):
        """deserialize() raising ValueError maps to ParseError (-32700)."""

        class BrokenDeserializeRPC(JSONRPC):
            def deserialize(self, data):
                raise ValueError('custom lib decode error')

        rpc = self._make_rpc(BrokenDeserializeRPC)
        response = json.loads(rpc.handle('anything'))
        self.assertEqual(response['error']['code'], -32700)
        self.assertIn('custom lib decode error', response['error']['message'])

    def test_deserialize_async_override_is_called(self):
        """Custom deserialize() is also invoked by handle_async()."""
        calls = []

        class TrackingRPC(JSONRPC):
            def deserialize(self, data):
                calls.append(data)
                return super().deserialize(data)

        rpc = self._make_rpc(TrackingRPC)
        asyncio.run(rpc.handle_async('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}'))
        self.assertEqual(len(calls), 1)

    # --- serialize ---

    def test_serialize_override_receives_response_dict(self):
        """Custom serialize() receives the fully-built response dict."""
        captured = []

        class TrackingRPC(JSONRPC):
            def serialize(self, data, **kwargs):
                captured.append(data)
                return super().serialize(data, **kwargs)

        rpc = self._make_rpc(TrackingRPC)
        rpc.handle('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}')
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]['result'], 3)

    def test_deserialize_and_serialize_together(self):
        """Overriding both hooks produces correct round-trip result."""
        import json as _json

        class RoundTripRPC(JSONRPC):
            def deserialize(self, data):
                return _json.loads(data)

            def serialize(self, data, **kwargs):
                return _json.dumps(data, **kwargs)

        rpc = self._make_rpc(RoundTripRPC)
        response = rpc.handle('{"jsonrpc":"2.0","method":"math.add","params":{"a":10,"b":5},"id":99}')
        data = json.loads(response)
        self.assertEqual(data['result'], 15)
        self.assertEqual(data['id'], 99)

    # --- serialize fallback paths ---

    def _failing_serialize(self, fail_on_call=1):
        """Return a serialize side-effect that raises TypeError on the Nth call."""
        import json as _json

        call_count = [0]

        def side_effect(data, **kwargs):
            call_count[0] += 1
            if call_count[0] == fail_on_call:
                raise TypeError('cannot serialize custom type')
            return _json.dumps(data, **kwargs)

        return side_effect

    def test_handle_single_serialize_failure_returns_internal_error(self):
        """_handle_single: TypeError from serialize() falls back to InternalError response."""
        from unittest.mock import patch

        rpc = self._make_rpc()
        with patch.object(rpc, 'serialize', side_effect=self._failing_serialize(fail_on_call=1)):
            response = rpc.handle('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":42}')
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)
        self.assertEqual(data['error']['message'], 'Internal error')
        self.assertEqual(data['id'], 42)

    def test_handle_single_async_serialize_failure_returns_internal_error(self):
        """_handle_single_async: TypeError from serialize() falls back to InternalError response."""
        from unittest.mock import patch

        rpc = self._make_rpc()
        with patch.object(rpc, 'serialize', side_effect=self._failing_serialize(fail_on_call=1)):
            response = asyncio.run(
                rpc.handle_async('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":42}')
            )
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)
        self.assertEqual(data['error']['message'], 'Internal error')

    def test_handle_batch_serialize_failure_keeps_the_array_and_the_ids(self):
        """_handle_batch: a batch-level serialize() failure is retried per entry.

        The methods in the batch have already run. Collapsing the array into one
        id-less error object destroys every receipt, and a client that gets no
        receipt can only retry -- re-executing everything that already committed.
        """
        from unittest.mock import patch

        rpc = self._make_rpc()
        batch = json.dumps(
            [
                {'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 1, 'b': 2}, 'id': 1},
                {'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 3, 'b': 4}, 'id': 2},
            ]
        )
        with patch.object(rpc, 'serialize', side_effect=self._failing_serialize(fail_on_call=1)):
            response = rpc.handle(batch)
        data = json.loads(response)

        self.assertIsInstance(data, list)
        self.assertEqual([entry['id'] for entry in data], [1, 2])
        self.assertEqual([entry['result'] for entry in data], [3, 7])

    def test_handle_batch_gives_up_only_when_even_the_repaired_batch_fails(self):
        """Last-resort arm: a serialize() override that fails on everything.

        The per-entry retry rebuilds the batch out of plain error envelopes. If
        those cannot be serialized either, there is nothing left to preserve and
        the caller gets a single error.
        """
        from unittest.mock import patch

        rpc = self._make_rpc()
        batch = json.dumps(
            [
                {'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 1, 'b': 2}, 'id': 1},
                {'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 3, 'b': 4}, 'id': 2},
            ]
        )

        calls = [0]
        real_serialize = rpc.serialize

        def always_failing_except_the_last(data, **kwargs):
            calls[0] += 1
            if calls[0] <= 4:  # batch, both entries, repaired batch
                raise TypeError('cannot serialize anything')
            return real_serialize(data, **kwargs)

        with patch.object(rpc, 'serialize', side_effect=always_failing_except_the_last):
            response = rpc.handle(batch)

        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)
        self.assertIsNone(data['id'])

    def test_handle_batch_async_serialize_failure_keeps_the_array_and_the_ids(self):
        """_handle_batch_async: same per-entry retry as the synchronous path."""
        from unittest.mock import patch

        rpc = self._make_rpc()
        batch = json.dumps(
            [
                {'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 1, 'b': 2}, 'id': 1},
                {'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 3, 'b': 4}, 'id': 2},
            ]
        )
        with patch.object(rpc, 'serialize', side_effect=self._failing_serialize(fail_on_call=1)):
            response = asyncio.run(rpc.handle_async(batch))
        data = json.loads(response)

        self.assertIsInstance(data, list)
        self.assertEqual([entry['id'] for entry in data], [1, 2])
        self.assertEqual([entry['result'] for entry in data], [3, 7])


class TestV2Logging(unittest.TestCase):
    """Tests for logging behavior."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')

        class BrokenMethod(Method):
            def execute(self, params: None) -> str:
                raise RuntimeError('Unexpected error!')

        self.rpc.register('broken', BrokenMethod())

    def test_notification_error_logged_at_warning(self):
        """Suppressed notification errors are logged at WARNING level.

        DEBUG made them invisible under any normal production log configuration,
        and the wire says nothing about a notification by design, so this record
        is the only evidence the call failed.
        """
        with self.assertLogs('jsonrpc-lib', level='WARNING') as cm:
            request = '{"jsonrpc":"2.0","method":"broken"}'
            response = self.rpc.handle(request)

        self.assertIsNone(response)
        self.assertTrue(any('Notification failed' in msg for msg in cm.output))

    def test_unhandled_exception_logged_at_error(self):
        """Unhandled exceptions in method dispatch are logged at ERROR level."""
        with self.assertLogs('jsonrpc-lib', level='ERROR') as cm:
            request = '{"jsonrpc":"2.0","method":"broken","id":1}'
            response = self.rpc.handle(request)

        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)
        self.assertTrue(any('Unhandled exception' in msg for msg in cm.output))

    def test_async_notification_error_logged_at_warning(self):
        """Suppressed async notification errors are logged at WARNING level."""

        class AsyncBrokenMethod(Method):
            async def execute(self, params: None) -> str:
                raise RuntimeError('Async error!')

        rpc = JSONRPC(version='2.0')
        rpc.register('async_broken', AsyncBrokenMethod())

        with self.assertLogs('jsonrpc-lib', level='WARNING') as cm:
            request = '{"jsonrpc":"2.0","method":"async_broken"}'
            response = asyncio.run(rpc.handle_async(request))

        self.assertIsNone(response)
        self.assertTrue(any('Notification failed' in msg for msg in cm.output))


@dataclass
class AmountParams:
    """A float the caller controls, checked against a host-side limit."""

    amount: float


LIMIT = 1000.0


class TransferMethod(Method):
    """The shape of a host-side bound check."""

    def execute(self, params: AmountParams) -> float:
        if params.amount > LIMIT:
            raise ValueError('over limit')
        return params.amount


class InfiniteResultMethod(Method):
    def execute(self, params: None) -> float:
        return float('inf')


class AdminDeleteMethod(Method):
    def execute(self, params: None) -> str:
        return 'deleted'


def strict_loads(text):
    """json.loads() that refuses the non-standard constants, like other stacks do."""

    def reject(name):
        raise ValueError(f'not valid JSON: {name}')

    return json.loads(text, parse_constant=reject)


class TestNonFiniteFloatsInbound(unittest.TestCase):
    def setUp(self):
        self.rpc = JSONRPC()
        self.rpc.register('transfer', TransferMethod())

    def test_nan_literal_is_refused_as_a_parse_error(self):
        """`nan > limit` is False, so a NaN slips through every `if x > limit` guard."""
        response = self.rpc.handle('{"jsonrpc":"2.0","method":"transfer","params":{"amount":NaN},"id":1}')
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32700)

    def test_infinity_literal_is_refused_as_a_parse_error(self):
        response = self.rpc.handle('{"jsonrpc":"2.0","method":"transfer","params":{"amount":Infinity},"id":1}')
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32700)

    def test_overflowing_literal_is_refused_as_invalid_params(self):
        """1e400 is legal JSON that json.loads() turns into inf.

        parse_constant never sees it - there is no NaN or Infinity token in the
        body - so the float check has to catch it.
        """
        response = self.rpc.handle('{"jsonrpc":"2.0","method":"transfer","params":{"amount":1e400},"id":1}')
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32602)

    def test_ordinary_floats_still_pass(self):
        response = self.rpc.handle('{"jsonrpc":"2.0","method":"transfer","params":{"amount":12.5},"id":1}')
        self.assertEqual(json.loads(response)['result'], 12.5)

    def test_integers_are_still_accepted_for_a_float_field(self):
        response = self.rpc.handle('{"jsonrpc":"2.0","method":"transfer","params":{"amount":12},"id":1}')
        self.assertEqual(json.loads(response)['result'], 12)


class TestNonFiniteFloatsOutbound(unittest.TestCase):
    def test_infinite_result_does_not_emit_invalid_json(self):
        """A response carrying the bare token Infinity is not valid JSON.

        json.dumps() emits it happily, so the corruption is silent server-side
        and only detonates in the caller's parser.
        """
        rpc = JSONRPC()
        rpc.register('inf', InfiniteResultMethod())

        response = rpc.handle('{"jsonrpc":"2.0","method":"inf","id":1}')

        strict_loads(response)  # must not raise
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)

    def test_serialize_refuses_a_non_finite_float(self):
        rpc = JSONRPC()
        with self.assertRaises(ValueError):
            rpc.serialize({'result': float('nan')})

    def test_serialize_still_produces_the_usual_separators(self):
        rpc = JSONRPC()
        self.assertEqual(rpc.serialize({'a': 1, 'b': [1, 2]}), '{"a": 1, "b": [1, 2]}')

    def test_serialize_still_accepts_keyword_arguments(self):
        rpc = JSONRPC()
        self.assertEqual(rpc.serialize({'a': 1}, separators=(',', ':')), '{"a":1}')


class TestBytesMustBeUtf8(unittest.TestCase):
    def setUp(self):
        self.rpc = JSONRPC()
        self.rpc.register('ping', NoParamsMethod())
        self.body = '{"jsonrpc":"2.0","method":"ping","id":1}'

    def test_utf8_bytes_are_accepted(self):
        response = self.rpc.handle(self.body.encode('utf-8'))
        self.assertEqual(json.loads(response)['result'], 'pong')

    def test_utf16_bytes_are_refused(self):
        """json.loads() sniffs UTF-16/32 for bytes input; the wire format is UTF-8."""
        response = self.rpc.handle(self.body.encode('utf-16'))
        self.assertEqual(json.loads(response)['error']['code'], -32700)

    def test_invalid_utf8_is_refused(self):
        response = self.rpc.handle(b'{"jsonrpc":"2.0","method":"\xff\xfe","id":1}')
        self.assertEqual(json.loads(response)['error']['code'], -32700)


class TestRequestMustBeAJsonObject(unittest.TestCase):
    """A JSON string containing a request is not a request."""

    def setUp(self):
        self.rpc = JSONRPC()
        self.rpc.register('admin_delete', AdminDeleteMethod())
        self.inner = '{"jsonrpc":"2.0","method":"admin_delete","id":9}'

    def test_a_json_string_body_is_not_unwrapped_and_executed(self):
        body = json.dumps(self.inner)
        self.assertIsInstance(json.loads(body), str)

        response = self.rpc.handle(body)
        data = json.loads(response)

        self.assertEqual(data['error']['code'], -32600)
        self.assertNotIn('deleted', response)

    def test_a_json_string_inside_a_batch_is_not_unwrapped_either(self):
        response = self.rpc.handle(json.dumps([self.inner]))
        data = json.loads(response)
        self.assertEqual(data[0]['error']['code'], -32600)

    def test_a_json_string_body_is_refused_on_the_async_path_too(self):
        response = asyncio.run(self.rpc.handle_async(json.dumps(self.inner)))
        self.assertEqual(json.loads(response)['error']['code'], -32600)

    def test_a_custom_deserialize_hook_is_not_bypassed(self):
        """The second parse used to run below deserialize(), on stdlib json.

        A host that replaced the parser - for speed, for limits, for a different
        library - did not get either on that path.
        """
        seen = []

        class CountingRPC(JSONRPC):
            def deserialize(self, data):
                seen.append(data)
                return super().deserialize(data)

        rpc = CountingRPC()
        rpc.register('admin_delete', AdminDeleteMethod())
        rpc.handle(json.dumps(self.inner))

        self.assertEqual(len(seen), 1)

    def test_a_scalar_body_is_refused(self):
        self.assertEqual(json.loads(self.rpc.handle('5'))['error']['code'], -32600)

    def test_a_null_body_is_refused(self):
        self.assertEqual(json.loads(self.rpc.handle('null'))['error']['code'], -32600)


class TestBatchSerializationIsolation(unittest.TestCase):
    """One unserializable result must not take the whole batch down with it."""

    def setUp(self):
        self.committed = []

        committed = self.committed

        class Payment(Method):
            def execute(self, params: AddParams) -> str:
                committed.append(params.a)
                return 'settled'

        class Metrics(Method):
            def execute(self, params: None) -> object:
                import datetime

                return datetime.datetime(2020, 1, 1)

        self.rpc = JSONRPC()
        self.rpc.register('pay', Payment())
        self.rpc.register('metrics', Metrics())

        self.batch = json.dumps(
            [{'jsonrpc': '2.0', 'method': 'pay', 'params': {'a': i, 'b': 0}, 'id': i} for i in range(3)]
            + [{'jsonrpc': '2.0', 'method': 'metrics', 'id': 99}]
        )

    def test_committed_siblings_keep_their_receipts(self):
        response = self.rpc.handle(self.batch)
        data = json.loads(response)

        self.assertEqual(self.committed, [0, 1, 2])
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 4)
        self.assertEqual([entry.get('result') for entry in data[:3]], ['settled'] * 3)
        self.assertEqual([entry['id'] for entry in data], [0, 1, 2, 99])

    def test_only_the_failing_entry_becomes_an_error(self):
        data = json.loads(self.rpc.handle(self.batch))
        self.assertEqual(data[3]['error']['code'], -32603)
        self.assertEqual(data[3]['id'], 99)

    def test_the_async_path_isolates_the_same_way(self):
        data = json.loads(asyncio.run(self.rpc.handle_async(self.batch)))
        self.assertIsInstance(data, list)
        self.assertEqual([entry['id'] for entry in data], [0, 1, 2, 99])
        self.assertEqual(data[3]['error']['code'], -32603)

    def test_a_healthy_batch_is_unaffected(self):
        batch = json.dumps([{'jsonrpc': '2.0', 'method': 'pay', 'params': {'a': 1, 'b': 0}, 'id': 1}])
        data = json.loads(self.rpc.handle(batch))
        self.assertEqual(data[0]['result'], 'settled')


class TestInternalErrorSanitization(unittest.TestCase):
    """Exception text is written for an operator, not for a caller."""

    def setUp(self):
        class Boom(Method):
            def execute(self, params: None) -> str:
                raise RuntimeError("psycopg2 FATAL: password authentication failed for user 'app'")

        self.method_class = Boom

    def test_the_wire_gets_a_bare_internal_error_by_default(self):
        rpc = JSONRPC()
        rpc.register('boom', self.method_class())
        data = json.loads(rpc.handle('{"jsonrpc":"2.0","method":"boom","id":1}'))

        self.assertEqual(data['error']['code'], -32603)
        self.assertEqual(data['error']['message'], 'Internal error')
        self.assertNotIn('psycopg2', json.dumps(data))

    def test_the_full_exception_still_reaches_the_log(self):
        rpc = JSONRPC()
        rpc.register('boom', self.method_class())
        with self.assertLogs('jsonrpc-lib', level='ERROR') as cm:
            rpc.handle('{"jsonrpc":"2.0","method":"boom","id":1}')
        self.assertTrue(any('psycopg2' in record for record in cm.output))

    def test_expose_internal_errors_restores_the_old_behaviour(self):
        rpc = JSONRPC(expose_internal_errors=True)
        rpc.register('boom', self.method_class())
        data = json.loads(rpc.handle('{"jsonrpc":"2.0","method":"boom","id":1}'))
        self.assertIn('psycopg2', data['error']['message'])

    def test_deliberate_protocol_errors_are_never_sanitized(self):
        """A JSONRPCError a method raises is the application's own vocabulary."""
        from jsonrpc.errors import InvalidParamsError

        class Refusing(Method):
            def execute(self, params: None) -> str:
                raise InvalidParamsError('Account is frozen, contact support')

        rpc = JSONRPC()
        rpc.register('refusing', Refusing())
        data = json.loads(rpc.handle('{"jsonrpc":"2.0","method":"refusing","id":1}'))

        self.assertEqual(data['error']['code'], -32602)
        self.assertEqual(data['error']['message'], 'Account is frozen, contact support')

    def test_the_wiring_diagnostic_is_not_sanitized_away(self):
        """Dispatch's own "you wired this wrong" message must reach the developer.

        It names the method path and the entry point to use - facts the caller
        already has - so the reason for sanitizing exception text does not apply,
        and losing it turns a one-line fix into a debugging session.
        """
        rpc = JSONRPC(version='2.0')
        rpc.register('async_op', AsyncMethod())

        data = json.loads(rpc.handle('{"jsonrpc":"2.0","method":"async_op","id":1}'))

        self.assertEqual(data['error']['code'], -32603)
        self.assertIn('use dispatch_async() instead', data['error']['message'])

    def test_an_ordinary_exception_is_still_sanitized(self):
        rpc = JSONRPC(version='2.0')
        rpc.register('boom', self.method_class())

        data = json.loads(rpc.handle('{"jsonrpc":"2.0","method":"boom","id":1}'))
        self.assertEqual(data['error']['message'], 'Internal error')

    def test_a_refused_request_is_logged(self):
        """Method resolution and params validation both refuse before any guard runs.

        That arm used to write nothing at any level, so enumeration left no trace.
        """
        rpc = JSONRPC()
        rpc.register('ping', NoParamsMethod())
        with self.assertLogs('jsonrpc-lib', level='INFO') as cm:
            rpc.handle('{"jsonrpc":"2.0","method":"does_not_exist","id":1}')
        self.assertTrue(any('does_not_exist' in record for record in cm.output))


class TestErrorResponsesEchoTheId(unittest.TestCase):
    """An error response must carry the id of the request that caused it.

    The spec allows a null id only when the id could not be detected. A bad
    `method` or `params` is not that case - the id is sitting right there. A
    client keyed on ids used to be handed an answer it could not match to any
    outstanding call, and waited for one that had already arrived.
    """

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        math = MethodGroup()
        math.register('add', AddMethod())
        self.rpc.register('math', math)

    def _error(self, body):
        return json.loads(self.rpc.handle(body))

    def test_params_of_the_wrong_shape_keeps_the_id(self):
        data = self._error('{"jsonrpc":"2.0","method":"math.add","params":"oops","id":42}')
        self.assertEqual(data['error']['code'], -32600)
        self.assertEqual(data['id'], 42)

    def test_wrong_protocol_version_keeps_the_id(self):
        data = self._error('{"jsonrpc":"1.5","method":"math.add","params":{"a":1,"b":1},"id":42}')
        self.assertEqual(data['id'], 42)

    def test_non_string_method_keeps_the_id(self):
        data = self._error('{"jsonrpc":"2.0","method":5,"params":{"a":1,"b":1},"id":42}')
        self.assertEqual(data['id'], 42)

    def test_a_string_id_is_echoed_too(self):
        data = self._error('{"jsonrpc":"2.0","method":5,"id":"abc"}')
        self.assertEqual(data['id'], 'abc')

    def test_an_unusable_id_is_still_null(self):
        """Here the id genuinely cannot be determined, which is the spec's case."""
        data = self._error('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":1},"id":4.2}')
        self.assertIsNone(data['id'])

    def test_batch_entries_stay_distinguishable(self):
        data = self._error(
            '[{"jsonrpc":"2.0","method":"math.add","params":"x","id":1},'
            ' {"jsonrpc":"2.0","method":"math.add","params":"y","id":2}]'
        )
        self.assertEqual([entry['id'] for entry in data], [1, 2])


class TestBatchEntriesMustBeVersion2(unittest.TestCase):
    """Batching exists only in 2.0, so every entry is a 2.0 request.

    A 1.0-framed entry used to be answered in 1.0 framing, producing one array
    holding two different response shapes.
    """

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        math = MethodGroup()
        math.register('add', AddMethod())
        self.rpc.register('math', math)

    def test_an_entry_without_the_version_is_refused(self):
        data = json.loads(
            self.rpc.handle(
                '[{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":1},"id":7},'
                ' {"method":"math.add","params":[3,3],"id":8}]'
            )
        )
        self.assertEqual(data[0]['result'], 2)
        self.assertEqual(data[1]['error']['code'], -32600)
        self.assertEqual(data[1]['id'], 8)
        self.assertTrue(all('jsonrpc' in entry for entry in data))

    def test_a_single_request_still_accepts_1_0_framing(self):
        """Only batching is 2.0-only; the spec recommends accepting 1.0 singles."""
        data = json.loads(self.rpc.handle('{"method":"math.add","params":[3,3],"id":9}'))
        self.assertEqual(data['result'], 6)
        self.assertNotIn('jsonrpc', data)


class TestDeeplyNestedBodyIsAParseError(unittest.TestCase):
    def test_recursion_error_becomes_32700(self):
        """Exhausting the parser's stack is a parse failure, not a server fault.

        RecursionError is a RuntimeError, so it slipped past the parse handler
        and became -32603 with a full traceback logged per request.
        """
        rpc = JSONRPC(version='2.0')
        rpc.register('ping', NoParamsMethod())
        body = '{"jsonrpc":"2.0","method":"ping","params":{"x":' + '[' * 20000 + ']' * 20000 + '},"id":1}'

        data = json.loads(rpc.handle(body))

        self.assertEqual(data['error']['code'], -32700)


class TestAsyncBatchConcurrencyDefault(unittest.TestCase):
    def test_the_default_limit_is_a_fixed_number(self):
        """os.cpu_count() said nothing useful: a coroutine awaiting a socket
        uses no CPU, so cores do not bound how many can wait at once."""
        from jsonrpc.jsonrpc import DEFAULT_MAX_CONCURRENT

        self.assertEqual(DEFAULT_MAX_CONCURRENT, 64)
        self.assertEqual(JSONRPC()._effective_max_concurrent, 64)

    def test_an_explicit_limit_still_wins(self):
        self.assertEqual(JSONRPC(max_concurrent=8)._effective_max_concurrent, 8)
        self.assertEqual(JSONRPC(max_concurrent=-1)._effective_max_concurrent, -1)


# ==========================================================================
# The worked examples from the specification
#
# https://www.jsonrpc.org/specification, section "Examples".
#
# Everything above was written by reading this implementation, which is how it
# ended up agreeing with itself and disagreeing with the document. These are
# transcribed from the specification instead: the request text is the one
# printed there, and each case asserts the whole response rather than a field
# of it. Where an example depends on a method the spec does not define, the
# method here does exactly what its printed response implies and nothing more.
# ==========================================================================


@dataclass
class SubtractParams:
    """Named form of subtract, per the spec's own example."""

    minuend: int
    subtrahend: int


@dataclass
class NumbersParams:
    numbers: list[int]


@dataclass
class OptionalNumbersParams:
    """The spec's notify.hello is called both with and without params."""

    numbers: list[int] = field(default_factory=list)


class Subtract(Method):
    def execute(self, params: SubtractParams) -> int:
        return params.minuend - params.subtrahend


class Update(Method):
    def execute(self, params: NumbersParams) -> int:
        return len(params.numbers)


class Notify(Method):
    def execute(self, params: OptionalNumbersParams) -> int:
        return len(params.numbers)


class Sum(Method):
    def execute(self, params: NumbersParams) -> int:
        return sum(params.numbers)


class GetData(Method):
    def execute(self, params: None) -> list:
        return ['hello', 5]


def build() -> JSONRPC:
    rpc = JSONRPC(version='2.0')
    rpc.register('subtract', Subtract())
    rpc.register('update', Update())
    rpc.register('sum', Sum())
    rpc.register('get_data', GetData())

    notify = MethodGroup()
    notify.register('hello', Notify())
    rpc.register('notify', notify)
    return rpc


class SpecExampleCase(unittest.TestCase):
    def setUp(self):
        self.rpc = build()

    def assertResponse(self, request: str, expected: str) -> None:
        """The whole response object must match, not merely some of its fields."""
        actual = self.rpc.handle(request)
        self.assertIsNotNone(actual, 'expected a response, got none')
        self.assertEqual(json.loads(actual), json.loads(expected))

    def assertNoResponse(self, request: str) -> None:
        self.assertIsNone(self.rpc.handle(request))


class TestPositionalParameters(SpecExampleCase):
    """rpc call with positional parameters."""

    def test_subtract_42_23(self):
        self.assertResponse(
            '{"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": 1}',
            '{"jsonrpc": "2.0", "result": 19, "id": 1}',
        )

    def test_subtract_23_42(self):
        self.assertResponse(
            '{"jsonrpc": "2.0", "method": "subtract", "params": [23, 42], "id": 2}',
            '{"jsonrpc": "2.0", "result": -19, "id": 2}',
        )


class TestNamedParameters(SpecExampleCase):
    """rpc call with named parameters."""

    def test_named_in_declaration_order(self):
        self.assertResponse(
            '{"jsonrpc": "2.0", "method": "subtract", "params": {"subtrahend": 23, "minuend": 42}, "id": 3}',
            '{"jsonrpc": "2.0", "result": 19, "id": 3}',
        )

    def test_named_in_any_order(self):
        self.assertResponse(
            '{"jsonrpc": "2.0", "method": "subtract", "params": {"minuend": 42, "subtrahend": 23}, "id": 4}',
            '{"jsonrpc": "2.0", "result": 19, "id": 4}',
        )


class TestNotifications(SpecExampleCase):
    """a Notification."""

    def test_notification_with_params(self):
        self.assertNoResponse('{"jsonrpc": "2.0", "method": "update", "params": {"numbers": [1,2,3,4,5]}}')

    def test_notification_without_params(self):
        self.assertNoResponse('{"jsonrpc": "2.0", "method": "notify.hello"}')


class TestNonExistentMethod(SpecExampleCase):
    """rpc call of non-existent method."""

    def test_method_not_found(self):
        response = json.loads(self.rpc.handle('{"jsonrpc": "2.0", "method": "foobar", "id": "1"}'))
        self.assertEqual(response['jsonrpc'], '2.0')
        self.assertEqual(response['id'], '1')
        self.assertEqual(response['error']['code'], -32601)


class TestInvalidJSON(SpecExampleCase):
    """rpc call with invalid JSON."""

    def test_parse_error(self):
        request = '{"jsonrpc": "2.0", "method": "foobar, "params": "bar", "baz]'
        response = json.loads(self.rpc.handle(request))
        self.assertEqual(response['jsonrpc'], '2.0')
        self.assertIsNone(response['id'])
        self.assertEqual(response['error']['code'], -32700)


class TestInvalidRequestObject(SpecExampleCase):
    """rpc call with invalid Request object."""

    def test_method_must_be_a_string(self):
        response = json.loads(self.rpc.handle('{"jsonrpc": "2.0", "method": 1, "params": "bar"}'))
        self.assertEqual(response['jsonrpc'], '2.0')
        self.assertEqual(response['error']['code'], -32600)


class TestBatchInvalidJSON(SpecExampleCase):
    """rpc call Batch, invalid JSON."""

    def test_one_parse_error_for_the_whole_batch(self):
        request = """[
          {"jsonrpc": "2.0", "method": "sum", "params": [1,2,4], "id": "1"},
          {"jsonrpc": "2.0", "method"
        ]"""
        response = json.loads(self.rpc.handle(request))
        self.assertEqual(response['jsonrpc'], '2.0')
        self.assertIsNone(response['id'])
        self.assertEqual(response['error']['code'], -32700)


class TestEmptyArray(SpecExampleCase):
    """rpc call with an empty Array."""

    def test_empty_batch_is_one_invalid_request(self):
        response = json.loads(self.rpc.handle('[]'))
        self.assertEqual(response['jsonrpc'], '2.0')
        self.assertIsNone(response['id'])
        self.assertEqual(response['error']['code'], -32600)


class TestInvalidBatch(SpecExampleCase):
    """rpc call with an invalid Batch."""

    def test_single_non_object_entry_answers_with_one_array_element(self):
        response = json.loads(self.rpc.handle('[1]'))
        self.assertIsInstance(response, list)
        self.assertEqual(len(response), 1)
        self.assertEqual(response[0]['error']['code'], -32600)
        self.assertIsNone(response[0]['id'])

    def test_three_non_object_entries_answer_with_three_array_elements(self):
        response = json.loads(self.rpc.handle('[1,2,3]'))
        self.assertIsInstance(response, list)
        self.assertEqual(len(response), 3)
        for entry in response:
            self.assertEqual(entry['error']['code'], -32600)
            self.assertIsNone(entry['id'])


class TestBatch(SpecExampleCase):
    """rpc call Batch - the mixed example from the specification."""

    def test_the_batch_answers_only_the_requests(self):
        request = """[
            {"jsonrpc": "2.0", "method": "sum", "params": {"numbers": [1,2,4]}, "id": "1"},
            {"jsonrpc": "2.0", "method": "notify.hello", "params": {"numbers": [7]}},
            {"jsonrpc": "2.0", "method": "subtract", "params": [42,23], "id": "2"},
            {"foo": "boo"},
            {"jsonrpc": "2.0", "method": "foo.get", "params": {"name": "myself"}, "id": "5"},
            {"jsonrpc": "2.0", "method": "get_data", "id": "9"}
        ]"""
        response = json.loads(self.rpc.handle(request))

        self.assertIsInstance(response, list)
        # One entry per request; the notification contributes nothing.
        self.assertEqual(len(response), 5)

        by_id = {entry.get('id'): entry for entry in response}
        self.assertEqual(by_id['1']['result'], 7)
        self.assertEqual(by_id['2']['result'], 19)
        self.assertEqual(by_id['5']['error']['code'], -32601)
        self.assertEqual(by_id['9']['result'], ['hello', 5])
        # {"foo": "boo"} carries no id at all, so its error answers with null.
        self.assertEqual(by_id[None]['error']['code'], -32600)

    def test_every_entry_is_a_2_0_response(self):
        request = """[
            {"jsonrpc": "2.0", "method": "sum", "params": {"numbers": [1,2,4]}, "id": "1"},
            {"jsonrpc": "2.0", "method": "subtract", "params": [42,23], "id": "2"}
        ]"""
        response = json.loads(self.rpc.handle(request))
        for entry in response:
            self.assertEqual(entry['jsonrpc'], '2.0')


class TestBatchOfOnlyNotifications(SpecExampleCase):
    """rpc call Batch (all notifications)."""

    def test_nothing_is_returned(self):
        request = """[
            {"jsonrpc": "2.0", "method": "notify.hello", "params": {"numbers": [1,2,4]}},
            {"jsonrpc": "2.0", "method": "notify.hello", "params": {"numbers": [7]}}
        ]"""
        self.assertIsNone(self.rpc.handle(request))


class TestIdEchoing(SpecExampleCase):
    """Section 5: the response id "MUST be the same as the value of the id member
    in the Request Object", with null reserved for "an error in detecting the id".
    """

    def test_a_refused_request_still_carries_its_id(self):
        response = json.loads(self.rpc.handle('{"jsonrpc": "2.0", "method": 1, "id": 77}'))
        self.assertEqual(response['id'], 77)

    def test_a_string_id_survives_as_a_string(self):
        response = json.loads(self.rpc.handle('{"jsonrpc": "2.0", "method": "subtract", "params": [42,23], "id": "x"}'))
        self.assertEqual(response['id'], 'x')

    def test_an_id_that_cannot_be_detected_is_null(self):
        response = json.loads(self.rpc.handle('{"jsonrpc": "2.0", "method": "subtract", "params": [1,2], "id": {}}'))
        self.assertIsNone(response['id'])


if __name__ == '__main__':
    unittest.main()
