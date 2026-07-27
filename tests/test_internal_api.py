"""Tests for internal JSON-RPC library API.

This module tests the internal library API (non-protocol methods).

For protocol-level tests (handle(), JSON-RPC compliance), see test_jsonrpc_v*.py
"""

import asyncio
import json
import unittest
from dataclasses import dataclass
from typing import Optional

from jsonrpc import JSONRPC, CallInfo, InvalidParamsError, InvalidResultError, MethodNotFoundError
from jsonrpc.method import Method, MethodGroup
from tests.fixtures import (
    AddMethod,
    AddParams,
    AsyncDataclassResultMethod,
    AsyncMethod,
    DataclassResultMethod,
    DictDataclassResultMethod,
    EchoMethod,
    EchoParams,
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


class TestNothingGrowsWithTraffic(unittest.TestCase):
    """A server runs for months. Anything keyed on what a caller sends is a leak.

    The route cache is the one keyed on caller input, so it is the one that has
    to refuse to remember a path that did not resolve.
    """

    def setUp(self):
        self.rpc = JSONRPC()
        group = MethodGroup()
        group.register('echo', EchoMethod())
        self.rpc.register('api', group)
        self.root = self.rpc.get_root_group()
        self.rpc.handle(json.dumps({'jsonrpc': '2.0', 'method': 'api.echo', 'params': {'message': 'x'}, 'id': 1}))

    def test_unknown_method_names_are_not_remembered(self):
        before = len(self.root._route_cache)
        for i in range(2000):
            self.rpc.handle(json.dumps({'jsonrpc': '2.0', 'method': f'nope.{i}', 'id': 1}))
        self.assertEqual(len(self.root._route_cache), before)

    def test_unknown_parameter_names_are_not_remembered(self):
        before = len(self.root._route_cache)
        for i in range(2000):
            body = json.dumps({'jsonrpc': '2.0', 'method': 'api.echo', 'params': {f'bad{i}': 1}, 'id': 1})
            self.rpc.handle(body)
        self.assertEqual(len(self.root._route_cache), before)

    def test_a_runtime_generated_params_type_is_not_pinned(self):
        """@rpc.method builds one per decorated function; the caches key weakly.

        A strong key would keep every such type - and its module, and its
        closure - alive for the life of the process.
        """
        import gc
        import weakref

        def build():
            rpc = JSONRPC(validate_results=True)

            @dataclass
            class Params:
                n: int

            @dataclass
            class Result:
                n: int

            class Temp(Method):
                def execute(self, params: Params) -> Result:
                    return Result(n=params.n)

            rpc.register('t', Temp())
            rpc.handle('{"jsonrpc":"2.0","method":"t","params":{"n":1},"id":1}')
            return weakref.ref(Params), weakref.ref(Result)

        refs = [build() for _ in range(20)]
        gc.collect()
        self.assertTrue(all(p() is None and r() is None for p, r in refs))


class TestAGroupCannotBeItsOwnAncestor(unittest.TestCase):
    """A cycle in the _owner chain hung the process, silently and forever.

    The ownership guard asks "is this group already registered somewhere". The
    outermost group of a tree has no owner, so registering it *into one of its
    own descendants* looked like registering a fresh group and was accepted.
    `_ancestors()` then walked `_owner` with nothing to stop it, and since
    register() is what calls `_ancestors()`, the very next registration anywhere
    in that tree never returned - at import time, with no error and no traceback.
    """

    def test_registering_a_group_into_its_own_child_is_refused(self):
        outer = MethodGroup()
        inner = MethodGroup()
        outer.register('inner', inner)

        with self.assertRaises(ValueError) as ctx:
            inner.register('outer', outer)
        self.assertIn('cannot be its own ancestor', str(ctx.exception))

    def test_registering_a_group_into_itself_is_refused(self):
        group = MethodGroup()
        with self.assertRaises(ValueError):
            group.register('me', group)

    def test_a_grandparent_is_refused_too(self):
        """The check is the whole ancestor chain, not just the immediate parent."""
        top = MethodGroup()
        middle = MethodGroup()
        leaf = MethodGroup()
        top.register('middle', middle)
        middle.register('leaf', leaf)

        with self.assertRaises(ValueError):
            leaf.register('top', top)

    def test_the_refusal_leaves_the_tree_usable(self):
        outer = MethodGroup()
        inner = MethodGroup()
        outer.register('inner', inner)

        with self.assertRaises(ValueError):
            inner.register('outer', outer)

        self.assertIsNone(outer._owner)
        self.assertNotIn('outer', inner.get_all_groups())

        # And registration still works afterwards - the guard runs before any
        # mutation, so nothing is half-done.
        inner.register('echo', EchoMethod())
        rpc = JSONRPC()
        rpc.register('api', outer)
        body = json.dumps({'jsonrpc': '2.0', 'method': 'api.inner.echo', 'params': {'message': 'hi'}, 'id': 1})
        self.assertEqual(json.loads(rpc.handle(body))['result'], 'hi')

    def test_ancestors_terminates_even_if_a_cycle_is_forced(self):
        """The guard in _ancestors() is what keeps the failure an error, not a hang.

        Reached here by writing _owner directly, which register() no longer
        allows - the point is that the walk cannot loop even so.
        """
        a = MethodGroup()
        b = MethodGroup()
        a._owner = b
        b._owner = a

        chain = a._ancestors()
        self.assertEqual(len(chain), 2)
        self.assertIs(chain[-1], a)


class TestRequestSizeIsBounded(unittest.TestCase):
    """max_batch counted requests; nothing bounded how large one could be.

    A single request is not a batch, so no limit applied to it at all: 16.9 MB of
    integers took 6.9 seconds of solid CPU and 90 MB of heap, and under
    handle_async() that is the whole event loop, because nothing on the
    validation path awaits. The amplification is the validator's, not the
    parser's - _coerce builds a second list on top of the one json.loads built.
    """

    def setUp(self):
        self.rpc = JSONRPC(max_request_size=200)
        group = MethodGroup()
        group.register('echo', EchoMethod())
        self.rpc.register('api', group)

    def _body(self, message):
        return json.dumps({'jsonrpc': '2.0', 'method': 'api.echo', 'params': {'message': message}, 'id': 1})

    def test_an_oversized_body_is_refused(self):
        response = json.loads(self.rpc.handle(self._body('x' * 500)))
        self.assertEqual(response['error']['code'], -32600)
        self.assertEqual(response['error']['data']['reason'], 'request_too_large')
        self.assertEqual(response['error']['data']['limit'], 200)
        self.assertIsNone(response['id'])

    def test_a_body_within_the_limit_is_served(self):
        self.assertEqual(json.loads(self.rpc.handle(self._body('hi')))['result'], 'hi')

    def test_it_is_refused_before_anything_parses_it(self):
        """The parse is where the cost starts, so it must not happen."""
        parsed = []

        class Watching(JSONRPC):
            def deserialize(self, data):
                parsed.append(data)
                return super().deserialize(data)

        rpc = Watching(max_request_size=200)
        rpc.handle(self._body('x' * 500))
        self.assertEqual(parsed, [], 'the body reached deserialize() before the limit was applied')

    def test_the_async_entry_point_has_the_same_limit(self):
        response = json.loads(asyncio.run(self.rpc.handle_async(self._body('x' * 500))))
        self.assertEqual(response['error']['data']['reason'], 'request_too_large')

    def test_unlimited_is_available_and_explicit(self):
        rpc = JSONRPC(max_request_size=-1)
        group = MethodGroup()
        group.register('echo', EchoMethod())
        rpc.register('api', group)
        self.assertEqual(json.loads(rpc.handle(self._body('x' * 5000)))['result'], 'x' * 5000)

    def test_a_batch_may_carry_a_tighter_limit_of_its_own(self):
        """For a host that raises the body limit for one large method.

        Without this, that headroom is silently multiplied by max_batch.
        """
        rpc = JSONRPC(max_request_size=100_000, max_batch_size=200)
        group = MethodGroup()
        group.register('echo', EchoMethod())
        rpc.register('api', group)

        entry = {'jsonrpc': '2.0', 'method': 'api.echo', 'params': {'message': 'hi'}}
        batch = json.dumps([dict(entry, id=i) for i in range(20)])

        response = json.loads(rpc.handle(batch))
        self.assertEqual(response['error']['data']['reason'], 'batch_too_large')

        response = json.loads(asyncio.run(rpc.handle_async(batch)))
        self.assertEqual(response['error']['data']['reason'], 'batch_too_large')

        # A single request of the same size is fine - that is the point.
        self.assertEqual(json.loads(rpc.handle(self._body('x' * 500)))['result'], 'x' * 500)

    def test_a_batch_under_both_limits_keeps_its_per_entry_receipts(self):
        """The limit is applied to the whole body, never per entry.

        Applying it per entry would turn an oversized batch into one collapsed
        error, and every sibling would lose the receipt it is owed.
        """
        rpc = JSONRPC(max_request_size=100_000, max_batch_size=100_000)
        group = MethodGroup()
        group.register('echo', EchoMethod())
        rpc.register('api', group)

        entry = {'jsonrpc': '2.0', 'method': 'api.echo', 'params': {'message': 'hi'}}
        responses = json.loads(rpc.handle(json.dumps([dict(entry, id=i) for i in range(5)])))
        self.assertEqual([r['id'] for r in responses], [0, 1, 2, 3, 4])

    def test_the_limits_are_validated_at_construction(self):
        for kwargs in ({'max_request_size': 0}, {'max_request_size': -2}, {'max_batch_size': 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                JSONRPC(**kwargs)

    def test_a_custom_deserialize_may_still_be_handed_something_without_a_length(self):
        """The limit measures str and bytes; anything else is deserialize()'s to describe."""

        class DictRPC(JSONRPC):
            def deserialize(self, data):
                return data

        rpc = DictRPC()
        group = MethodGroup()
        group.register('echo', EchoMethod())
        rpc.register('api', group)

        payload = {'jsonrpc': '2.0', 'method': 'api.echo', 'params': {'message': 'hi'}, 'id': 1}
        self.assertEqual(json.loads(rpc.handle(payload))['result'], 'hi')


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

    def test_clearing_root_methods_lets_a_root_group_be_installed(self):
        """The refusal was remembered rather than rechecked.

        Registering one method at root and then removing it left a flag set, so
        register(None, group) still refused - and told the caller to clear the
        methods they had just cleared, with no way to satisfy it short of a new
        JSONRPC.
        """
        rpc = JSONRPC(version='2.0')  # a root group also displaces subgroups
        rpc.register('ping', NoParamsMethod())
        rpc.unregister('ping')

        root = MethodGroup()
        root.register('ping', NoParamsMethod())
        rpc.register(None, root)

        self.assertEqual(rpc.call_method('ping'), 'pong')

    def test_a_method_still_at_root_keeps_the_refusal(self):
        rpc = JSONRPC(version='2.0')
        rpc.register('ping', NoParamsMethod())

        with self.assertRaises(ValueError) as ctx:
            rpc.register(None, MethodGroup())
        self.assertIn('already has directly registered methods', str(ctx.exception))


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


class TestAnOptionalParamsTypeIsRefused(unittest.TestCase):
    """`params: P | None` was rewritten to `P` and the `| None` was gone.

    params_type became P, so the `if params is None` branch the author wrote
    could never run, and a call carrying no params answered "Missing required
    parameters" against a signature that plainly permits their absence. mypy
    checked the annotation; the library discarded it; nothing said so. For a
    library whose whole argument is fail-fast, that is the worst shape of
    failure available.
    """

    def test_the_pipe_spelling_is_refused(self):
        @dataclass
        class Params:
            a: int

        with self.assertRaises(TypeError) as ctx:

            class M(Method):
                def execute(self, params: Params | None) -> int:
                    return 0 if params is None else params.a

        message = str(ctx.exception)
        self.assertIn('optional params type is not supported', message)
        self.assertIn('Params', message)

    def test_the_typing_spelling_is_refused_the_same_way(self):
        @dataclass
        class Params:
            a: int

        with self.assertRaises(TypeError) as ctx:

            class M(Method):
                def execute(self, params: Optional[Params]) -> int:  # noqa: UP045 - the point of the test
                    return 0 if params is None else params.a

        self.assertIn('optional params type is not supported', str(ctx.exception))

    def test_the_error_names_the_way_out(self):
        """A method callable with no params is a dataclass whose fields default."""

        @dataclass
        class Params:
            a: int = 0

        class M(Method):
            def execute(self, params: Params) -> int:
                return params.a

        rpc = JSONRPC()
        rpc.register('m', M())
        response = json.loads(rpc.handle(json.dumps({'jsonrpc': '2.0', 'method': 'm', 'id': 1})))
        self.assertEqual(response['result'], 0)

    def test_a_plain_dataclass_and_None_are_untouched(self):
        @dataclass
        class Params:
            a: int

        class WithParams(Method):
            def execute(self, params: Params) -> int:
                return params.a

        class WithoutParams(Method):
            def execute(self, params: None) -> int:
                return 7

        self.assertIs(WithParams.params_type, Params)
        self.assertIs(WithoutParams.params_type, type(None))


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


class AsyncEchoMethod(Method):
    """Async twin of EchoMethod, for the sync/async hook rules."""

    async def execute(self, params: EchoParams) -> str:
        return params.message


class TestMethodIntrospection(unittest.TestCase):
    """The small public surface a middleware author may reach for."""

    def test_is_async_reports_the_kind_of_execute(self):
        self.assertFalse(EchoMethod()._is_async())
        self.assertTrue(AsyncEchoMethod()._is_async())


class TestAroundCallChain(unittest.TestCase):
    """around_call() runs for every group on the resolved path, outermost first.

    Its counterpart execute_method() runs on the group that owns the method and
    nowhere else, so a guard mounted above a subgroup used to be inert: the call
    reached the method with no error and no log line.
    """

    def _guarded_tree(self):
        """rpc -> api(guard) -> v1 -> echo / aecho"""

        class RequireAuthGroup(MethodGroup):
            def around_call(self, call, context, call_next):
                if context is None:
                    raise InvalidParamsError('Authentication required')
                return call_next(context)

            async def around_call_async(self, call, context, call_next):
                if context is None:
                    raise InvalidParamsError('Authentication required')
                return await call_next(context)

        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        leaf.register('aecho', AsyncEchoMethod())
        v1 = MethodGroup()
        v1.register('v1', leaf)
        guard = RequireAuthGroup()
        guard.register('sub', v1)
        rpc = JSONRPC()
        rpc.register('api', guard)
        return rpc

    def test_a_nested_method_is_covered_by_the_guard(self):
        rpc = self._guarded_tree()
        with self.assertRaises(InvalidParamsError):
            rpc.call_method('api.sub.v1.echo', {'message': 'hi'})

    def test_an_authorized_call_passes_through(self):
        rpc = self._guarded_tree()
        result = rpc.call_method('api.sub.v1.echo', {'message': 'hi'}, context=object())
        self.assertEqual(result, 'hi')

    def test_a_nested_async_method_is_covered_too(self):
        rpc = self._guarded_tree()
        with self.assertRaises(InvalidParamsError):
            asyncio.run(rpc.call_method_async('api.sub.v1.aecho', {'message': 'hi'}))

    def test_a_sync_method_under_async_dispatch_is_still_guarded(self):
        rpc = self._guarded_tree()
        with self.assertRaises(InvalidParamsError):
            asyncio.run(rpc.call_method_async('api.sub.v1.echo', {'message': 'hi'}))

    def test_handle_reports_the_refusal_as_an_error_response(self):
        rpc = self._guarded_tree()
        response = rpc.handle('{"jsonrpc":"2.0","method":"api.sub.v1.echo","params":{"message":"hi"},"id":1}')
        self.assertIn('Authentication required', response)
        self.assertNotIn('"result"', response)

    def test_order_is_root_to_leaf_and_unwinds_back(self):
        trace = []

        class Recording(MethodGroup):
            label = 'group'

            def around_call(self, call, context, call_next):
                trace.append(f'{self.label}:in')
                result = call_next(context)
                trace.append(f'{self.label}:out')
                return result

        class Outer(Recording):
            label = 'outer'

        class Inner(Recording):
            label = 'inner'

        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        inner = Inner()
        inner.register('sub', leaf)
        outer = Outer()
        outer.register('v1', inner)
        rpc = JSONRPC()
        rpc.register('api', outer)

        result = rpc.call_method('api.v1.sub.echo', {'message': 'hi'})

        self.assertEqual(result, 'hi')
        self.assertEqual(trace, ['outer:in', 'inner:in', 'inner:out', 'outer:out'])

    def test_async_order_is_root_to_leaf_and_unwinds_back(self):
        """The async chain is composed separately and must order identically."""
        trace = []

        class Recording(MethodGroup):
            label = 'group'

            async def around_call_async(self, call, context, call_next):
                trace.append(f'{self.label}:in')
                result = await call_next(context)
                trace.append(f'{self.label}:out')
                return result

        class Outer(Recording):
            label = 'outer'

        class Inner(Recording):
            label = 'inner'

        leaf = MethodGroup()
        leaf.register('aecho', AsyncEchoMethod())
        inner = Inner()
        inner.register('sub', leaf)
        outer = Outer()
        outer.register('v1', inner)
        rpc = JSONRPC()
        rpc.register('api', outer)

        result = asyncio.run(rpc.call_method_async('api.v1.sub.aecho', {'message': 'hi'}))

        self.assertEqual(result, 'hi')
        self.assertEqual(trace, ['outer:in', 'inner:in', 'inner:out', 'outer:out'])

    def test_the_method_runs_exactly_once(self):
        calls = []

        class Counting(Method):
            def execute(self, params: EchoParams) -> str:
                calls.append(1)
                return params.message

        class Wrapper(MethodGroup):
            def around_call(self, call, context, call_next):
                return call_next(context)

        leaf = MethodGroup()
        leaf.register('echo', Counting())
        mid = Wrapper()
        mid.register('sub', leaf)
        outer = Wrapper()
        outer.register('v1', mid)
        rpc = JSONRPC()
        rpc.register('api', outer)

        rpc.call_method('api.v1.sub.echo', {'message': 'hi'})
        self.assertEqual(len(calls), 1)

    def test_a_sync_only_wrapper_runs_under_async_dispatch(self):
        """Async dispatch runs the synchronous chain when nothing on it is async.

        This is what keeps an existing synchronous guard working after a host
        switches to handle_async(): the whole path is sync, so there is nothing
        to await and the wrapper can wrap it directly.
        """
        seen = []

        class SyncOnlyWrapper(MethodGroup):
            def around_call(self, call, context, call_next):
                seen.append(call.path)
                return call_next(context)

        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        wrapper = SyncOnlyWrapper()
        wrapper.register('sub', leaf)
        rpc = JSONRPC()
        rpc.register('api', wrapper)

        result = asyncio.run(rpc.call_method_async('api.sub.echo', {'message': 'hi'}))

        self.assertEqual(result, 'hi')
        self.assertEqual(seen, ['api.sub.echo'])

    def test_an_async_wrapper_around_a_sync_method_reaches_the_method(self):
        """Async chain, synchronous terminal: the result still comes back."""

        class AsyncWrapper(MethodGroup):
            def around_call(self, call, context, call_next):
                return call_next(context)

            async def around_call_async(self, call, context, call_next):
                return await call_next(context)

        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        wrapper = AsyncWrapper()
        wrapper.register('sub', leaf)
        rpc = JSONRPC()
        rpc.register('api', wrapper)

        self.assertEqual(asyncio.run(rpc.call_method_async('api.sub.echo', {'message': 'hi'})), 'hi')

    def test_a_wrapper_may_delegate_to_the_base_hook(self):
        """`super().around_call(...)` is the documented way to pass the call on."""
        seen = []

        class Delegating(MethodGroup):
            def around_call(self, call, context, call_next):
                seen.append('sync')
                return super().around_call(call, context, call_next)

            async def around_call_async(self, call, context, call_next):
                seen.append('async')
                return await super().around_call_async(call, context, call_next)

        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        leaf.register('aecho', AsyncEchoMethod())
        wrapper = Delegating()
        wrapper.register('sub', leaf)
        rpc = JSONRPC()
        rpc.register('api', wrapper)

        self.assertEqual(rpc.call_method('api.sub.echo', {'message': 'hi'}), 'hi')
        self.assertEqual(asyncio.run(rpc.call_method_async('api.sub.aecho', {'message': 'hi'})), 'hi')
        self.assertEqual(seen, ['sync', 'async'])

    def test_a_tree_with_no_wrappers_still_dispatches(self):
        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        mid = MethodGroup()
        mid.register('sub', leaf)
        rpc = JSONRPC()
        rpc.register('api', mid)

        self.assertEqual(rpc.call_method('api.sub.echo', {'message': 'hi'}), 'hi')

    def test_execute_method_on_the_owning_group_still_wraps(self):
        seen = []

        class OwningGroup(MethodGroup):
            def execute_method(self, method, params, context=None):
                seen.append('wrapped')
                return super().execute_method(method, params, context)

        group = OwningGroup()
        group.register('echo', EchoMethod())
        rpc = JSONRPC()
        rpc.register('api', group)

        rpc.call_method('api.echo', {'message': 'hi'})
        self.assertEqual(seen, ['wrapped'])


class TestCallInfo(unittest.TestCase):
    """What a wrapper is told about the call it is wrapping."""

    def test_call_carries_path_method_and_validated_params(self):
        seen = {}

        class Inspecting(MethodGroup):
            def around_call(self, call: CallInfo, context, call_next):
                seen.update(path=call.path, method=call.method, params=call.params)
                return call_next(context)

        method = EchoMethod()
        leaf = MethodGroup()
        leaf.register('echo', method)
        inspecting = Inspecting()
        inspecting.register('sub', leaf)
        rpc = JSONRPC()
        rpc.register('api', inspecting)

        rpc.call_method('api.sub.echo', {'message': 'hello'})

        self.assertEqual(seen['path'], 'api.sub.echo')
        self.assertIs(seen['method'], method)
        self.assertIsInstance(seen['params'], EchoParams)
        self.assertEqual(seen['params'].message, 'hello')

    def test_the_registered_path_distinguishes_two_instances_of_one_class(self):
        """The path is what separates sibling registrations of the same class.

        A cache or limiter keyed on the method's class name gives two
        registrations of one class the same key, so a low-privilege sibling can
        write into a privileged sibling's slot.
        """
        keys = []

        class Keying(MethodGroup):
            def around_call(self, call: CallInfo, context, call_next):
                keys.append(call.path)
                return call_next(context)

        group = Keying()
        group.register('first', EchoMethod())
        group.register('second', EchoMethod())
        rpc = JSONRPC()
        rpc.register('api', group)

        rpc.call_method('api.first', {'message': 'x'})
        rpc.call_method('api.second', {'message': 'x'})

        self.assertEqual(keys, ['api.first', 'api.second'])

    def test_the_request_id_reaches_the_wrapper(self):
        """Without it a middleware cannot tie its log lines to a response."""
        seen = []

        class Correlating(MethodGroup):
            def around_call(self, call: CallInfo, context, call_next):
                seen.append(call.id)
                return call_next(context)

            async def around_call_async(self, call: CallInfo, context, call_next):
                seen.append(call.id)
                return await call_next(context)

        group = Correlating()
        group.register('echo', EchoMethod())
        rpc = JSONRPC()
        rpc.register('api', group)

        rpc.handle(json.dumps({'jsonrpc': '2.0', 'method': 'api.echo', 'params': {'message': 'x'}, 'id': 7}))
        rpc.handle(json.dumps({'jsonrpc': '2.0', 'method': 'api.echo', 'params': {'message': 'x'}, 'id': 'abc'}))
        asyncio.run(
            rpc.handle_async(json.dumps({'jsonrpc': '2.0', 'method': 'api.echo', 'params': {'message': 'x'}, 'id': 9}))
        )

        self.assertEqual(seen, [7, 'abc', 9])

    def test_a_notification_carries_no_id(self):
        """None here means "nobody is waiting for an answer", not "unknown"."""
        seen = []

        class Correlating(MethodGroup):
            def around_call(self, call: CallInfo, context, call_next):
                seen.append(call.id)
                return call_next(context)

        group = Correlating()
        group.register('echo', EchoMethod())
        rpc = JSONRPC()
        rpc.register('api', group)

        rpc.handle(json.dumps({'jsonrpc': '2.0', 'method': 'api.echo', 'params': {'message': 'x'}}))

        self.assertEqual(seen, [None])


class TestHookPairRules(unittest.TestCase):
    """Half-overridden hook pairs are rejected instead of silently failing open.

    Async dispatch used to pick the hook from the method's async-ness rather
    than from what the group overrode, so a group guarding only in the
    synchronous hook let every async method through unguarded.
    """

    def test_sync_only_around_call_rejects_an_async_method_below_it(self):
        class SyncOnlyGuard(MethodGroup):
            def around_call(self, call, context, call_next):
                return call_next(context)

        leaf = MethodGroup()
        leaf.register('aecho', AsyncEchoMethod())

        with self.assertRaises(TypeError) as ctx:
            SyncOnlyGuard().register('sub', leaf)
        self.assertIn('around_call_async', str(ctx.exception))

    def test_sync_only_around_call_is_fine_with_synchronous_methods(self):
        class SyncOnlyGuard(MethodGroup):
            def around_call(self, call, context, call_next):
                raise InvalidParamsError('blocked')

        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        guard = SyncOnlyGuard()
        guard.register('sub', leaf)
        rpc = JSONRPC()
        rpc.register('api', guard)

        with self.assertRaises(InvalidParamsError):
            rpc.call_method('api.sub.echo', {'message': 'hi'})

    def test_async_only_around_call_raises_on_synchronous_dispatch(self):
        class AsyncOnlyGuard(MethodGroup):
            async def around_call_async(self, call, context, call_next):
                return await call_next(context)

        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        guard = AsyncOnlyGuard()
        guard.register('sub', leaf)
        rpc = JSONRPC()
        rpc.register('api', guard)

        with self.assertRaises(RuntimeError) as ctx:
            rpc.call_method('api.sub.echo', {'message': 'hi'})
        self.assertIn('dispatch_async', str(ctx.exception))

    def test_execute_method_only_group_rejects_an_async_method(self):
        class SyncOnlyOwner(MethodGroup):
            def execute_method(self, method, params, context=None):
                raise InvalidParamsError('Authentication required')

        with self.assertRaises(TypeError) as ctx:
            SyncOnlyOwner().register('aecho', AsyncEchoMethod())
        self.assertIn('execute_method_async', str(ctx.exception))

    def test_execute_method_async_only_group_rejects_a_sync_method(self):
        class AsyncOnlyOwner(MethodGroup):
            async def execute_method_async(self, method, params, context=None):
                return await super().execute_method_async(method, params, context)

        with self.assertRaises(TypeError) as ctx:
            AsyncOnlyOwner().register('echo', EchoMethod())
        self.assertIn('execute_method()', str(ctx.exception))

    def test_a_group_overriding_both_hooks_accepts_both_kinds(self):
        class BothOwner(MethodGroup):
            def execute_method(self, method, params, context=None):
                return super().execute_method(method, params, context)

            async def execute_method_async(self, method, params, context=None):
                return await super().execute_method_async(method, params, context)

        group = BothOwner()
        group.register('echo', EchoMethod())
        group.register('aecho', AsyncEchoMethod())
        rpc = JSONRPC()
        rpc.register('api', group)

        self.assertEqual(rpc.call_method('api.echo', {'message': 'hi'}), 'hi')


class TestIrreconcilableWrappers(unittest.TestCase):
    """A sync-only and an async-only wrapper on one path serve nobody.

    Neither entry point can run that chain without skipping one of them, so the
    combination is refused when the tree is mounted. Registration cannot notice
    it earlier: no async *method* is involved, the conflict is between the two
    wrappers.
    """

    def _wrappers(self):
        class SyncOnly(MethodGroup):
            def around_call(self, call, context, call_next):
                return call_next(context)

        class AsyncOnly(MethodGroup):
            async def around_call_async(self, call, context, call_next):
                return await call_next(context)

        return SyncOnly, AsyncOnly

    def _build(self, outer_cls, inner_cls):
        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        inner = inner_cls()
        inner.register('leaf', leaf)
        outer = outer_cls()
        outer.register('inner', inner)
        return outer

    def test_mounting_both_kinds_on_one_path_is_refused(self):
        SyncOnly, AsyncOnly = self._wrappers()
        tree = self._build(AsyncOnly, SyncOnly)

        with self.assertRaises(TypeError) as ctx:
            JSONRPC().register('api', tree)
        self.assertIn('one wraps calls only synchronously', str(ctx.exception))

    def test_the_order_of_the_two_does_not_matter(self):
        SyncOnly, AsyncOnly = self._wrappers()
        tree = self._build(SyncOnly, AsyncOnly)

        with self.assertRaises(TypeError):
            JSONRPC().register('api', tree)

    def test_dispatch_still_refuses_a_detached_tree(self):
        """Groups are dispatchable on their own, below the mount-time check."""
        SyncOnly, AsyncOnly = self._wrappers()
        tree = self._build(AsyncOnly, SyncOnly)

        with self.assertRaises(RuntimeError) as ctx:
            tree.dispatch('inner.leaf.echo', {'message': 'hi'}, None)
        self.assertIn('use dispatch_async()', str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(tree.dispatch_async('inner.leaf.echo', {'message': 'hi'}, None))
        self.assertIn('but not around_call_async()', str(ctx.exception))

    def test_either_kind_alone_is_fine(self):
        SyncOnly, AsyncOnly = self._wrappers()

        for cls in (SyncOnly, AsyncOnly):
            with self.subTest(wrapper=cls.__name__):
                leaf = MethodGroup()
                leaf.register('echo', EchoMethod())
                wrapper = cls()
                wrapper.register('leaf', leaf)
                JSONRPC().register('api', wrapper)  # must not raise


class TestMountingIntoALiveParentSeesTheGroupsAbove(unittest.TestCase):
    """The same tree must be judged the same way however it was assembled.

    A tree built bottom-up and handed to register() is validated from its root,
    so every group meets the ones above it. A subtree registered into a parent
    that is *already* mounted is validated from itself downwards, and used to
    start as though it had no ancestors - so this second, equally ordinary
    assembly order slipped past checks the first one failed, and every call
    below the pair then answered -32603.
    """

    def _wrappers(self):
        class SyncOnly(MethodGroup):
            def around_call(self, call, context, call_next):
                return call_next(context)

        class AsyncOnly(MethodGroup):
            async def around_call_async(self, call, context, call_next):
                return await call_next(context)

        return SyncOnly, AsyncOnly

    def _mounted_parent(self, wrapper_cls):
        rpc = JSONRPC()
        parent = wrapper_cls()
        rpc.register('api', parent)
        return rpc, parent

    def test_an_incompatible_wrapper_added_later_is_refused(self):
        SyncOnly, AsyncOnly = self._wrappers()
        rpc, parent = self._mounted_parent(SyncOnly)

        late = AsyncOnly()
        late.register('echo', EchoMethod())

        with self.assertRaises(TypeError) as ctx:
            parent.register('late', late)
        self.assertIn('one wraps calls only synchronously', str(ctx.exception))

    def test_the_order_of_the_two_still_does_not_matter(self):
        SyncOnly, AsyncOnly = self._wrappers()
        rpc, parent = self._mounted_parent(AsyncOnly)

        late = SyncOnly()
        late.register('echo', EchoMethod())

        with self.assertRaises(TypeError):
            parent.register('late', late)

    def test_the_refusal_leaves_nothing_attached(self):
        SyncOnly, AsyncOnly = self._wrappers()
        rpc, parent = self._mounted_parent(SyncOnly)

        late = AsyncOnly()
        late.register('echo', EchoMethod())

        with self.assertRaises(TypeError):
            parent.register('late', late)

        self.assertNotIn('late', parent.get_all_groups())
        self.assertIsNone(late._owner)
        self.assertFalse(hasattr(late, 'rpc'), 'the rejected subtree kept a reference to the RPC')

    def test_a_wrapper_two_levels_below_a_mounted_one_is_refused(self):
        """The ancestor state has to survive the descent, not just the first step."""
        SyncOnly, AsyncOnly = self._wrappers()
        rpc, parent = self._mounted_parent(SyncOnly)

        deep = AsyncOnly()
        deep.register('echo', EchoMethod())
        middle = MethodGroup()
        middle.register('deep', deep)

        with self.assertRaises(TypeError):
            parent.register('middle', middle)

    def test_an_async_method_added_later_under_a_sync_only_wrapper_is_refused(self):
        SyncOnly, _ = self._wrappers()
        rpc, parent = self._mounted_parent(SyncOnly)

        late = MethodGroup()
        late.register('echo', AsyncEchoMethod())

        with self.assertRaises(TypeError) as ctx:
            parent.register('late', late)
        self.assertIn('around_call()', str(ctx.exception))

    def test_a_compatible_subtree_added_later_still_serves_calls(self):
        SyncOnly, _ = self._wrappers()
        rpc, parent = self._mounted_parent(SyncOnly)

        late = MethodGroup()
        late.register('echo', EchoMethod())
        parent.register('late', late)

        body = json.dumps({'jsonrpc': '2.0', 'method': 'api.late.echo', 'params': {'message': 'hi'}, 'id': 1})
        response = json.loads(rpc.handle(body))
        self.assertEqual(response['result'], 'hi')


class TestDeadHookDetection(unittest.TestCase):
    """execute_method() on a group that owns no methods can never run."""

    def test_mounting_a_wrapper_that_owns_only_subgroups_is_rejected(self):
        class LoggingGroup(MethodGroup):
            def execute_method(self, method, params, context=None):
                return super().execute_method(method, params, context)

        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        logging_group = LoggingGroup()
        logging_group.register('sub', leaf)

        with self.assertRaises(TypeError) as ctx:
            JSONRPC().register('api', logging_group)

        message = str(ctx.exception)
        self.assertIn('owns no methods of its own', message)
        self.assertIn('around_call', message)

    def test_a_wrapper_that_owns_methods_and_subgroups_is_allowed(self):
        class MixedGroup(MethodGroup):
            def execute_method(self, method, params, context=None):
                return super().execute_method(method, params, context)

        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        mixed = MixedGroup()
        mixed.register('own', EchoMethod())
        mixed.register('sub', leaf)

        rpc = JSONRPC()
        rpc.register('api', mixed)
        self.assertEqual(rpc.call_method('api.own', {'message': 'hi'}), 'hi')


class TestRouteCacheInvalidation(unittest.TestCase):
    """Resolution is memoized per path; registry changes must drop it."""

    def setUp(self):
        self.group = MethodGroup()
        self.group.register('first', EchoMethod())
        self.rpc = JSONRPC()
        self.rpc.register('api', self.group)

    def test_a_method_registered_after_the_first_dispatch_is_visible(self):
        self.rpc.call_method('api.first', {'message': 'x'})
        self.group.register('second', EchoMethod())
        self.assertEqual(self.rpc.call_method('api.second', {'message': 'x'}), 'x')

    def test_an_unregistered_method_stops_resolving(self):
        self.rpc.call_method('api.first', {'message': 'x'})
        self.group.unregister('first')
        with self.assertRaises(MethodNotFoundError):
            self.rpc.call_method('api.first', {'message': 'x'})

    def test_a_wrapper_added_after_the_first_dispatch_takes_effect(self):
        self.rpc.call_method('api.first', {'message': 'x'})

        class Blocking(MethodGroup):
            def around_call(self, call, context, call_next):
                raise InvalidParamsError('blocked')

        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        blocking = Blocking()
        blocking.register('sub', leaf)
        self.rpc.register('blocked', blocking)

        with self.assertRaises(InvalidParamsError):
            self.rpc.call_method('blocked.sub.echo', {'message': 'x'})

    def test_only_the_affected_branch_is_dropped(self):
        """A cache holds paths resolved from its own group downwards.

        So a change at one group can only invalidate that group and the ones it
        sits under - a sibling branch has no entry that passes through it. The
        module-level epoch this replaced was correct but indiscriminate: it
        dropped every cache in the process, including trees on unrelated JSONRPC
        instances, and put a global read on the dispatch path to do it.
        """
        sibling = MethodGroup()
        sibling.register('other', EchoMethod())
        self.rpc.register('sibling', sibling)
        root = self.rpc.get_root_group()

        self.rpc.call_method('api.first', {'message': 'x'})
        self.rpc.call_method('sibling.other', {'message': 'x'})
        sibling.dispatch('other', {'message': 'x'}, None)
        self.assertEqual(len(sibling._route_cache), 1)

        self.group.register('second', EchoMethod())

        self.assertEqual(len(self.group._route_cache), 0, 'the mutated group kept a stale cache')
        self.assertEqual(len(root._route_cache), 0, 'an ancestor kept a route through the mutated group')
        self.assertEqual(len(sibling._route_cache), 1, 'an untouched sibling was invalidated for nothing')

    def test_another_rpc_instance_is_left_alone(self):
        other = JSONRPC()
        other_group = MethodGroup()
        other_group.register('ping', EchoMethod())
        other.register('api', other_group)
        other.call_method('api.ping', {'message': 'x'})
        self.assertEqual(len(other.get_root_group()._route_cache), 1)

        self.group.register('second', EchoMethod())

        self.assertEqual(len(other.get_root_group()._route_cache), 1)

    def test_a_detached_group_invalidates_without_any_rpc(self):
        """This is why the counter cannot live on JSONRPC: there is not one here."""
        group = MethodGroup()
        group.register('first', EchoMethod())
        group.dispatch('first', {'message': 'x'}, None)
        self.assertEqual(len(group._route_cache), 1)

        group.register('second', EchoMethod())
        self.assertEqual(len(group._route_cache), 0)
        self.assertEqual(group.dispatch('second', {'message': 'ok'}, None), 'ok')

    def test_unknown_methods_do_not_accumulate_in_the_cache(self):
        """Only successful resolutions are memoized.

        Caching misses would let an unknown-method flood grow the cache without
        bound, keyed by attacker-chosen strings.
        """
        root = self.rpc.get_root_group()
        for i in range(50):
            self.rpc.handle(f'{{"jsonrpc":"2.0","method":"nope{i}","id":1}}')
        self.assertEqual(len(root._route_cache), 0)


class TestSingleOwnership(unittest.TestCase):
    """An instance belongs to exactly one place in exactly one tree."""

    def test_a_method_cannot_be_registered_into_two_detached_groups(self):
        """Ownership is recorded at registration, not at RPC injection.

        The old guard tested `hasattr(target, 'rpc')`, which is only set once a
        group is attached to a JSONRPC - so building a tree bottom-up, the
        pattern the MethodGroup docstring teaches, left no sentinel at all.
        """
        method = EchoMethod()
        first = MethodGroup()
        second = MethodGroup()

        first.register('echo', method)
        with self.assertRaises(ValueError) as ctx:
            second.register('echo', method)
        self.assertIn('already registered', str(ctx.exception))

    def test_a_method_cannot_be_registered_twice_in_the_same_group(self):
        method = EchoMethod()
        group = MethodGroup()
        group.register('echo', method)
        with self.assertRaises(ValueError):
            group.register('echo_again', method)

    def test_a_group_cannot_be_registered_into_two_parents(self):
        """MethodGroup targets had no ownership check at all."""
        child = MethodGroup()
        child.register('echo', EchoMethod())

        first = MethodGroup()
        second = MethodGroup()
        first.register('child', child)

        with self.assertRaises(ValueError) as ctx:
            second.register('child', child)
        self.assertIn('already registered', str(ctx.exception))

    def test_a_method_mounted_on_one_rpc_cannot_be_mounted_on_another(self):
        method = EchoMethod()
        first = JSONRPC()
        second = JSONRPC()
        first.register('echo', method)
        with self.assertRaises(ValueError):
            second.register('echo', method)

    def test_unregister_releases_ownership(self):
        method = EchoMethod()
        first = MethodGroup()
        second = MethodGroup()

        first.register('echo', method)
        first.unregister('echo')
        second.register('echo', method)  # must not raise

        rpc = JSONRPC()
        rpc.register('api', second)
        self.assertEqual(rpc.call_method('api.echo', {'message': 'hi'}), 'hi')

    def test_unregister_releases_ownership_of_a_subgroup(self):
        child = MethodGroup()
        child.register('echo', EchoMethod())
        first = MethodGroup()
        first.register('child', child)
        first.unregister('child')

        MethodGroup().register('child', child)  # must not raise

    def test_unregistering_a_subtree_detaches_it_all_the_way_down(self):
        """Every method below the removed subgroup loses its rpc back-reference."""
        deep_method = EchoMethod()
        deep = MethodGroup()
        deep.register('echo', deep_method)
        middle = MethodGroup()
        middle.register('deep', deep)
        top = MethodGroup()
        top.register('middle', middle)

        rpc = JSONRPC()
        rpc.register('api', top)
        self.assertIs(deep_method.rpc, rpc)

        top.unregister('middle')

        self.assertFalse(hasattr(deep_method, 'rpc'))
        self.assertFalse(hasattr(deep, 'rpc'))
        self.assertEqual(rpc.list_methods(), [])

    def test_the_rpc_back_reference_points_at_the_owning_instance(self):
        """With one instance on two mounts, unregistering one broke the other."""
        method = EchoMethod()
        group = MethodGroup()
        group.register('echo', method)
        rpc = JSONRPC()
        rpc.register('api', group)

        self.assertIs(method.rpc, rpc)

        second_group = MethodGroup()
        with self.assertRaises(ValueError):
            second_group.register('echo', method)
        self.assertIs(method.rpc, rpc)


class TestFailedRegistrationLeavesNothingBehind(unittest.TestCase):
    """A refused subtree must stay reusable.

    Mounting used to attach the rpc reference on the way down and validate as it
    went, so a subtree rejected halfway kept `.rpc` on the groups the walk had
    already passed. The ownership guard then saw that attribute and refused to
    register the subtree anywhere else - the objects were unusable for the rest
    of the process, and the only way out was rebuilding the whole tree.
    """

    def setUp(self):
        from dataclasses import dataclass

        @dataclass
        class Ctx:
            user_id: int

        @dataclass
        class Unrelated:
            other: str

        class NeedsUnrelated(Method):
            def execute(self, params: EchoParams, context: Unrelated) -> str:
                return params.message

        self.Ctx = Ctx
        self.method = NeedsUnrelated()
        self.inner = MethodGroup()
        self.inner.register('echo', self.method)
        self.outer = MethodGroup()
        self.outer.register('inner', self.inner)

    def test_the_registration_is_refused(self):
        rpc = JSONRPC(context_type=self.Ctx)
        with self.assertRaises(TypeError):
            rpc.register('api', self.outer)
        self.assertEqual(rpc.list_methods(), [])

    def test_no_object_in_the_subtree_kept_a_reference(self):
        rpc = JSONRPC(context_type=self.Ctx)
        with self.assertRaises(TypeError):
            rpc.register('api', self.outer)

        for obj in (self.outer, self.inner, self.method):
            self.assertFalse(hasattr(obj, 'rpc'), f'{type(obj).__name__} kept its rpc reference')
        self.assertIsNone(self.outer._owner)

    def test_the_subtree_can_still_be_registered_elsewhere(self):
        rpc = JSONRPC(context_type=self.Ctx)
        with self.assertRaises(TypeError):
            rpc.register('api', self.outer)

        compatible = JSONRPC()
        compatible.register('api', self.outer)
        self.assertEqual(compatible.list_methods(), ['api.inner.echo'])

    def test_the_same_holds_for_a_group_level_registration(self):
        parent = MethodGroup()
        rpc = JSONRPC(context_type=self.Ctx)
        rpc.register('api', parent)

        with self.assertRaises(TypeError):
            parent.register('outer', self.outer)

        self.assertFalse(hasattr(self.outer, 'rpc'))
        self.assertEqual(rpc.list_methods(), [])
        JSONRPC().register('api', self.outer)  # must not raise


class TestRootGroupOwnership(unittest.TestCase):
    """register(None, group) checked the receiver but never the target."""

    def test_one_group_cannot_be_the_root_of_two_instances(self):
        shared = MethodGroup()
        shared.register('ping', NoParamsMethod())

        public = JSONRPC()
        public.register(None, shared)

        with self.assertRaises(ValueError) as ctx:
            JSONRPC().register(None, shared)
        self.assertIn('already registered', str(ctx.exception))

    def test_the_method_tables_of_two_instances_stay_separate(self):
        """The consequence the check prevents.

        With one group as the root of both, a later registration on either
        instance shows up on the other, so an admin subtree mounted on an
        internal endpoint is published on the public one.
        """
        shared = MethodGroup()
        shared.register('ping', NoParamsMethod())

        public = JSONRPC()
        internal = JSONRPC()
        public.register(None, shared)
        with self.assertRaises(ValueError):
            internal.register(None, shared)

        admin = MethodGroup()
        admin.register('dump_secrets', NoParamsMethod())
        internal.register('admin', admin)

        self.assertNotIn('admin.dump_secrets', public.list_methods())
        with self.assertRaises(MethodNotFoundError):
            public.call_method('admin.dump_secrets')

    def test_an_unmounted_group_can_still_become_a_root(self):
        group = MethodGroup()
        group.register('ping', NoParamsMethod())
        rpc = JSONRPC()
        rpc.register(None, group)
        self.assertEqual(rpc.list_methods(), ['ping'])


class TestInheritanceAwareExtraction(unittest.TestCase):
    """Type extraction runs for classes that define execute(), and only those."""

    def test_an_intermediate_base_without_execute_can_be_declared(self):
        """A per-namespace base carrying shared domain logic used to be impossible.

        __init_subclass__ ran the extraction on every subclass, so a base that
        inherited Method.execute(self, params: Any) died at class-definition
        time with a message about params not being a dataclass.
        """

        class DomainBase(Method):
            def require_session(self, context):
                return context

        class Concrete(DomainBase):
            def execute(self, params: EchoParams) -> str:
                return params.message

        self.assertIs(Concrete.params_type, EchoParams)
        self.assertIs(Concrete.result_type, str)

    def test_two_level_base_chains_work(self):
        class Level1(Method):
            pass

        class Level2(Level1):
            pass

        class Concrete(Level2):
            def execute(self, params: EchoParams) -> str:
                return params.message

        self.assertIs(Concrete.params_type, EchoParams)

    def test_template_method_inherits_the_extracted_contract(self):
        """A leaf that overrides only a hook shares the base's contract."""

        class TemplateBase(Method):
            def execute(self, params: EchoParams) -> str:
                return self.transform(params.message)

            def transform(self, text: str) -> str:
                return text

        class Shouting(TemplateBase):
            def transform(self, text: str) -> str:
                return text.upper()

        self.assertIs(Shouting.params_type, EchoParams)
        self.assertIs(Shouting.result_type, str)

        rpc = JSONRPC()
        rpc.register('shout', Shouting())
        self.assertEqual(rpc.call_method('shout', {'message': 'hi'}), 'HI')

    def test_registering_an_abstract_method_is_refused(self):
        """The old import-time crash was accidentally the thing that stopped an
        abstract Method from being mounted. That protection moves to register()."""

        class DomainBase(Method):
            def helper(self):
                return None

        with self.assertRaises(TypeError) as ctx:
            MethodGroup().register('base', DomainBase())
        self.assertIn('does not implement execute()', str(ctx.exception))

    def test_registering_an_abstract_method_on_the_rpc_is_refused(self):
        class DomainBase(Method):
            pass

        with self.assertRaises(TypeError):
            JSONRPC().register('base', DomainBase())

    def test_a_group_base_without_execute_method_inherits_context_type(self):
        class TypedGroup(MethodGroup):
            def execute_method(self, method, params, context: MathResult = None):
                return super().execute_method(method, params, context)

        class DerivedGroup(TypedGroup):
            pass

        self.assertIs(DerivedGroup.context_type, MathResult)

    def test_a_subclass_without_its_own_execute_keeps_the_inherited_types(self):
        class Base(Method):
            def execute(self, params: AddParams) -> int:
                return params.a + params.b

        class Narrowed(Base):
            pass

        self.assertIs(Narrowed.params_type, AddParams)
        self.assertIs(Narrowed.result_type, int)


class TestMethodNotFoundHidesTheTreeShape(unittest.TestCase):
    """-32601 named internal groups, including the root as the string 'None'.

    0.4.0 already removed the caller's own payload and repr addresses from error
    messages; group names are the same category - the server's internal shape,
    of no use to the caller and of some use to someone mapping the surface.
    """

    def setUp(self):
        leaf = MethodGroup()
        leaf.register('echo', EchoMethod())
        outer = MethodGroup()
        outer.register('sub', leaf)
        self.rpc = JSONRPC()
        self.rpc.register('outer', outer)

    def _message(self, path):
        response = self.rpc.handle(f'{{"jsonrpc":"2.0","method":"{path}","id":1}}')
        return json.loads(response)['error']['message']

    def test_an_unknown_root_method_names_only_the_path(self):
        self.assertEqual(self._message('nope'), "Method 'nope' not found")

    def test_a_missing_intermediate_group_names_only_the_path(self):
        message = self._message('missing.sub.echo')
        self.assertEqual(message, "Method 'missing.sub.echo' not found")
        self.assertNotIn('subgroup', message)

    def test_an_unknown_leaf_names_only_the_path(self):
        message = self._message('outer.sub.nope')
        self.assertEqual(message, "Method 'outer.sub.nope' not found")

    def test_no_message_mentions_the_root_group(self):
        for path in ('nope', 'missing.sub.echo', 'outer.sub.nope'):
            self.assertNotIn('None', self._message(path))

    def test_the_detail_goes_to_the_log(self):
        with self.assertLogs('jsonrpc-lib', level='DEBUG') as captured:
            self._message('outer.sub.nope')
        self.assertTrue(any('outer.sub.nope' in record for record in captured.output))


if __name__ == '__main__':
    unittest.main()
