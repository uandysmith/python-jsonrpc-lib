"""Tests for @rpc.method decorator.

This module tests the decorator API for simplified method registration.
The decorator is designed for prototyping only and has intentional limitations
(no context, no groups) compared to the full Method class approach.
"""

import asyncio
import json
import unittest

from jsonrpc import JSONRPC, OpenAPIGenerator


class TestMethodDecorator(unittest.TestCase):
    """Test @rpc.method decorator functionality."""

    def setUp(self):
        """Create fresh RPC instance for each test."""
        self.rpc = JSONRPC(version='2.0')

    def test_simple_function(self):
        """Test decorator with simple function (basic case)."""

        @self.rpc.method
        def add(a: int, b: int) -> int:
            return a + b

        # Test via RPC
        result = self.rpc.call_method('add', {'a': 2, 'b': 3})
        self.assertEqual(result, 5)

        # Test with positional params
        result = self.rpc.call_method('add', [2, 3])
        self.assertEqual(result, 5)

    def test_custom_name(self):
        """Test decorator with custom method name."""

        @self.rpc.method('custom_add')
        def add(a: int, b: int) -> int:
            return a + b

        # Should be registered as 'custom_add', not 'add'
        result = self.rpc.call_method('custom_add', {'a': 10, 'b': 5})
        self.assertEqual(result, 15)

        # Original name should NOT be registered
        with self.assertRaises(Exception):
            self.rpc.call_method('add', {'a': 1, 'b': 1})

    def test_no_params(self):
        """Test function with no parameters."""

        @self.rpc.method
        def ping() -> str:
            return 'pong'

        # Test with no params
        result = self.rpc.call_method('ping')
        self.assertEqual(result, 'pong')

        # Test with None params
        result = self.rpc.call_method('ping', None)
        self.assertEqual(result, 'pong')

        # Test with empty dict
        result = self.rpc.call_method('ping', {})
        self.assertEqual(result, 'pong')

    def test_original_function_still_works(self):
        """Test that original function can still be called directly."""

        @self.rpc.method
        def multiply(x: int, y: int) -> int:
            return x * y

        # Original function should still be callable
        result = multiply(3, 4)
        self.assertEqual(result, 12)

        # RPC call should also work
        result = self.rpc.call_method('multiply', {'x': 3, 'y': 4})
        self.assertEqual(result, 12)

    def test_default_values(self):
        """Test function with default parameter values."""

        @self.rpc.method
        def greet(name: str, greeting: str = 'Hello') -> str:
            return f'{greeting}, {name}!'

        # With default
        result = self.rpc.call_method('greet', {'name': 'Alice'})
        self.assertEqual(result, 'Hello, Alice!')

        # Override default
        result = self.rpc.call_method('greet', {'name': 'Bob', 'greeting': 'Hi'})
        self.assertEqual(result, 'Hi, Bob!')

    def test_optional_params(self):
        """Test function with optional (T | None) parameters."""

        @self.rpc.method
        def create_user(username: str, age: int | None = None) -> dict:
            return {'username': username, 'age': age}

        # Without optional param
        result = self.rpc.call_method('create_user', {'username': 'alice'})
        self.assertEqual(result, {'username': 'alice', 'age': None})

        # With optional param
        result = self.rpc.call_method('create_user', {'username': 'bob', 'age': 25})
        self.assertEqual(result, {'username': 'bob', 'age': 25})

    def test_complex_types(self):
        """Test function with complex type hints."""

        @self.rpc.method
        def process(items: list[int], metadata: dict[str, str]) -> list[str]:
            return [f'{meta}: {item}' for item, meta in zip(items, metadata.values())]

        result = self.rpc.call_method(
            'process', {'items': [1, 2, 3], 'metadata': {'a': 'first', 'b': 'second', 'c': 'third'}}
        )
        self.assertEqual(result, ['first: 1', 'second: 2', 'third: 3'])

    def test_async_function(self):
        """Test decorator with async function."""

        @self.rpc.method
        async def fetch(url: str) -> str:
            await asyncio.sleep(0.001)  # Simulate async work
            return f'Data from {url}'

        # Test via async call_method
        result = asyncio.run(self.rpc.call_method_async('fetch', {'url': 'example.com'}))
        self.assertEqual(result, 'Data from example.com')

    def test_return_dataclass_like_dict(self):
        """Test function that returns dict (dataclass-like)."""

        @self.rpc.method
        def get_info(name: str, age: int) -> dict:
            return {'name': name, 'age': age, 'active': True}

        result = self.rpc.call_method('get_info', {'name': 'Alice', 'age': 30})
        self.assertEqual(result, {'name': 'Alice', 'age': 30, 'active': True})

    def test_multiple_decorators_same_rpc(self):
        """Test multiple decorated functions on same RPC instance."""

        @self.rpc.method
        def add(a: int, b: int) -> int:
            return a + b

        @self.rpc.method
        def subtract(a: int, b: int) -> int:
            return a - b

        @self.rpc.method
        def multiply(x: int, y: int) -> int:
            return x * y

        # All should be registered
        self.assertEqual(self.rpc.call_method('add', [5, 3]), 8)
        self.assertEqual(self.rpc.call_method('subtract', [5, 3]), 2)
        self.assertEqual(self.rpc.call_method('multiply', [5, 3]), 15)

        # Check list_methods
        methods = self.rpc.list_methods()
        self.assertIn('add', methods)
        self.assertIn('subtract', methods)
        self.assertIn('multiply', methods)

    def test_missing_type_hints(self):
        """Test that missing type hints raise TypeError."""
        with self.assertRaises(TypeError) as ctx:

            @self.rpc.method
            def broken(a, b):  # No type hints!
                return a + b

        self.assertIn('type hint', str(ctx.exception).lower())
        self.assertIn('broken', str(ctx.exception))

    def test_missing_return_type(self):
        """Test that missing return type raises TypeError."""
        with self.assertRaises(TypeError) as ctx:

            @self.rpc.method
            def broken(a: int, b: int):  # No return type!
                return a + b

        self.assertIn('return type', str(ctx.exception).lower())
        self.assertIn('broken', str(ctx.exception))

    def test_context_parameter_not_supported(self):
        """Test that context parameter raises TypeError."""
        with self.assertRaises(TypeError) as ctx:

            @self.rpc.method
            def broken(a: int, context: dict) -> int:  # context not supported!
                return a

        self.assertIn('context', str(ctx.exception).lower())
        self.assertIn('not support', str(ctx.exception).lower())

    def test_invalid_method_name_with_dots(self):
        """Test that method name with dots raises ValueError."""
        with self.assertRaises(ValueError) as ctx:

            @self.rpc.method('math.add')  # Dots not allowed!
            def add(a: int, b: int) -> int:
                return a + b

        self.assertIn("'.'", str(ctx.exception))
        self.assertIn('math.add', str(ctx.exception))

    def test_empty_method_name(self):
        """Test that empty method name raises ValueError."""
        with self.assertRaises(ValueError) as ctx:

            @self.rpc.method('')  # Empty name not allowed!
            def add(a: int, b: int) -> int:
                return a + b

        self.assertIn('empty', str(ctx.exception).lower())

    def test_full_jsonrpc_flow(self):
        """Test complete JSON-RPC request/response flow."""

        @self.rpc.method
        def multiply(x: int, y: int) -> int:
            return x * y

        # Test JSON-RPC 2.0 request
        request = '{"jsonrpc": "2.0", "method": "multiply", "params": {"x": 3, "y": 4}, "id": 1}'
        response = self.rpc.handle(request)

        self.assertIsNotNone(response)
        data = json.loads(response)
        self.assertEqual(data['jsonrpc'], '2.0')
        self.assertEqual(data['result'], 12)
        self.assertEqual(data['id'], 1)

    def test_full_jsonrpc_flow_positional_params(self):
        """Test JSON-RPC with positional parameters."""

        @self.rpc.method
        def add(a: int, b: int) -> int:
            return a + b

        # Test with dict params
        request = '{"jsonrpc": "2.0", "method": "add", "params": {"a": 5, "b": 7}, "id": 2}'
        response = self.rpc.handle(request)

        data = json.loads(response)
        self.assertEqual(data['result'], 12)

    def test_full_jsonrpc_notification(self):
        """Test JSON-RPC notification (no response expected)."""
        call_count = []

        @self.rpc.method
        def log(message: str) -> str:
            call_count.append(message)
            return 'logged'

        # Notification (no id) should not return response
        request = '{"jsonrpc": "2.0", "method": "log", "params": {"message": "test"}}'
        response = self.rpc.handle(request)

        # No response for notifications
        self.assertIsNone(response)

        # But function should have been called
        self.assertEqual(call_count, ['test'])

    def test_openapi_generation(self):
        """Test that decorated methods work with OpenAPI generator."""

        @self.rpc.method
        def add(a: int, b: int) -> int:
            """Add two numbers together."""
            return a + b

        @self.rpc.method
        def greet(name: str) -> str:
            """Greet a person by name."""
            return f'Hello, {name}!'

        # Generate OpenAPI spec
        openapi = OpenAPIGenerator(self.rpc, title='Test API', version='1.0.0')
        spec = openapi.generate()

        # Verify spec structure
        self.assertIn('paths', spec)
        self.assertIn('components', spec)

        # Verify methods are in spec
        self.assertIn('/jsonrpc#add', spec['paths'])
        self.assertIn('/jsonrpc#greet', spec['paths'])

        # Verify schemas exist
        self.assertIn('schemas', spec['components'])
        self.assertIn('add_request', spec['components']['schemas'])
        self.assertIn('add_response', spec['components']['schemas'])

    def test_async_full_flow(self):
        """Test async function with full JSON-RPC flow."""

        @self.rpc.method
        async def async_add(a: int, b: int) -> int:
            await asyncio.sleep(0.001)
            return a + b

        # Test async handle
        request = '{"jsonrpc": "2.0", "method": "async_add", "params": {"a": 10, "b": 20}, "id": 1}'
        response = asyncio.run(self.rpc.handle_async(request))

        data = json.loads(response)
        self.assertEqual(data['result'], 30)

    def test_batch_request_with_decorated_methods(self):
        """Test batch requests with decorated methods."""

        @self.rpc.method
        def add(a: int, b: int) -> int:
            return a + b

        @self.rpc.method
        def multiply(x: int, y: int) -> int:
            return x * y

        # Batch request
        batch_request = json.dumps(
            [
                {'jsonrpc': '2.0', 'method': 'add', 'params': {'a': 1, 'b': 2}, 'id': 1},
                {'jsonrpc': '2.0', 'method': 'multiply', 'params': {'x': 3, 'y': 4}, 'id': 2},
            ]
        )

        response = self.rpc.handle(batch_request)
        data = json.loads(response)

        # Verify batch response
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['result'], 3)  # add(1, 2)
        self.assertEqual(data[1]['result'], 12)  # multiply(3, 4)

    def test_method_listed_in_list_methods(self):
        """Test that decorated methods appear in list_methods()."""

        @self.rpc.method
        def test_method(x: int) -> int:
            return x * 2

        methods = self.rpc.list_methods()
        self.assertIn('test_method', methods)

    def test_get_method_returns_instance(self):
        """Test that get_method() returns Method instance for decorated function."""

        @self.rpc.method
        def sample(value: int) -> int:
            return value

        method_instance = self.rpc.get_method('sample')
        self.assertIsNotNone(method_instance)

        # Should be a Method instance with proper attributes
        self.assertTrue(hasattr(method_instance, 'params_type'))
        self.assertTrue(hasattr(method_instance, 'result_type'))

    def test_decorator_v1_raises_error(self):
        """Test that decorator raises error for JSON-RPC 1.0."""
        rpc_v1 = JSONRPC(version='1.0')

        with self.assertRaises(ValueError) as context:

            @rpc_v1.method
            def add(a: int, b: int) -> int:
                return a + b

        error_msg = str(context.exception)
        self.assertEqual('only available for JSON-RPC 2.0' in error_msg, True)
        self.assertEqual('current version: 1.0' in error_msg, True)
        self.assertEqual('intentional' in error_msg, True)
        self.assertEqual('prototyping' in error_msg, True)


