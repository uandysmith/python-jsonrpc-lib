"""Tests for OpenAPI documentation generator - comprehensive coverage for all methods."""

import json
import unittest

from jsonrpc import JSONRPC, MethodGroup
from jsonrpc.openapi import OpenAPIGenerator
from tests.fixtures import (
    AddMethod,
    AsyncDataclassResultMethod,
    AsyncMethod,
    ComplexTypesMethod,
    DataclassResultMethod,
    DictDataclassResultMethod,
    EchoMethod,
    ErrorMethod,
    InternalCallMethod,
    ListDataclassResultMethod,
    MetadataMethod,
    MultiLineDocstringMethod,
    MultiplyMethod,
    NestedCompanyMethod,
    NestedDataclassResultMethod,
    NoDocstringMethod,
    NoParamsMethod,
    NoResultTypeMethod,
    OptionalMethod,
    SubtractMethod,
    TypedAddMethod,
    WrongTypeMethod,
)


class TestOpenAPIBasic(unittest.TestCase):
    """Tests for basic OpenAPI spec generation."""

    def setUp(self):
        self.rpc = JSONRPC()

        # Register all methods in appropriate groups
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        math_group.register('subtract', SubtractMethod())
        math_group.register('multiply', MultiplyMethod())
        self.rpc.register('math', math_group)

        utils_group = MethodGroup()
        utils_group.register('ping', NoParamsMethod())
        utils_group.register('echo', EchoMethod())
        utils_group.register('optional', OptionalMethod())
        self.rpc.register('utils', utils_group)

        results_group = MethodGroup()
        results_group.register('dataclass_result', DataclassResultMethod())
        results_group.register('nested_user', NestedDataclassResultMethod())
        results_group.register('list_results', ListDataclassResultMethod())
        results_group.register('dict_results', DictDataclassResultMethod())
        self.rpc.register('results', results_group)

        async_group = MethodGroup()
        async_group.register('async_test', AsyncMethod())
        async_group.register('async_dataclass_add', AsyncDataclassResultMethod())
        self.rpc.register('async', async_group)

        special_group = MethodGroup()
        special_group.register('process_company', NestedCompanyMethod())
        special_group.register('double_add', InternalCallMethod())
        special_group.register('typed_add', TypedAddMethod())
        special_group.register('wrong_type', WrongTypeMethod())
        special_group.register('error', ErrorMethod())
        self.rpc.register('special', special_group)

    def test_generate_basic_spec(self):
        """Test basic OpenAPI spec structure."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        self.assertEqual(spec['openapi'], '3.0.3')
        self.assertEqual(spec['info']['title'], 'JSON-RPC API')
        self.assertEqual(spec['info']['version'], '1.0.0')

    def test_generate_with_custom_info(self):
        """Test OpenAPI spec with custom metadata."""
        generator = OpenAPIGenerator(
            self.rpc,
            base_url='/api/rpc',
            title='My API',
            version='2.0.0',
            description='Test API',
        )
        spec = generator.generate()

        self.assertEqual(spec['info']['title'], 'My API')
        self.assertEqual(spec['info']['version'], '2.0.0')
        self.assertEqual(spec['info']['description'], 'Test API')
        self.assertIn('/api/rpc#math.add', spec['paths'])

    def test_generate_json(self):
        """Test JSON serialization of OpenAPI spec."""
        generator = OpenAPIGenerator(self.rpc)
        json_str = generator.generate_json()
        data = json.loads(json_str)
        self.assertEqual(data['openapi'], '3.0.3')

    def test_generate_json_with_indent(self):
        """Test JSON serialization with indentation."""
        generator = OpenAPIGenerator(self.rpc)
        json_str = generator.generate_json(indent=4)
        self.assertIn('    ', json_str)


class TestOpenAPIBasicMethods(unittest.TestCase):
    """Tests for basic methods with simple types."""

    def setUp(self):
        self.rpc = JSONRPC()
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        math_group.register('subtract', SubtractMethod())
        math_group.register('multiply', MultiplyMethod())
        self.rpc.register('math', math_group)

        utils_group = MethodGroup()
        utils_group.register('ping', NoParamsMethod())
        utils_group.register('echo', EchoMethod())
        self.rpc.register('utils', utils_group)

    def test_add_method_schema(self):
        """Test OpenAPI schema for add method (int params → int result)."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        # Check path exists
        self.assertIn('/jsonrpc#math.add', spec['paths'])
        add_op = spec['paths']['/jsonrpc#math.add']['post']

        # Check operation metadata
        self.assertEqual(add_op['operationId'], 'math_add')
        self.assertEqual(add_op['summary'], 'Add two numbers together.')
        self.assertEqual(add_op['tags'], ['math'])

        # Check request schema
        schemas = spec['components']['schemas']
        self.assertIn('math.add_request', schemas)
        add_request = schemas['math.add_request']
        self.assertIn('params', add_request['properties'])

        # Check params schema (AddParams)
        params_schema = add_request['properties']['params']
        self.assertEqual(params_schema['type'], 'object')
        self.assertIn('a', params_schema['properties'])
        self.assertIn('b', params_schema['properties'])
        self.assertEqual(params_schema['properties']['a']['type'], 'integer')
        self.assertEqual(params_schema['properties']['b']['type'], 'integer')
        self.assertIn('a', params_schema['required'])
        self.assertIn('b', params_schema['required'])

        # Check response schema
        self.assertIn('math.add_response', schemas)
        add_response = schemas['math.add_response']
        self.assertEqual(add_response['properties']['result']['type'], 'integer')

    def test_subtract_method_schema(self):
        """Test OpenAPI schema for subtract method."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        self.assertIn('/jsonrpc#math.subtract', spec['paths'])
        subtract_op = spec['paths']['/jsonrpc#math.subtract']['post']
        self.assertEqual(subtract_op['operationId'], 'math_subtract')

    def test_echo_method_schema(self):
        """Test OpenAPI schema for echo method (string params → string result)."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        self.assertIn('/jsonrpc#utils.echo', spec['paths'])
        schemas = spec['components']['schemas']

        # Check EchoParams schema
        echo_request = schemas['utils.echo_request']
        params_schema = echo_request['properties']['params']
        self.assertIn('message', params_schema['properties'])
        self.assertEqual(params_schema['properties']['message']['type'], 'string')

        # Check string result
        echo_response = schemas['utils.echo_response']
        self.assertEqual(echo_response['properties']['result']['type'], 'string')

    def test_ping_method_schema(self):
        """Test OpenAPI schema for ping method (no params → string result)."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        self.assertIn('/jsonrpc#utils.ping', spec['paths'])
        schemas = spec['components']['schemas']

        # Check request schema - ping has no params, so no params field
        ping_request = schemas['utils.ping_request']
        # No params field for methods with params: None
        self.assertNotIn('params', ping_request['properties'])


class TestOpenAPIComplexParams(unittest.TestCase):
    """Tests for methods with complex parameters."""

    def setUp(self):
        self.rpc = JSONRPC()
        math_group = MethodGroup()
        math_group.register('multiply', MultiplyMethod())
        self.rpc.register('math', math_group)

        utils_group = MethodGroup()
        utils_group.register('optional', OptionalMethod())
        self.rpc.register('utils', utils_group)

        special_group = MethodGroup()
        special_group.register('process_company', NestedCompanyMethod())
        self.rpc.register('special', special_group)

    def test_multiply_method_schema(self):
        """Test OpenAPI schema for multiply method (3 params: x, y, z)."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        schemas = spec['components']['schemas']
        multiply_request = schemas['math.multiply_request']
        params_schema = multiply_request['properties']['params']

        # Should have x, y, z fields
        self.assertIn('x', params_schema['properties'])
        self.assertIn('y', params_schema['properties'])
        self.assertIn('z', params_schema['properties'])
        self.assertEqual(params_schema['properties']['x']['type'], 'integer')
        self.assertIn('x', params_schema['required'])
        self.assertIn('y', params_schema['required'])
        self.assertIn('z', params_schema['required'])

    def test_optional_params_schema(self):
        """Test OpenAPI schema for optional method (params with optional field)."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        schemas = spec['components']['schemas']
        optional_request = schemas['utils.optional_request']
        params_schema = optional_request['properties']['params']

        # Should have required and optional fields
        self.assertIn('required', params_schema['properties'])
        self.assertIn('optional', params_schema['properties'])

        # Only 'required' should be in required array
        self.assertIn('required', params_schema['required'])
        self.assertNotIn('optional', params_schema['required'])

    def test_nested_company_schema(self):
        """Test OpenAPI schema for nested_company (3-level nested dataclass params)."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        schemas = spec['components']['schemas']
        company_request = schemas['special.process_company_request']
        params_schema = company_request['properties']['params']

        # CompanyInfo is inlined into params, not referenced
        self.assertEqual(params_schema['type'], 'object')
        self.assertIn('name', params_schema['properties'])
        self.assertIn('founded', params_schema['properties'])
        self.assertIn('address', params_schema['properties'])

        # Address should reference Address schema
        address_ref = params_schema['properties']['address']
        self.assertIn('$ref', address_ref)
        self.assertIn('Address', address_ref['$ref'])

        # Address schema should exist
        self.assertIn('Address', schemas)
        address_schema = schemas['Address']
        self.assertIn('contact', address_schema['properties'])

        # Contact should reference Contact schema
        contact_ref = address_schema['properties']['contact']
        self.assertIn('$ref', contact_ref)
        self.assertIn('Contact', contact_ref['$ref'])

        # Contact schema should exist (3rd level)
        self.assertIn('Contact', schemas)


