"""Tests for internal JSON-RPC library API.

This module tests the internal library API (non-protocol methods).

For protocol-level tests (handle(), JSON-RPC compliance), see test_jsonrpc_v*.py
"""

import asyncio
import unittest

from jsonrpc import JSONRPC, InvalidParamsError, InvalidResultError, MethodNotFoundError
from jsonrpc.method import Method, MethodGroup
from tests.fixtures import (
    AddMethod,
    AsyncDataclassResultMethod,
    AsyncMethod,
    DataclassResultMethod,
    DictDataclassResultMethod,
    InternalCallMethod,
    ListDataclassResultMethod,
    MathResult,
    NestedDataclassResultMethod,
    NoParamsMethod,
    OptionalMethod,
    SubtractMethod,
    UserAddress,
    UserInfo,
    WrongTypeMethod,
)


class TestMethodGroup(unittest.TestCase):
    """Tests for MethodGroup error cases and edge cases."""

    def test_register_duplicate_method_error(self):
        """Test that registering duplicate method raises ValueError."""
        group = MethodGroup()
        group.register('add', AddMethod())

        with self.assertRaises(ValueError) as ctx:
            group.register('add', AddMethod())
        self.assertIn('already registered', str(ctx.exception))

    def test_register_non_method_class_raises_error(self):
        """Test MethodGroup.register() with non-Method class raises TypeError."""
        group = MethodGroup()

        class NotAMethod:
            pass

        with self.assertRaises(TypeError) as ctx:
            group.register('test', NotAMethod())

        self.assertIn('Expected Method or MethodGroup instance', str(ctx.exception))

    def test_unregister_method(self):
        """Test that method can be unregistered from group."""
        group = MethodGroup()
        group.register('add', AddMethod())

        self.assertIn('add', group.list_methods())
        group.unregister('add')
        self.assertNotIn('add', group.list_methods())

    def test_unregister_subgroup(self):
        """Test that subgroup can be unregistered from group."""
        parent = MethodGroup()
        child = MethodGroup()
        child.register('add', AddMethod())
        parent.register('math', child)

        self.assertIsNotNone(parent.get_subgroup('math'))
        parent.unregister('math')
        self.assertIsNone(parent.get_subgroup('math'))

    def test_unregister_nonexistent_raises_key_error(self):
        """Test that unregistering nonexistent name raises KeyError."""
        group = MethodGroup()

        with self.assertRaises(KeyError):
            group.unregister('nonexistent')

    def test_get_method_returns_none_for_nonexistent(self):
        """Test that get_method returns None for nonexistent method."""
        group = MethodGroup()
        group.register('add', AddMethod())

        method = group.get_method('nonexistent')
        self.assertIsNone(method)

    def test_register_method_before_group_has_rpc(self):
        """Test registering method to group before group has RPC reference - line 469."""
        # Create a group without RPC reference
        group = MethodGroup()

        # Ensure no 'rpc' attribute exists
        if hasattr(group, 'rpc'):
            delattr(group, 'rpc')

        # Register method - should succeed without setting rpc on instance
        group.register('add', AddMethod())

        # Verify method is registered
        self.assertEqual(len(group.list_methods()), 1)
        self.assertIn('add', group.list_methods())

        # Now register group to JSONRPC and add another method
        rpc = JSONRPC()
        rpc.register('test', group)

        # Register another method - this one gets rpc immediately
        group.register('subtract', SubtractMethod())
        self.assertEqual(len(group.list_methods()), 2)


