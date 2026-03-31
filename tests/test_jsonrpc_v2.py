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
        self.assertEqual('Unexpected error' in data['error']['message'], True)

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
            self.assertEqual('dispatcher error' in data['error']['message'], True)

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
            self.assertEqual('async dispatcher error' in data['error']['message'], True)

    def test_handle_outer_except_catches_exception_from_handle_single(self):
        """handle() outer except fires when _handle_single itself raises unexpectedly."""
        from unittest.mock import patch

        # Patch _handle_single to raise — bypasses all internal error handling.
        with patch.object(self.rpc, '_handle_single', side_effect=RuntimeError('internal crash')):
            response = self.rpc.handle('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}')
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)
        self.assertIn('internal crash', data['error']['message'])

    def test_handle_async_outer_except_catches_exception_from_handle_single_async(self):
        """handle_async() outer except fires when _handle_single_async itself raises unexpectedly."""
        from unittest.mock import patch

        with patch.object(self.rpc, '_handle_single_async', side_effect=RuntimeError('async crash')):
            response = asyncio.run(
                self.rpc.handle_async('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}')
            )
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)
        self.assertIn('async crash', data['error']['message'])


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
        self.assertIn('cannot serialize', data['error']['message'])
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
        self.assertIn('cannot serialize', data['error']['message'])

    def test_handle_batch_serialize_failure_returns_internal_error(self):
        """_handle_batch: TypeError from serialize() on batch falls back to InternalError."""
        from unittest.mock import patch

        rpc = self._make_rpc()
        batch = json.dumps([{'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 1, 'b': 2}, 'id': 1}])
        with patch.object(rpc, 'serialize', side_effect=self._failing_serialize(fail_on_call=1)):
            response = rpc.handle(batch)
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)

    def test_handle_batch_async_serialize_failure_returns_internal_error(self):
        """_handle_batch_async: TypeError from serialize() on batch falls back to InternalError."""
        from unittest.mock import patch

        rpc = self._make_rpc()
        batch = json.dumps([{'jsonrpc': '2.0', 'method': 'math.add', 'params': {'a': 1, 'b': 2}, 'id': 1}])
        with patch.object(rpc, 'serialize', side_effect=self._failing_serialize(fail_on_call=1)):
            response = asyncio.run(rpc.handle_async(batch))
        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)


class TestV2Logging(unittest.TestCase):
    """Tests for logging behavior."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')

        class BrokenMethod(Method):
            def execute(self, params: None) -> str:
                raise RuntimeError('Unexpected error!')

        self.rpc.register('broken', BrokenMethod())

    def test_notification_error_logged_at_debug(self):
        """Suppressed notification errors are logged at DEBUG level."""
        with self.assertLogs('jsonrpc-lib', level='DEBUG') as cm:
            request = '{"jsonrpc":"2.0","method":"broken"}'
            response = self.rpc.handle(request)

        self.assertIsNone(response)
        self.assertTrue(any('Notification error suppressed' in msg for msg in cm.output))

    def test_unhandled_exception_logged_at_error(self):
        """Unhandled exceptions in method dispatch are logged at ERROR level."""
        with self.assertLogs('jsonrpc-lib', level='ERROR') as cm:
            request = '{"jsonrpc":"2.0","method":"broken","id":1}'
            response = self.rpc.handle(request)

        data = json.loads(response)
        self.assertEqual(data['error']['code'], -32603)
        self.assertTrue(any('Unhandled exception' in msg for msg in cm.output))

    def test_async_notification_error_logged_at_debug(self):
        """Suppressed async notification errors are logged at DEBUG level."""

        class AsyncBrokenMethod(Method):
            async def execute(self, params: None) -> str:
                raise RuntimeError('Async error!')

        rpc = JSONRPC(version='2.0')
        rpc.register('async_broken', AsyncBrokenMethod())

        with self.assertLogs('jsonrpc-lib', level='DEBUG') as cm:
            request = '{"jsonrpc":"2.0","method":"async_broken"}'
            response = asyncio.run(rpc.handle_async(request))

        self.assertIsNone(response)
        self.assertTrue(any('Notification error suppressed' in msg for msg in cm.output))


if __name__ == '__main__':
    unittest.main()