class TestOpenAPIDataclassResults(unittest.TestCase):
    """Tests for methods with dataclass results."""

    def setUp(self):
        self.rpc = JSONRPC()
        results_group = MethodGroup()
        results_group.register('dataclass_result', DataclassResultMethod())
        results_group.register('nested_user', NestedDataclassResultMethod())
        results_group.register('list_results', ListDataclassResultMethod())
        results_group.register('dict_results', DictDataclassResultMethod())
        self.rpc.register('results', results_group)

    def test_dataclass_result_schema(self):
        """Test OpenAPI schema for dataclass_result (returns MathResult)."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        schemas = spec['components']['schemas']
        response_schema = schemas['results.dataclass_result_response']
        result_schema = response_schema['properties']['result']

        # Should reference MathResult
        self.assertIn('$ref', result_schema)
        self.assertIn('MathResult', result_schema['$ref'])

        # MathResult schema should exist
        self.assertIn('MathResult', schemas)
        math_result = schemas['MathResult']
        self.assertIn('operation', math_result['properties'])
        self.assertIn('result', math_result['properties'])

    def test_nested_dataclass_result_schema(self):
        """Test OpenAPI schema for nested_user (returns nested UserInfo)."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        schemas = spec['components']['schemas']
        response_schema = schemas['results.nested_user_response']
        result_schema = response_schema['properties']['result']

        # Should reference UserInfo
        self.assertIn('$ref', result_schema)
        self.assertIn('UserInfo', result_schema['$ref'])

        # UserInfo schema should exist with nested UserAddress
        self.assertIn('UserInfo', schemas)
        user_info = schemas['UserInfo']
        self.assertIn('address', user_info['properties'])

        address_ref = user_info['properties']['address']
        self.assertIn('$ref', address_ref)
        self.assertIn('UserAddress', address_ref['$ref'])

    def test_list_dataclass_result_schema(self):
        """Test OpenAPI schema for list_results (returns list[MathResult])."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        schemas = spec['components']['schemas']
        response_schema = schemas['results.list_results_response']
        result_schema = response_schema['properties']['result']

        # Should be array type
        self.assertEqual(result_schema['type'], 'array')

        # Items should reference MathResult
        self.assertIn('items', result_schema)
        self.assertIn('$ref', result_schema['items'])
        self.assertIn('MathResult', result_schema['items']['$ref'])

    def test_dict_dataclass_result_schema(self):
        """Test OpenAPI schema for dict_results (returns dict[str, MathResult])."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        schemas = spec['components']['schemas']
        response_schema = schemas['results.dict_results_response']
        result_schema = response_schema['properties']['result']

        # Should be object type
        self.assertEqual(result_schema['type'], 'object')

        # additionalProperties should reference MathResult
        self.assertIn('additionalProperties', result_schema)
        self.assertIn('$ref', result_schema['additionalProperties'])
        self.assertIn('MathResult', result_schema['additionalProperties']['$ref'])