class TestMethodTypeValidation(unittest.TestCase):
    """Tests for Method type hint validation at class definition."""

    def test_method_without_params_type_hint_error(self):
        """Test that Method without params type hint raises TypeError."""
        with self.assertRaises(TypeError) as ctx:

            class BrokenMethod(Method):
                name = 'broken'

                def execute(self, params) -> str:  # Missing type hint
                    return 'test'

        self.assertIn("must have type hint for 'params'", str(ctx.exception))

    def test_method_without_return_type_hint_error(self):
        """Test that Method without return type hint raises TypeError."""
        with self.assertRaises(TypeError) as ctx:

            class BrokenMethod(Method):
                name = 'broken'

                def execute(self, params: None):  # Missing return type
                    return 'test'

        self.assertIn('must have return type annotation', str(ctx.exception))

    def test_method_with_non_dataclass_params_error(self):
        """Test that Method with non-dataclass params raises TypeError."""
        with self.assertRaises(TypeError) as ctx:

            class BrokenMethod(Method):
                name = 'broken'

                def execute(self, params: dict) -> str:  # dict, not dataclass
                    return 'test'

        self.assertIn('must be a dataclass or None', str(ctx.exception))

    def test_method_without_params_parameter_error(self):
        """Test Method without 'params' parameter raises TypeError."""
        with self.assertRaises(TypeError) as ctx:

            class BrokenMethod(Method):
                name = 'broken'

                def execute(self) -> str:  # Missing params parameter
                    return 'bad'

        self.assertIn("must have 'params' parameter", str(ctx.exception))

    def test_method_with_wrong_parameter_name_error(self):
        """Test Method with wrong parameter name raises TypeError."""
        with self.assertRaises(TypeError) as ctx:

            class BrokenMethod(Method):
                name = 'broken'

                def execute(self, data: None) -> str:  # Wrong param name
                    return 'bad'

        self.assertIn("must have 'params' as second parameter", str(ctx.exception))
        self.assertIn("got 'data'", str(ctx.exception))

    def test_method_execute_not_implemented_error(self):
        """Test calling base Method.execute() raises NotImplementedError."""
        method = Method()
        with self.assertRaises(NotImplementedError) as ctx:
            method.execute(None)

        self.assertIn('must implement execute', str(ctx.exception))

    # NOTE: Method._get_name() tests removed - name is now specified during registration, not as a class attribute
    # The new API uses group.register('name', MethodInstance()) instead of method.name


class TestJSONRPCFacade(unittest.TestCase):
    """Tests for JSONRPC facade methods."""

    def test_jsonrpc_unregister_group(self):
        """Test JSONRPC.unregister() removes a named group."""
        rpc = JSONRPC(version='2.0')
        math = MethodGroup()
        math.register('add', AddMethod())
        rpc.register('math', math)

        result = rpc.call_method('math.add', {'a': 1, 'b': 2})
        self.assertEqual(result, 3)

        rpc.unregister('math')

        with self.assertRaises(MethodNotFoundError):
            rpc.call_method('math.add', {'a': 1, 'b': 2})

    def test_jsonrpc_unregister_root_method(self):
        """Test JSONRPC.unregister() removes a root-level method."""
        rpc = JSONRPC(version='2.0')
        rpc.register('ping', NoParamsMethod())

        result = rpc.call_method('ping')
        self.assertEqual(result, 'pong')

        rpc.unregister('ping')

        with self.assertRaises(MethodNotFoundError):
            rpc.call_method('ping')

    def test_jsonrpc_unregister_nested_method(self):
        """Test JSONRPC.unregister() removes a method by dotted path."""
        rpc = JSONRPC(version='2.0')
        math = MethodGroup()
        math.register('add', AddMethod())
        math.register('subtract', SubtractMethod())
        rpc.register('math', math)

        rpc.unregister('math.add')

        with self.assertRaises(MethodNotFoundError):
            rpc.call_method('math.add', {'a': 1, 'b': 2})

        result = rpc.call_method('math.subtract', {'a': 5, 'b': 3})
        self.assertEqual(result, 2)

    def test_jsonrpc_unregister_nonexistent_raises_key_error(self):
        """Test JSONRPC.unregister() raises KeyError for unknown name."""
        rpc = JSONRPC(version='2.0')

        with self.assertRaises(KeyError):
            rpc.unregister('nonexistent')

    def test_jsonrpc_unregister_then_reregister(self):
        """Test re-registering after unregister works."""
        rpc = JSONRPC(version='2.0')
        math = MethodGroup()
        math.register('add', AddMethod())
        rpc.register('math', math)
        rpc.unregister('math')

        math2 = MethodGroup()
        math2.register('subtract', SubtractMethod())
        rpc.register('math', math2)

        result = rpc.call_method('math.subtract', {'a': 10, 'b': 3})
        self.assertEqual(result, 7)

        with self.assertRaises(MethodNotFoundError):
            rpc.call_method('math.add', {'a': 1, 'b': 2})

    def test_unregister_method_then_reregister_same_instance(self):
        """Test re-registering the same Method instance after unregister."""
        rpc = JSONRPC(version='2.0')
        math = MethodGroup()
        add = AddMethod()
        math.register('add', add)
        rpc.register('math', math)

        self.assertEqual(rpc.call_method('math.add', {'a': 1, 'b': 2}), 3)

        rpc.unregister('math.add')
        # Same instance should be re-registerable after unregister
        math.register('add', add)
        self.assertEqual(rpc.call_method('math.add', {'a': 5, 'b': 3}), 8)

    def test_unregister_subgroup_clears_rpc_on_children(self):
        """Test unregistering a subgroup clears .rpc on all nested methods."""
        rpc = JSONRPC(version='2.0')
        math = MethodGroup()
        add = AddMethod()
        sub = SubtractMethod()
        math.register('add', add)
        math.register('subtract', sub)
        rpc.register('math', math)

        self.assertTrue(hasattr(add, 'rpc'))
        self.assertTrue(hasattr(sub, 'rpc'))

        rpc.unregister('math')
        self.assertFalse(hasattr(add, 'rpc'))
        self.assertFalse(hasattr(sub, 'rpc'))

    def test_invalid_version_raises_value_error(self):
        """Test that invalid version raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            JSONRPC(version='3.0')
        self.assertIn("'1.0' or '2.0'", str(ctx.exception))

    def test_max_concurrent_zero_raises_value_error(self):
        """Test that max_concurrent=0 raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            JSONRPC(max_concurrent=0)
        self.assertIn('max_concurrent', str(ctx.exception))

    def test_max_concurrent_negative_two_raises_value_error(self):
        """Test that max_concurrent=-2 raises ValueError."""
        with self.assertRaises(ValueError):
            JSONRPC(max_concurrent=-2)

    def test_max_concurrent_minus_one_is_valid(self):
        """Test that max_concurrent=-1 (unlimited) is accepted."""
        rpc = JSONRPC(max_concurrent=-1)
        self.assertEqual(rpc._effective_max_concurrent, -1)