class TestDecoratorEdgeCaseCoverage(unittest.TestCase):
    """Tests covering specific lines in decorator implementation."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')

    def test_decorator_async_no_params_execution_covers_line_123(self):
        """Async decorated function with no params hits 'return await func()' (line 123)."""

        @self.rpc.method
        async def async_ping() -> str:
            return 'pong'

        request = json.dumps({'jsonrpc': '2.0', 'method': 'async_ping', 'id': 1})
        response = asyncio.run(self.rpc.handle_async(request))
        data = json.loads(response)
        self.assertEqual(data['result'], 'pong')

    def test_decorator_async_with_docstring_sets_doc_covers_line_128(self):
        """Async decorated function with docstring sets DecoratedAsyncMethod.__doc__ (line 128)."""

        @self.rpc.method
        async def fetch_data(url: str) -> str:
            """Fetch data from URL."""
            return f'data from {url}'

        method = self.rpc.get_method('fetch_data')
        self.assertEqual(method.__class__.__doc__, 'Fetch data from URL.')

    def test_decorator_function_with_self_param_skips_it_covers_line_82(self):
        """Function with typed 'self' param — _create_params_dataclass skips it (line 82)."""

        @self.rpc.method
        def scale(self: float, factor: float) -> float:
            return self * factor

        result = scale(3.0, 2.0)
        self.assertEqual(result, 6.0)


if __name__ == '__main__':
    unittest.main()