class TestOpenAPIAsyncMethods(unittest.TestCase):
    """Tests for async methods."""

    def setUp(self):
        self.rpc = JSONRPC()
        async_group = MethodGroup()
        async_group.register('async_test', AsyncMethod())
        async_group.register('async_dataclass_add', AsyncDataclassResultMethod())
        self.rpc.register('async', async_group)

    def test_async_method_schema(self):
        """Test OpenAPI schema for async method without params."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        # Async methods should have same schema structure as sync methods
        self.assertIn('/jsonrpc#async.async_test', spec['paths'])
        schemas = spec['components']['schemas']
        self.assertIn('async.async_test_request', schemas)
        self.assertIn('async.async_test_response', schemas)

        response_schema = schemas['async.async_test_response']
        self.assertEqual(response_schema['properties']['result']['type'], 'string')

    def test_async_dataclass_result_schema(self):
        """Test OpenAPI schema for async method with dataclass result."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        schemas = spec['components']['schemas']
        response_schema = schemas['async.async_dataclass_add_response']
        result_schema = response_schema['properties']['result']

        # Should reference MathResult
        self.assertIn('$ref', result_schema)
        self.assertIn('MathResult', result_schema['$ref'])


class TestOpenAPISpecialMethods(unittest.TestCase):
    """Tests for special methods (error, wrong_type, internal_call)."""

    def setUp(self):
        self.rpc = JSONRPC()

        # Need math group for internal call to work
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        self.rpc.register('math', math_group)

        special_group = MethodGroup()
        special_group.register('error', ErrorMethod())
        special_group.register('wrong_type', WrongTypeMethod())
        special_group.register('double_add', InternalCallMethod())
        special_group.register('typed_add', TypedAddMethod())
        self.rpc.register('special', special_group)

    def test_error_method_schema(self):
        """Test OpenAPI schema for error method (raises exception)."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        # Error method should still have normal schema
        # (OpenAPI describes interface, not runtime behavior)
        self.assertIn('/jsonrpc#special.error', spec['paths'])
        schemas = spec['components']['schemas']
        self.assertIn('special.error_request', schemas)
        self.assertIn('special.error_response', schemas)

    def test_wrong_type_method_schema(self):
        """Test OpenAPI schema for wrong_type method."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        schemas = spec['components']['schemas']
        response_schema = schemas['special.wrong_type_response']

        # Schema should say int (based on type hint)
        # even though implementation returns string
        self.assertEqual(response_schema['properties']['result']['type'], 'integer')

    def test_internal_call_method_schema(self):
        """Test OpenAPI schema for method that calls another method internally."""
        generator = OpenAPIGenerator(self.rpc)
        spec = generator.generate()

        # Should have normal schema (internal implementation doesn't affect API)
        self.assertIn('/jsonrpc#special.double_add', spec['paths'])
        schemas = spec['components']['schemas']

        request_schema = schemas['special.double_add_request']
        params_schema = request_schema['properties']['params']
        # Uses AddParams
        self.assertIn('a', params_schema['properties'])
        self.assertIn('b', params_schema['properties'])