class TestCallMethod(unittest.TestCase):
    """Comprehensive tests for call_method() internal API."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        math_group.register('subtract', SubtractMethod())
        self.rpc.register('math', math_group)

        self.rpc.register('ping', NoParamsMethod())
        self.rpc.register('optional', OptionalMethod())
        self.rpc.register('dataclass_result', DataclassResultMethod())
        self.rpc.register('nested_user', NestedDataclassResultMethod())
        self.rpc.register('list_results', ListDataclassResultMethod())
        self.rpc.register('dict_results', DictDataclassResultMethod())

    def test_call_method_none_params_explicit(self):
        """Test call_method with params=None explicitly."""
        result = self.rpc.call_method('ping', params=None)
        self.assertEqual(result, 'pong')

    def test_call_method_empty_list_noparams_method(self):
        """Test call_method with empty list [] for NoParams method."""
        result = self.rpc.call_method('ping', params=[])
        self.assertEqual(result, 'pong')

    def test_call_method_empty_dict_noparams_method(self):
        """Test call_method with empty dict {} for NoParams method."""
        result = self.rpc.call_method('ping', params={})
        self.assertEqual(result, 'pong')

    def test_call_method_optional_params_omitted(self):
        """Test call_method with optional param omitted (uses default)."""
        result = self.rpc.call_method('optional', {'required': 'test'})
        self.assertEqual(result, 'test:default')

    def test_call_method_optional_params_provided(self):
        """Test call_method with optional param provided."""
        result = self.rpc.call_method('optional', {'required': 'test', 'optional': 'custom'})
        self.assertEqual(result, 'test:custom')

    def test_call_method_dataclass_result_returns_object(self):
        """Test call_method returns actual dataclass object, not JSON."""
        result = self.rpc.call_method('dataclass_result', [5, 3])

        # Should return dataclass instance, not dict
        self.assertIsInstance(result, MathResult)
        self.assertEqual(result.operation, 'add')
        self.assertEqual(result.result, 8)

    def test_call_method_nested_dataclass_result(self):
        """Test call_method with nested dataclass result."""
        result = self.rpc.call_method('nested_user')

        self.assertIsInstance(result, UserInfo)
        self.assertEqual(result.name, 'Jakub')
        self.assertIsInstance(result.address, UserAddress)
        self.assertEqual(result.address.city, 'Krakow')

    def test_call_method_list_dataclass_result(self):
        """Test call_method with list[Dataclass] result."""
        result = self.rpc.call_method('list_results')

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], MathResult)
        self.assertEqual(result[0].operation, 'add')

    def test_call_method_dict_dataclass_result(self):
        """Test call_method with dict[str, Dataclass] result."""
        result = self.rpc.call_method('dict_results')

        self.assertIsInstance(result, dict)
        self.assertIn('first', result)
        self.assertIsInstance(result['first'], MathResult)
        self.assertEqual(result['first'].result, 10)

    def test_call_method_method_not_found(self):
        """Test call_method raises MethodNotFoundError for non-existent method."""
        with self.assertRaises(MethodNotFoundError) as ctx:
            self.rpc.call_method('nonexistent')
        self.assertIn('not found', str(ctx.exception))

    def test_call_method_invalid_params(self):
        """Test call_method raises InvalidParamsError for wrong params."""
        with self.assertRaises(InvalidParamsError):
            self.rpc.call_method('math.add', [1])  # Missing 'b' param

    def test_call_method_invalid_result_with_validation(self):
        """Test call_method raises InvalidResultError when validation enabled."""
        wrong_group = MethodGroup()
        wrong_group.register('wrong_type', WrongTypeMethod())
        self.rpc.register('test', wrong_group)

        with self.assertRaises(InvalidResultError):
            self.rpc.call_method('test.wrong_type', validate_result=True)

    def test_call_method_async_list_params(self):
        """Test call_method_async with list params."""
        async_group = MethodGroup()
        async_group.register('async_dataclass_add', AsyncDataclassResultMethod())
        self.rpc.register('async', async_group)

        result = asyncio.run(self.rpc.call_method_async('async.async_dataclass_add', [10, 5]))

        self.assertIsInstance(result, MathResult)
        self.assertEqual(result.result, 15)

    def test_call_method_async_dict_params(self):
        """Test call_method_async with dict params."""
        async_group = MethodGroup()
        async_group.register('async_dataclass_add', AsyncDataclassResultMethod())
        self.rpc.register('async', async_group)

        result = asyncio.run(self.rpc.call_method_async('async.async_dataclass_add', {'a': 20, 'b': 30}))

        self.assertIsInstance(result, MathResult)
        self.assertEqual(result.result, 50)

    def test_call_method_async_none_params(self):
        """Test call_method_async with no params."""
        async_group = MethodGroup()
        async_group.register('async_test', AsyncMethod())
        self.rpc.register('async', async_group)

        result = asyncio.run(self.rpc.call_method_async('async.async_test'))
        self.assertEqual(result, 'async_result')

    def test_call_method_nested_call_chain(self):
        """Test method calling another method internally via call_method."""
        # InternalCallMethod calls math.add internally
        internal_group = MethodGroup()
        internal_group.register('double_add', InternalCallMethod())
        self.rpc.register('internal', internal_group)

        result = self.rpc.call_method('internal.double_add', [10, 5])
        self.assertEqual(result, 30)  # (10 + 5) * 2

    def test_call_method_chain_error_propagation(self):
        """Test errors propagate correctly through call chains."""
        internal_group = MethodGroup()
        internal_group.register('double_add', InternalCallMethod())
        self.rpc.register('internal', internal_group)

        # InternalCallMethod calls math.add with invalid params
        with self.assertRaises(InvalidParamsError):
            self.rpc.call_method('internal.double_add', [1])  # Missing param


class TestJSONRPCInitExplicitFlags(unittest.TestCase):
    """Tests for JSONRPC.__init__ explicit flag else-branches (lines 190, 195, 200)."""

    def test_init_explicit_allow_batch_true(self):
        """JSONRPC(allow_batch=True) sets allow_batch directly."""
        rpc = JSONRPC(version='1.0', allow_batch=True)
        self.assertTrue(rpc.allow_batch)

    def test_init_explicit_allow_batch_false(self):
        """JSONRPC(allow_batch=False) sets allow_batch directly."""
        rpc = JSONRPC(version='2.0', allow_batch=False)
        self.assertFalse(rpc.allow_batch)

    def test_init_explicit_allow_dict_params_true(self):
        """JSONRPC(allow_dict_params=True) sets allow_dict_params directly."""
        rpc = JSONRPC(version='1.0', allow_dict_params=True)
        self.assertTrue(rpc.allow_dict_params)

    def test_init_explicit_allow_dict_params_false(self):
        """JSONRPC(allow_dict_params=False) sets allow_dict_params directly."""
        rpc = JSONRPC(version='2.0', allow_dict_params=False)
        self.assertFalse(rpc.allow_dict_params)

    def test_init_explicit_allow_list_params_true(self):
        """JSONRPC(allow_list_params=True) sets allow_list_params directly."""
        rpc = JSONRPC(version='2.0', allow_list_params=True)
        self.assertTrue(rpc.allow_list_params)

    def test_init_explicit_allow_list_params_false(self):
        """JSONRPC(allow_list_params=False) sets allow_list_params directly."""
        rpc = JSONRPC(version='1.0', allow_list_params=False)
        self.assertFalse(rpc.allow_list_params)


class TestJSONRPCRegisterEdgeCases(unittest.TestCase):
    """Tests for JSONRPC.register() error paths (lines 237, 242, 248-265, 269, 286, 300)."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')

    def test_register_empty_name_raises_value_error(self):
        """register('') raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.rpc.register('', AddMethod())
        self.assertEqual(
            str(ctx.exception),
            'Name cannot be empty string. Use None for root group, or non-empty string for named registration.',
        )

    def test_register_class_not_instance_raises_type_error(self):
        """register('foo', MethodClass) raises TypeError."""
        with self.assertRaises(TypeError) as ctx:
            self.rpc.register('add', AddMethod)
        self.assertEqual(
            str(ctx.exception),
            "Cannot register class 'AddMethod'. Must register instance: register('add', AddMethod())",
        )

    def test_register_none_name_with_method_raises_type_error(self):
        """register(None, Method()) raises TypeError — only MethodGroup accepted for None."""
        with self.assertRaises(TypeError) as ctx:
            self.rpc.register(None, AddMethod())
        expected = (
            "Cannot register Method directly with name=None. Use a non-empty name: register('method_name', AddMethod())"
        )
        self.assertEqual(str(ctx.exception), expected)

    def test_register_none_name_after_direct_methods_raises_value_error(self):
        """register(None, group) fails when direct root methods exist."""
        self.rpc.register('ping', NoParamsMethod())
        with self.assertRaises(ValueError) as ctx:
            self.rpc.register(None, MethodGroup())
        self.assertEqual(
            str(ctx.exception),
            'Cannot register explicit root group: root already has directly registered methods. '
            'Clear them first or use named groups.',
        )

    def test_register_none_name_after_root_group_raises_value_error(self):
        """register(None, g2) fails after register(None, g1) already set root group."""
        group1 = MethodGroup()
        group1.register('ping', NoParamsMethod())
        self.rpc.register(None, group1)
        with self.assertRaises(ValueError) as ctx:
            self.rpc.register(None, MethodGroup())
        self.assertEqual(str(ctx.exception), 'Root group already exists with methods/subgroups')

    def test_register_none_name_success(self):
        """register(None, group) successfully replaces root group."""
        root = MethodGroup()
        root.register('ping', NoParamsMethod())
        self.rpc.register(None, root)
        result = self.rpc.call_method('ping')
        self.assertEqual(result, 'pong')

    def test_register_method_name_with_dot_raises_value_error(self):
        """register('math.add', method) raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.rpc.register('math.add', AddMethod())
        self.assertEqual(str(ctx.exception), "Method name cannot contain '.': 'math.add'")

    def test_register_group_name_with_dot_raises_value_error(self):
        """register('math.sub', group) raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.rpc.register('math.sub', MethodGroup())
        self.assertEqual(str(ctx.exception), "Group name cannot contain '.': 'math.sub'")

    def test_register_invalid_type_raises_type_error(self):
        """register('foo', 42) raises TypeError."""
        with self.assertRaises(TypeError) as ctx:
            self.rpc.register('foo', 42)
        self.assertEqual(str(ctx.exception), 'Expected Method or MethodGroup instance, got int')


class TestJSONRPCUnregisterEdgeCases(unittest.TestCase):
    """Tests for JSONRPC.unregister() error paths (lines 318, 330)."""

    def setUp(self):
        self.rpc = JSONRPC(version='2.0')
        math_group = MethodGroup()
        math_group.register('add', AddMethod())
        self.rpc.register('math', math_group)

    def test_unregister_empty_path_raises_value_error(self):
        """unregister('') raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.rpc.unregister('')
        self.assertEqual(str(ctx.exception), 'Path cannot be empty')

    def test_unregister_nonexistent_subgroup_path_raises_key_error(self):
        """unregister('nonexistent.method') raises KeyError when subgroup not found."""
        with self.assertRaises(KeyError):
            self.rpc.unregister('nonexistent.method')