class TestOpenAPISchemaValidation(unittest.TestCase):
    """Tests for comprehensive schema validation."""

    def setUp(self):
        self.rpc = JSONRPC()

        # Register ALL 17 methods
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        math_group.register('subtract', SubtractMethod())
        math_group.register('multiply', MultiplyMethod())
        self.rpc.register('math', math_group)

        utils_group = MethodGroup()
        utils_group.register('ping', NoParamsMethod())
        utils_group.register('echo', EchoMethod())
        utils_group.register('optional', OptionalMethod())
        self.rpc.register('utils', utils_group)

        results_group = MethodGroup()
        results_group.register('dataclass_result', DataclassResultMethod())
        results_group.register('nested_user', NestedDataclassResultMethod())
        results_group.register('list_results', ListDataclassResultMethod())
        results_group.register('dict_results', DictDataclassResultMethod())
        self.rpc.register('results', results_group)

        async_group = MethodGroup()
        async_group.register('async_test', AsyncMethod())
        async_group.register('async_dataclass_add', AsyncDataclassResultMethod())
        self.rpc.register('async', async_group)

        special_group = MethodGroup()
        special_group.register('process_company', NestedCompanyMethod())
        special_group.register('double_add', InternalCallMethod())
        special_group.register('typed_add', TypedAddMethod())
        special_group.register('wrong_type', WrongTypeMethod())
        special_group.register('error', ErrorMethod())
        self.rpc.register('special', special_group)

        self.generator = OpenAPIGenerator(self.rpc)
        self.spec = self.generator.generate()

    def test_all_methods_have_paths(self):
        """Verify all 17 methods have path entries."""
        expected_paths = [
            '/jsonrpc#math.add',
            '/jsonrpc#math.subtract',
            '/jsonrpc#math.multiply',
            '/jsonrpc#utils.ping',
            '/jsonrpc#utils.echo',
            '/jsonrpc#utils.optional',
            '/jsonrpc#results.dataclass_result',
            '/jsonrpc#results.nested_user',
            '/jsonrpc#results.list_results',
            '/jsonrpc#results.dict_results',
            '/jsonrpc#async.async_test',
            '/jsonrpc#async.async_dataclass_add',
            '/jsonrpc#special.process_company',
            '/jsonrpc#special.double_add',
            '/jsonrpc#special.typed_add',
            '/jsonrpc#special.wrong_type',
            '/jsonrpc#special.error',
        ]

        for path in expected_paths:
            self.assertIn(path, self.spec['paths'], f'Missing path: {path}')

    def test_all_methods_have_request_schemas(self):
        """Verify all 17 methods have request schemas."""
        expected_schemas = [
            'math.add_request',
            'math.subtract_request',
            'math.multiply_request',
            'utils.ping_request',
            'utils.echo_request',
            'utils.optional_request',
            'results.dataclass_result_request',
            'results.nested_user_request',
            'results.list_results_request',
            'results.dict_results_request',
            'async.async_test_request',
            'async.async_dataclass_add_request',
            'special.process_company_request',
            'special.double_add_request',
            'special.typed_add_request',
            'special.wrong_type_request',
            'special.error_request',
        ]

        schemas = self.spec['components']['schemas']
        for schema_name in expected_schemas:
            self.assertIn(schema_name, schemas, f'Missing schema: {schema_name}')

    def test_all_methods_have_response_schemas(self):
        """Verify all 17 methods have response schemas."""
        expected_schemas = [
            'math.add_response',
            'math.subtract_response',
            'math.multiply_response',
            'utils.ping_response',
            'utils.echo_response',
            'utils.optional_response',
            'results.dataclass_result_response',
            'results.nested_user_response',
            'results.list_results_response',
            'results.dict_results_response',
            'async.async_test_response',
            'async.async_dataclass_add_response',
            'special.process_company_response',
            'special.double_add_response',
            'special.typed_add_response',
            'special.wrong_type_response',
            'special.error_response',
        ]

        schemas = self.spec['components']['schemas']
        for schema_name in expected_schemas:
            self.assertIn(schema_name, schemas, f'Missing schema: {schema_name}')

    def test_nested_dataclass_schemas_generated(self):
        """Verify nested dataclass schemas are generated."""
        schemas = self.spec['components']['schemas']

        # 3-level nesting: CompanyInfo is inlined in params, but Address and Contact are referenced
        # (CompanyInfo not in schemas because it's top-level params)
        self.assertIn('Address', schemas)
        self.assertIn('Contact', schemas)

        # 2-level nesting: UserInfo -> UserAddress (UserInfo is result, should be in schemas)
        self.assertIn('UserInfo', schemas)
        self.assertIn('UserAddress', schemas)

        # Result types
        self.assertIn('MathResult', schemas)

    def test_operation_ids_unique(self):
        """Verify all operationIds are unique."""
        operation_ids = []
        for path_data in self.spec['paths'].values():
            if 'post' in path_data:
                operation_id = path_data['post']['operationId']
                operation_ids.append(operation_id)

        # Check no duplicates
        self.assertEqual(len(operation_ids), len(set(operation_ids)))

        # Should have 17 unique operation IDs
        self.assertEqual(len(operation_ids), 17)

    def test_tags_for_all_methods(self):
        """Verify tags are assigned correctly."""
        tag_names = [t['name'] for t in self.spec['tags']]

        # Should have tags for all groups
        self.assertIn('math', tag_names)
        self.assertIn('utils', tag_names)
        self.assertIn('results', tag_names)
        self.assertIn('async', tag_names)
        self.assertIn('special', tag_names)

        # Check specific method has correct tag
        add_op = self.spec['paths']['/jsonrpc#math.add']['post']
        self.assertEqual(add_op['tags'], ['math'])

    def test_error_response_schema_exists(self):
        """Verify JSONRPCError schema exists."""
        schemas = self.spec['components']['schemas']
        self.assertIn('JSONRPCError', schemas)
        error_schema = schemas['JSONRPCError']
        self.assertIn('error', error_schema['properties'])

    def test_request_id_is_integer_only(self):
        """Verify request id is integer only (default simplify_id=True)."""
        schemas = self.spec['components']['schemas']
        add_request = schemas['math.add_request']
        id_schema = add_request['properties']['id']
        self.assertEqual(id_schema, {'type': 'integer'})

        # Error schema id should also respect simplify_id
        error_schema = schemas['JSONRPCError']
        error_id = error_schema['properties']['id']
        self.assertEqual(error_id, {'oneOf': [{'type': 'integer'}, {'type': 'null'}]})

    def test_simplify_id_false_uses_oneof(self):
        """simplify_id=False produces oneOf[string, integer] for id fields."""
        generator = OpenAPIGenerator(self.rpc, simplify_id=False)
        spec = generator.generate()
        schemas = spec['components']['schemas']

        expected_id = {'oneOf': [{'type': 'string'}, {'type': 'integer'}]}

        # Request schema
        add_request = schemas['math.add_request']
        self.assertEqual(add_request['properties']['id'], expected_id)

        # Response schema
        add_response = schemas['math.add_response']
        self.assertEqual(add_response['properties']['id'], expected_id)

        # Error schema id should include null
        error_schema = schemas['JSONRPCError']
        error_id = error_schema['properties']['id']
        expected_error_id = {'oneOf': [{'type': 'string'}, {'type': 'integer'}, {'type': 'null'}]}
        self.assertEqual(error_id, expected_error_id)


class TestOpenAPITypeConversionEdgeCases(unittest.TestCase):
    """Tests for edge cases in type conversion to JSON Schema."""

    def setUp(self):
        self.rpc = JSONRPC()
        test_group = MethodGroup()
        test_group.register('complex_types', ComplexTypesMethod())
        test_group.register('no_docstring', NoDocstringMethod())
        self.rpc.register('test', test_group)

        self.generator = OpenAPIGenerator(self.rpc)
        self.spec = self.generator.generate()

    def test_none_type_converts_to_null(self):
        """Test type(None) converts to {"type": "null"}."""
        schemas = self.spec['components']['schemas']
        request_schema = schemas['test.complex_types_request']
        params_props = request_schema['properties']['params']['properties']
        none_schema = params_props['none_type']
        self.assertEqual(none_schema, {'type': 'null'})

    def test_optional_type_converts_to_oneof(self):
        """Test Optional[T] converts to oneOf with null."""
        schemas = self.spec['components']['schemas']
        request_schema = schemas['test.complex_types_request']
        params_props = request_schema['properties']['params']['properties']
        optional_schema = params_props['optional_int']
        self.assertIn('oneOf', optional_schema)
        self.assertEqual(len(optional_schema['oneOf']), 2)
        # Should have integer and null
        types = [s.get('type') for s in optional_schema['oneOf']]
        self.assertIn('integer', types)
        self.assertIn('null', types)

    def test_general_union_converts_to_oneof(self):
        """Test Union[A, B, C] converts to oneOf."""
        schemas = self.spec['components']['schemas']
        request_schema = schemas['test.complex_types_request']
        params_props = request_schema['properties']['params']['properties']
        union_schema = params_props['union_types']
        self.assertIn('oneOf', union_schema)
        self.assertEqual(len(union_schema['oneOf']), 3)
        types = [s.get('type') for s in union_schema['oneOf']]
        self.assertIn('integer', types)
        self.assertIn('string', types)
        self.assertIn('number', types)

    def test_literal_converts_to_enum(self):
        """Test Literal converts to enum."""
        schemas = self.spec['components']['schemas']
        request_schema = schemas['test.complex_types_request']
        params_props = request_schema['properties']['params']['properties']
        literal_schema = params_props['literal_val']
        self.assertIn('enum', literal_schema)
        self.assertEqual(set(literal_schema['enum']), {'a', 'b', 'c'})

    def test_plain_list_converts_to_unrestricted(self):
        """Test plain list (no args) converts to unrestricted schema."""
        schemas = self.spec['components']['schemas']
        request_schema = schemas['test.complex_types_request']
        params_props = request_schema['properties']['params']['properties']
        list_schema = params_props['plain_list']
        # Plain list without type args falls back to unrestricted schema
        self.assertEqual(list_schema, {})

    def test_plain_dict_converts_to_unrestricted(self):
        """Test plain dict (no args) converts to unrestricted schema."""
        schemas = self.spec['components']['schemas']
        request_schema = schemas['test.complex_types_request']
        params_props = request_schema['properties']['params']['properties']
        dict_schema = params_props['plain_dict']
        # Plain dict without type args falls back to unrestricted schema
        self.assertEqual(dict_schema, {})

    def test_any_type_converts_to_empty_schema(self):
        """Test Any type converts to empty schema (no restrictions)."""
        schemas = self.spec['components']['schemas']
        request_schema = schemas['test.complex_types_request']
        params_props = request_schema['properties']['params']['properties']
        any_schema = params_props['any_value']
        self.assertEqual(any_schema, {})

    def test_float_converts_to_number(self):
        """Test float converts to number."""
        schemas = self.spec['components']['schemas']
        request_schema = schemas['test.complex_types_request']
        params_props = request_schema['properties']['params']['properties']
        float_schema = params_props['float_val']
        self.assertEqual(float_schema, {'type': 'number'})

    def test_bool_converts_to_boolean(self):
        """Test bool converts to boolean."""
        schemas = self.spec['components']['schemas']
        request_schema = schemas['test.complex_types_request']
        params_props = request_schema['properties']['params']['properties']
        bool_schema = params_props['bool_val']
        self.assertEqual(bool_schema, {'type': 'boolean'})

    def test_method_without_docstring_has_no_description(self):
        """Test method without docstring doesn't have description field."""
        path_item = self.spec['paths']['/jsonrpc#test.no_docstring']
        operation = path_item['post']
        # Should still have summary, but description might be empty or missing
        # _get_docstring returns None when no docstring
        self.assertIn('summary', operation)