class TestJSONRPCGetMethodNonexistent(unittest.TestCase):
    """Tests for JSONRPC.get_method() returning None (lines 623-624)."""

    def test_get_method_returns_none_for_nonexistent_path(self):
        """get_method('a.b.c') returns None when path not found."""
        rpc = JSONRPC(version='2.0')
        rpc.register('ping', NoParamsMethod())
        result = rpc.get_method('a.b.c.not.found')
        self.assertIsNone(result)

    def test_get_method_returns_method_for_existing(self):
        """get_method('ping') returns the method when found."""
        rpc = JSONRPC(version='2.0')
        rpc.register('ping', NoParamsMethod())
        result = rpc.get_method('ping')
        self.assertIsNotNone(result)


class TestMethodGroupRegisterEdgeCases(unittest.TestCase):
    """Tests for MethodGroup.register() error paths (lines 460, 481, 483, 485, 489, 492)."""

    def test_group_name_property_returns_registered_name(self):
        """MethodGroup.name property returns the name set during registration."""
        rpc = JSONRPC(version='2.0')
        math = MethodGroup()
        math.register('add', AddMethod())
        rpc.register('math', math)
        self.assertEqual(math.name, 'math')

    def test_group_name_property_none_for_unregistered(self):
        """MethodGroup.name is None before registration."""
        group = MethodGroup()
        self.assertIsNone(group.name)

    def test_register_none_name_raises_value_error(self):
        """group.register(None, method) raises ValueError."""
        group = MethodGroup()
        with self.assertRaises(ValueError) as ctx:
            group.register(None, AddMethod())
        self.assertEqual(str(ctx.exception), 'Name cannot be None in MethodGroup.register()')

    def test_register_empty_name_raises_value_error(self):
        """group.register('', method) raises ValueError."""
        group = MethodGroup()
        with self.assertRaises(ValueError) as ctx:
            group.register('', AddMethod())
        self.assertEqual(str(ctx.exception), 'Name cannot be empty string. Use None only in JSONRPC.register()')

    def test_register_dot_in_name_raises_value_error(self):
        """group.register('a.b', method) raises ValueError."""
        group = MethodGroup()
        with self.assertRaises(ValueError) as ctx:
            group.register('a.b', AddMethod())
        self.assertEqual(str(ctx.exception), "Name cannot contain '.': 'a.b'")

    def test_register_duplicate_subgroup_raises_value_error(self):
        """Registering two subgroups with same name raises ValueError."""
        parent = MethodGroup()
        parent.register('sub', MethodGroup())
        with self.assertRaises(ValueError) as ctx:
            parent.register('sub', MethodGroup())
        self.assertEqual(str(ctx.exception), "Subgroup 'sub' already registered")

    def test_register_class_not_instance_raises_type_error(self):
        """group.register('foo', AddMethod) raises TypeError."""
        group = MethodGroup()
        with self.assertRaises(TypeError) as ctx:
            group.register('foo', AddMethod)
        self.assertEqual(
            str(ctx.exception),
            "Cannot register class 'AddMethod'. Must register instance: register('foo', AddMethod())",
        )