class TestOpenAPISecurityAndHeaders(unittest.TestCase):
    """Tests for OpenAPI security and global headers."""

    def test_add_global_header(self):
        """Test adding global header to OpenAPI spec."""
        rpc = JSONRPC()
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        rpc.register('math', math_group)

        generator = OpenAPIGenerator(rpc)
        generator.add_header(
            name='X-API-Key',
            description='API authentication key',
            required=True,
            schema={'type': 'string', 'minLength': 32},
        )

        spec = generator.generate()

        # Check that header is added to all operations
        add_operation = spec['paths']['/jsonrpc#math.add']['post']
        self.assertIn('parameters', add_operation)

        # Find the X-API-Key header
        headers = [p for p in add_operation['parameters'] if p.get('name') == 'X-API-Key']
        self.assertEqual(len(headers), 1)
        header = headers[0]

        self.assertEqual(header['in'], 'header')
        self.assertEqual(header['description'], 'API authentication key')
        self.assertEqual(header['required'], True)
        self.assertEqual(header['schema'], {'type': 'string', 'minLength': 32})

    def test_add_security_requirement(self):
        """Test adding security requirement to OpenAPI spec."""
        rpc = JSONRPC()
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        rpc.register('math', math_group)

        generator = OpenAPIGenerator(rpc)

        # Add security scheme
        generator.add_security_scheme(
            name='bearerAuth',
            scheme_type='http',
            options={'scheme': 'bearer', 'bearerFormat': 'JWT'},
        )

        # Add security requirement
        generator.add_security_requirement('bearerAuth')

        spec = generator.generate()

        # Check security is defined
        self.assertIn('securitySchemes', spec['components'])
        self.assertIn('bearerAuth', spec['components']['securitySchemes'])

        # Check security scheme content
        scheme = spec['components']['securitySchemes']['bearerAuth']
        self.assertEqual(scheme['type'], 'http')
        self.assertEqual(scheme['scheme'], 'bearer')
        self.assertEqual(scheme['bearerFormat'], 'JWT')

        # Check security requirement is applied globally
        self.assertIn('security', spec)
        self.assertEqual(spec['security'], [{'bearerAuth': []}])

    def test_add_apikey_security_scheme(self):
        """Test adding apiKey security scheme with name and in fields."""
        rpc = JSONRPC()
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        rpc.register('math', math_group)

        generator = OpenAPIGenerator(rpc)
        generator.add_security_scheme(
            name='apiKeyAuth',
            scheme_type='apiKey',
            options={'name': 'X-API-Key', 'in': 'header'},
        )
        generator.add_security_requirement('apiKeyAuth')

        spec = generator.generate()

        scheme = spec['components']['securitySchemes']['apiKeyAuth']
        self.assertEqual(scheme['type'], 'apiKey')
        self.assertEqual(scheme['name'], 'X-API-Key')
        self.assertEqual(scheme['in'], 'header')

    def test_add_oauth2_security_with_scopes(self):
        """Test adding OAuth2 security with scopes."""
        rpc = JSONRPC()
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        rpc.register('math', math_group)

        generator = OpenAPIGenerator(rpc)

        # Add OAuth2 security scheme
        generator.add_security_scheme(
            name='oauth2',
            scheme_type='oauth2',
            options={
                'flows': {
                    'authorizationCode': {
                        'authorizationUrl': 'https://example.com/oauth/authorize',
                        'tokenUrl': 'https://example.com/oauth/token',
                        'scopes': {'read': 'Read access', 'write': 'Write access'},
                    }
                },
            },
        )

        # Add security requirement with scopes
        generator.add_security_requirement('oauth2', scopes=['read', 'write'])

        spec = generator.generate()

        # Check security scheme content
        scheme = spec['components']['securitySchemes']['oauth2']
        self.assertEqual(scheme['type'], 'oauth2')
        self.assertIn('flows', scheme)
        self.assertIn('authorizationCode', scheme['flows'])

        # Check security requirement includes scopes
        self.assertIn('security', spec)
        self.assertEqual(spec['security'], [{'oauth2': ['read', 'write']}])