class TestMethodGroupSubgroupContextMismatch(unittest.TestCase):
    """Tests for MethodGroup.register() subgroup context type incompatibility (lines 519-520)."""

    def test_register_subgroup_incompatible_context_type_raises_type_error(self):
        """Subgroup with context_type not subclass of parent raises TypeError."""
        from dataclasses import dataclass

        from jsonrpc.method import MethodGroup

        @dataclass
        class BaseCtx:
            request_id: str

        @dataclass
        class AdminCtx(BaseCtx):
            user_id: int

        class ParentGroup(MethodGroup):
            def execute_method(self, method, params, context: AdminCtx):
                return super().execute_method(method, params, context)

        class ChildGroup(MethodGroup):
            def execute_method(self, method, params, context: BaseCtx):
                return super().execute_method(method, params, context)

        parent = ParentGroup()
        child = ChildGroup()
        with self.assertRaises(TypeError) as ctx:
            parent.register('child', child)
        self.assertEqual(
            str(ctx.exception),
            'Cannot register ChildGroup: group context_type BaseCtx must be subclass of parent context_type AdminCtx',
        )


class TestDispatchEdgeCases(unittest.TestCase):
    """Tests for MethodGroup.dispatch() edge cases (lines 630, 674)."""

    def test_dispatch_sync_on_async_method_raises_runtime_error(self):
        """Calling dispatch() synchronously on async method raises RuntimeError."""
        group = MethodGroup()
        group.register('async_ping', AsyncMethod())

        with self.assertRaises(RuntimeError) as ctx:
            group.dispatch('async_ping', None, 1)
        self.assertEqual(str(ctx.exception), "Method 'async_ping' is async, use dispatch_async() instead")

    def test_dispatch_async_with_validate_result(self):
        """dispatch_async() with validate_result=True validates the result."""
        group = MethodGroup()
        group.register('async_ping', AsyncMethod())

        result = asyncio.run(group.dispatch_async('async_ping', None, 1, validate_result=True))
        self.assertEqual(result, 'async_result')