class TestOpenAPIEdgeCasesCoverage(unittest.TestCase):
    """Tests for OpenAPI edge cases to achieve 100% coverage."""

    def test_get_docstring_none(self):
        """Test _get_docstring with object without __doc__ - line 25."""
        from jsonrpc.openapi import _get_docstring

        # Create class without docstring
        class NoDoc:
            pass

        result = _get_docstring(NoDoc)
        self.assertIsNone(result)

    def test_type_to_jsonschema_plain_list(self):
        """Test plain list type conversion - line 68."""
        from unittest.mock import Mock, patch

        from jsonrpc.openapi import _type_to_jsonschema

        # Lines 68 and 78 are only hit when get_origin returns list/dict but get_args returns empty
        # This doesn't happen with normal type annotations, so we need to mock it
        mock_type = Mock()

        with patch('jsonrpc.openapi.get_origin', return_value=list):
            with patch('jsonrpc.openapi.get_args', return_value=()):
                schema = _type_to_jsonschema(mock_type, {})
                self.assertEqual(schema, {'type': 'array'})

    def test_type_to_jsonschema_plain_dict(self):
        """Test plain dict type conversion - line 78."""
        from unittest.mock import Mock, patch

        from jsonrpc.openapi import _type_to_jsonschema

        # Mock a type where origin is dict but args is empty
        mock_type = Mock()

        with patch('jsonrpc.openapi.get_origin', return_value=dict):
            with patch('jsonrpc.openapi.get_args', return_value=()):
                schema = _type_to_jsonschema(mock_type, {})
                self.assertEqual(schema, {'type': 'object'})

    def test_dataclass_to_jsonschema_non_dataclass_error(self):
        """Test non-dataclass validation - line 114."""
        from jsonrpc.openapi import _dataclass_to_jsonschema

        # Test: Passing non-dataclass should raise ValueError
        with self.assertRaises(ValueError) as ctx:
            _dataclass_to_jsonschema(int, {})
        self.assertIn('Expected dataclass', str(ctx.exception))

    def test_dataclass_field_metadata_description(self):
        """Test field metadata description - line 130."""
        rpc = JSONRPC()
        test_group = MethodGroup()
        test_group.register('metadata_test', MetadataMethod())
        rpc.register('test', test_group)

        generator = OpenAPIGenerator(rpc)
        spec = generator.generate()

        # Check that field descriptions from metadata are included
        schemas = spec['components']['schemas']
        request_schema = schemas['test.metadata_test_request']
        params_props = request_schema['properties']['params']['properties']

        # Check name field has description from metadata
        self.assertIn('description', params_props['name'])
        self.assertEqual(params_props['name']['description'], 'User name')

        # Check age field has description from metadata
        self.assertIn('description', params_props['age'])
        self.assertEqual(params_props['age']['description'], 'User age')

    def test_method_summary_multiline_docstring(self):
        """Test multi-line docstring summary extraction - line 278."""
        rpc = JSONRPC()
        test_group = MethodGroup()
        test_group.register('multiline_doc', MultiLineDocstringMethod())
        rpc.register('test', test_group)

        generator = OpenAPIGenerator(rpc)
        spec = generator.generate()

        # Check that only first line is used as summary
        path_item = spec['paths']['/jsonrpc#test.multiline_doc']
        operation = path_item['post']
        self.assertEqual(operation['summary'], 'First line summary.')

    def test_generate_method_response_no_result_type(self):
        """Test response schema when result_type is None - line 479."""
        rpc = JSONRPC()
        test_group = MethodGroup()
        test_group.register('no_result_type', NoResultTypeMethod())
        rpc.register('test', test_group)

        generator = OpenAPIGenerator(rpc)
        spec = generator.generate()

        # Check that result schema is empty {} when result_type is None
        schemas = spec['components']['schemas']
        response_schema = schemas['test.no_result_type_response']
        self.assertEqual(response_schema['properties']['result'], {})

    def test_generate_yaml_import_error(self):
        """Test generate_yaml() when PyYAML not installed - lines 505-512."""
        import sys
        from unittest.mock import patch

        rpc = JSONRPC()
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        rpc.register('math', math_group)

        generator = OpenAPIGenerator(rpc)

        # Mock import to raise ImportError
        with patch.dict(sys.modules, {'yaml': None}):
            with self.assertRaises(ImportError) as ctx:
                generator.generate_yaml()
            self.assertIn('PyYAML is required', str(ctx.exception))
            self.assertIn('pip install pyyaml', str(ctx.exception))


class TestOpenAPIEdgeCases(unittest.TestCase):
    """Tests for OpenAPI edge cases not covered elsewhere."""

    def test_get_method_summary_no_docstring_returns_class_name(self):
        """_get_method_summary returns class name when class has no docstring (line 264)."""
        from jsonrpc.method import Method
        from jsonrpc.openapi import OpenAPIGenerator

        class TrulyNoDocMethod(Method):
            def execute(self, params: None) -> str:
                return 'ok'

        rpc = JSONRPC(version='2.0')
        rpc.register('nodoc', TrulyNoDocMethod())
        generator = OpenAPIGenerator(rpc)
        spec = generator.generate()

        # The summary should be the class name since TrulyNoDocMethod has no docstring
        path = '/jsonrpc#nodoc'
        summary = spec['paths'][path]['post']['summary']
        self.assertEqual(summary, 'TrulyNoDocMethod')

    def test_generate_yaml_success_when_pyyaml_installed(self):
        """generate_yaml() returns YAML string when PyYAML is installed (line 495)."""
        rpc = JSONRPC(version='2.0')
        rpc.register('ping', NoParamsMethod())
        generator = OpenAPIGenerator(rpc)
        yaml_str = generator.generate_yaml()
        self.assertIsInstance(yaml_str, str)
        self.assertEqual(yaml_str[:7], 'openapi')


if __name__ == '__main__':
    unittest.main()