class TestMethodInitSubclassEdgeCases(unittest.TestCase):
    """Tests for Method.__init_subclass__ edge case (line 338)."""

    def test_method_execute_third_param_not_named_context_raises_error(self):
        """execute() with 4 params where index-2 param is not 'context' raises TypeError."""
        from dataclasses import dataclass

        from jsonrpc.method import Method

        @dataclass
        class Params:
            value: int

        @dataclass
        class Ctx:
            user: str

        with self.assertRaises(TypeError) as ctx:

            class BadMethod(Method):
                def execute(self, params: Params, extra: str, context: Ctx) -> int:
                    return params.value

        self.assertEqual(
            str(ctx.exception),
            "BadMethod.execute() third parameter must be 'context', got 'extra'",
        )


class TestRegisterAlreadyInitialized(unittest.TestCase):
    """Tests for fail-fast when a Method instance is registered more than once.

    The guard fires whenever `method.rpc` is already set — which happens as soon
    as a Method is registered into any JSONRPC instance (directly or via an
    already-attached group). Two standalone MethodGroups that haven't been
    attached to a JSONRPC yet don't trigger the check because neither has injected
    `rpc` into their children.
    """

    def test_method_registered_in_two_jsonrpc_raises_value_error(self):
        """Registering same Method instance in two JSONRPC instances raises ValueError."""
        method = AddMethod()
        rpc1 = JSONRPC(version='2.0')
        rpc1.register('add', method)

        rpc2 = JSONRPC(version='2.0')
        with self.assertRaises(ValueError) as ctx:
            rpc2.register('add', method)
        self.assertIn('already registered', str(ctx.exception))
        self.assertIn('AddMethod', str(ctx.exception))

    def test_method_registered_in_jsonrpc_then_group_raises_value_error(self):
        """Registering same Method instance in JSONRPC then a MethodGroup raises ValueError."""
        method = AddMethod()
        rpc = JSONRPC(version='2.0')
        rpc.register('add', method)

        group = MethodGroup()
        with self.assertRaises(ValueError) as ctx:
            group.register('add', method)
        self.assertIn('already registered', str(ctx.exception))

    def test_method_registered_in_jsonrpc_then_same_jsonrpc_raises_value_error(self):
        """Registering same Method instance twice in the same JSONRPC raises ValueError."""
        method = AddMethod()
        rpc = JSONRPC(version='2.0')
        rpc.register('add', method)

        with self.assertRaises(ValueError):
            rpc.register('add2', method)

    def test_fresh_instance_can_be_registered_normally(self):
        """Two separate Method instances of the same class register without error."""
        rpc = JSONRPC(version='2.0')
        rpc.register('add1', AddMethod())
        rpc.register('add2', AddMethod())  # Different instance — must not raise
        self.assertEqual(rpc.call_method('add1', {'a': 1, 'b': 2}), 3)
        self.assertEqual(rpc.call_method('add2', {'a': 10, 'b': 5}), 15)


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


if __name__ == '__main__':
    unittest.main()
