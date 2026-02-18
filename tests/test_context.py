"""Tests for context support."""

import unittest
from dataclasses import dataclass

from jsonrpc import JSONRPC, Method, MethodGroup


@dataclass
class BaseContext:
    """Base context."""

    request_id: str


@dataclass
class AdminContext(BaseContext):
    """Admin context (inherits BaseContext)."""

    user_id: int
    role: str


@dataclass
class SuperAdminContext(AdminContext):
    """SuperAdmin context (inherits AdminContext)."""

    can_delete: bool


class TestContextInspection(unittest.TestCase):
    """Test signature inspection for context detection."""

    def test_method_with_context_detected(self):
        """Method with context parameter sets accepts_context=True."""

        @dataclass
        class Params:
            value: int

        class CtxMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> int:
                return params.value + context.user_id

        self.assertTrue(CtxMethod.accepts_context)
        self.assertEqual(CtxMethod.context_type, AdminContext)

    def test_method_without_context_detected(self):
        """Method without context parameter sets accepts_context=False."""

        @dataclass
        class Params:
            value: int

        class NoCtxMethod(Method):
            def execute(self, params: Params) -> int:
                return params.value

        self.assertFalse(NoCtxMethod.accepts_context)
        self.assertIsNone(NoCtxMethod.context_type)

    def test_methodgroup_with_context_detected(self):
        """MethodGroup with context in execute_method() sets context_type."""

        class AdminGroup(MethodGroup):
            def execute_method(self, method: Method, params, context: AdminContext):
                return super().execute_method(method, params, context)

        self.assertEqual(AdminGroup.context_type, AdminContext)

    def test_methodgroup_without_context(self):
        """MethodGroup without context has context_type=None."""

        class RegularGroup(MethodGroup):
            pass

        self.assertIsNone(RegularGroup.context_type)

    def test_method_context_missing_type_hint_raises_error(self):
        """Method with context parameter but no type hint raises TypeError."""

        @dataclass
        class Params:
            value: int

        with self.assertRaises(TypeError) as cm:

            class BadMethod(Method):
                def execute(self, params: Params, context) -> int:  # No type hint!
                    return 42

        self.assertIn("has 'context' parameter but no type hint", str(cm.exception))

    def test_methodgroup_context_without_type_hint_allowed(self):
        """MethodGroup with context parameter but no type hint has context_type=None.

        This allows groups to pass context through without validation.
        """

        class PassThroughGroup(MethodGroup):
            def execute_method(self, method, params, context=None):  # No type hint = no validation
                return super().execute_method(method, params, context)

        # Should not raise error, and context_type should be None
        self.assertIsNone(PassThroughGroup.context_type)


class TestContextRegistrationValidation(unittest.TestCase):
    """Test context type validation during registration."""

    def test_register_compatible_method_in_group(self):
        """Can register method with compatible context_type."""

        @dataclass
        class Params:
            value: int

        class AdminGroup(MethodGroup):
            def execute_method(self, method: Method, params, context: AdminContext):
                return super().execute_method(method, params, context)

        class SuperAdminMethod(Method):
            def execute(self, params: Params, context: SuperAdminContext) -> int:
                return params.value if context.can_delete else 0

        group = AdminGroup()
        # Should work: SuperAdminContext(AdminContext)
        group.register('delete', SuperAdminMethod())

    def test_register_incompatible_method_raises_error(self):
        """Cannot register method with incompatible context_type."""

        @dataclass
        class Params:
            value: int

        @dataclass
        class OtherContext:
            other_field: str

        class AdminGroup(MethodGroup):
            def execute_method(self, method: Method, params, context: AdminContext):
                return super().execute_method(method, params, context)

        class OtherMethod(Method):
            def execute(self, params: Params, context: OtherContext) -> int:
                return 42

        group = AdminGroup()

        with self.assertRaises(TypeError) as cm:
            group.register('other', OtherMethod())

        self.assertIn('must be subclass', str(cm.exception))
        self.assertIn('OtherContext', str(cm.exception))

    def test_register_compatible_group_in_rpc(self):
        """Can register group with compatible context_type in RPC."""

        class AdminGroup(MethodGroup):
            def execute_method(self, method: Method, params, context: AdminContext):
                return super().execute_method(method, params, context)

        rpc = JSONRPC(context_type=BaseContext)

        # Should work: AdminContext(BaseContext)
        rpc.register('admin', AdminGroup())

    def test_register_incompatible_group_in_rpc_raises_error(self):
        """Cannot register group with incompatible context_type in RPC."""

        @dataclass
        class OtherContext:
            other_field: str

        class OtherGroup(MethodGroup):
            def execute_method(self, method: Method, params, context: OtherContext):
                return super().execute_method(method, params, context)

        rpc = JSONRPC(context_type=BaseContext)

        with self.assertRaises(TypeError) as cm:
            rpc.register('other', OtherGroup())

        self.assertIn('must be subclass', str(cm.exception))

    def test_register_no_context_method_always_works(self):
        """Methods without context are compatible with any group/RPC."""

        @dataclass
        class Params:
            value: int

        class NoCtxMethod(Method):
            def execute(self, params: Params) -> int:
                return params.value

        class AdminGroup(MethodGroup):
            def execute_method(self, method: Method, params, context: AdminContext):
                return super().execute_method(method, params, context)

        group = AdminGroup()
        group.register('ping', NoCtxMethod())  # Should work

    def test_register_context_method_in_group_without_context_type(self):
        """Can register method with context in group that has no context_type.

        This tests that groups without context_type (regular MethodGroup)
        can still contain methods that accept context. The group simply
        passes context through without validation.
        """

        @dataclass
        class Params:
            value: int

        class CtxMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> dict:
                return {'value': params.value, 'user': context.user_id}

        # Regular MethodGroup (no context_type)
        group = MethodGroup()
        # Should work - group without context_type accepts any method
        group.register('test', CtxMethod())

        # Verify it was registered
        self.assertIn('test', group._methods)

    def test_group_without_context_passes_context_to_method(self):
        """Group without context_type still passes context to methods."""

        @dataclass
        class Params:
            value: int

        class CtxMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> dict:
                return {'value': params.value, 'user_id': context.user_id}

        rpc = JSONRPC()  # No context_type
        group = MethodGroup()  # No context_type
        group.register('test', CtxMethod())
        rpc.register('group', group)

        ctx = AdminContext(request_id='123', user_id=99, role='admin')
        result = rpc.call_method('group.test', {'value': 42}, context=ctx)

        self.assertEqual(result['value'], 42)
        self.assertEqual(result['user_id'], 99)

    def test_register_compatible_method_in_rpc(self):
        """Can register method with compatible context_type directly in RPC."""

        @dataclass
        class Params:
            value: int

        class AdminMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> int:
                return params.value + context.user_id

        rpc = JSONRPC(context_type=BaseContext)

        # Should work: AdminContext(BaseContext)
        rpc.register('admin_op', AdminMethod())

    def test_register_incompatible_method_in_rpc_raises_error(self):
        """Cannot register method with incompatible context_type in RPC."""

        @dataclass
        class Params:
            value: int

        @dataclass
        class OtherContext:
            other_field: str

        class OtherMethod(Method):
            def execute(self, params: Params, context: OtherContext) -> int:
                return 42

        rpc = JSONRPC(context_type=BaseContext)

        with self.assertRaises(TypeError) as cm:
            rpc.register('other', OtherMethod())

        self.assertIn('must be subclass', str(cm.exception))


class TestContextPassing(unittest.TestCase):
    """Test context passing through call chain."""

    def test_context_passed_to_method(self):
        """Context is passed to methods that accept it."""

        @dataclass
        class Params:
            value: int

        class CtxMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> dict:
                return {
                    'result': params.value,
                    'user_id': context.user_id,
                    'request_id': context.request_id,
                }

        rpc = JSONRPC()
        rpc.register('test', CtxMethod())
        ctx = AdminContext(request_id='123', user_id=42, role='admin')

        result = rpc.call_method('test', {'value': 10}, context=ctx)

        self.assertEqual(result['result'], 10)
        self.assertEqual(result['user_id'], 42)
        self.assertEqual(result['request_id'], '123')

    def test_context_not_passed_to_non_context_method(self):
        """Context is NOT passed to methods that don't accept it."""

        @dataclass
        class Params:
            value: int

        class NoCtxMethod(Method):
            def execute(self, params: Params) -> int:
                return params.value * 2

        rpc = JSONRPC()
        rpc.register('test', NoCtxMethod())
        ctx = AdminContext(request_id='123', user_id=42, role='admin')

        # Should work - context silently ignored
        result = rpc.call_method('test', {'value': 5}, context=ctx)
        self.assertEqual(result, 10)

    def test_context_passed_through_handle(self):
        """Context passed via handle() reaches the method."""

        @dataclass
        class Params:
            value: int

        class CtxMethod(Method):
            def execute(self, params: Params, context: BaseContext) -> str:
                return f'request_{context.request_id}'

        rpc = JSONRPC(version='2.0')
        rpc.register('test', CtxMethod())
        ctx = BaseContext(request_id='xyz-789')

        response = rpc.handle('{"jsonrpc":"2.0","method":"test","params":{"value":5},"id":1}', context=ctx)

        self.assertIn('request_xyz-789', response)
        self.assertIn('"result"', response)

    def test_context_passed_in_method_group(self):
        """Context passed through MethodGroup dispatch."""

        @dataclass
        class Params:
            value: int

        class CtxMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> dict:
                return {'value': params.value, 'role': context.role}

        group = MethodGroup()
        group.register('op', CtxMethod())

        rpc = JSONRPC()
        rpc.register('admin', group)

        ctx = AdminContext(request_id='abc', user_id=1, role='superadmin')
        result = rpc.call_method('admin.op', {'value': 100}, context=ctx)

        self.assertEqual(result['value'], 100)
        self.assertEqual(result['role'], 'superadmin')


class TestContextRuntimeValidation(unittest.TestCase):
    """Test runtime context type validation."""

    def test_correct_context_type_accepted(self):
        """Correct context type passes runtime validation."""

        @dataclass
        class Params:
            pass

        class CtxMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> str:
                return f'user_{context.user_id}'

        rpc = JSONRPC()
        rpc.register('test', CtxMethod())
        ctx = AdminContext(request_id='123', user_id=99, role='admin')

        result = rpc.call_method('test', {}, context=ctx)
        self.assertEqual(result, 'user_99')

    def test_subclass_context_type_accepted(self):
        """Subclass of expected context type passes runtime validation."""

        @dataclass
        class Params:
            pass

        class CtxMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> str:
                return f'user_{context.user_id}'

        rpc = JSONRPC()
        rpc.register('test', CtxMethod())
        # SuperAdminContext is subclass of AdminContext
        ctx = SuperAdminContext(request_id='123', user_id=99, role='admin', can_delete=True)

        result = rpc.call_method('test', {}, context=ctx)
        self.assertEqual(result, 'user_99')

    def test_wrong_context_type_raises_error(self):
        """Wrong context type raises TypeError at runtime."""

        @dataclass
        class Params:
            pass

        @dataclass
        class WrongContext:
            value: str

        class CtxMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> str:
                return 'ok'

        rpc = JSONRPC()
        rpc.register('test', CtxMethod())
        wrong_ctx = WrongContext(value='wrong')

        with self.assertRaises(TypeError) as cm:
            rpc.call_method('test', {}, context=wrong_ctx)

        self.assertIn('Expected context of type AdminContext', str(cm.exception))

    def test_none_context_allowed_for_optional(self):
        """None context is allowed (no runtime error)."""

        @dataclass
        class Params:
            value: int

        class CtxMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> int:
                # This won't be called with None context in practice
                # but runtime validation only checks if context is not None
                return params.value

        rpc = JSONRPC()
        rpc.register('test', CtxMethod())

        # Passing None context should not raise runtime error
        # (execute() will fail if it tries to use context, but that's different)
        result = rpc.call_method('test', {'value': 42}, context=None)
        self.assertEqual(result, 42)


class TestContextAsync(unittest.IsolatedAsyncioTestCase):
    """Test async context support."""

    async def test_async_context_passing(self):
        """Context passed to async methods."""

        @dataclass
        class Params:
            value: int

        class AsyncCtxMethod(Method):
            async def execute(self, params: Params, context: AdminContext) -> dict:
                return {
                    'result': params.value * 2,
                    'user': context.user_id,
                }

        rpc = JSONRPC()
        rpc.register('test', AsyncCtxMethod())
        ctx = AdminContext(request_id='123', user_id=77, role='admin')

        result = await rpc.call_method_async('test', {'value': 5}, context=ctx)

        self.assertEqual(result['result'], 10)
        self.assertEqual(result['user'], 77)

    async def test_async_handle_with_context(self):
        """Context passed via handle_async() reaches the method."""

        @dataclass
        class Params:
            pass

        class AsyncCtxMethod(Method):
            async def execute(self, params: Params, context: BaseContext) -> str:
                return f'req_{context.request_id}'

        rpc = JSONRPC(version='2.0')
        rpc.register('test', AsyncCtxMethod())
        ctx = BaseContext(request_id='async-999')

        response = await rpc.handle_async('{"jsonrpc":"2.0","method":"test","params":{},"id":1}', context=ctx)

        self.assertIn('req_async-999', response)
        self.assertIn('"result"', response)

    async def test_async_method_runtime_validation(self):
        """Async methods get runtime context type validation."""

        @dataclass
        class Params:
            pass

        @dataclass
        class WrongContext:
            value: str

        class AsyncCtxMethod(Method):
            async def execute(self, params: Params, context: AdminContext) -> str:
                return 'ok'

        rpc = JSONRPC()
        rpc.register('test', AsyncCtxMethod())
        wrong_ctx = WrongContext(value='wrong')

        with self.assertRaises(TypeError) as cm:
            await rpc.call_method_async('test', {}, context=wrong_ctx)

        self.assertIn('Expected context of type AdminContext', str(cm.exception))


class TestContextHierarchy(unittest.TestCase):
    """Test hierarchical context types (RPC → MethodGroup → Method)."""

    def test_three_level_hierarchy(self):
        """Test RPC → MethodGroup → Method hierarchy."""

        @dataclass
        class Params:
            value: int

        # Level 1: RPC with BaseContext
        class AdminGroup(MethodGroup):
            def execute_method(self, method: Method, params, context: AdminContext):
                # Middleware: check role
                if context.role != 'admin':
                    raise PermissionError('Admin role required')
                return super().execute_method(method, params, context)

        # Level 2: Method with SuperAdminContext
        class DeleteMethod(Method):
            def execute(self, params: Params, context: SuperAdminContext) -> dict:
                if not context.can_delete:
                    raise PermissionError('Cannot delete')
                return {'deleted': params.value, 'by': context.user_id}

        rpc = JSONRPC(context_type=BaseContext)
        admin_group = AdminGroup()
        admin_group.register('delete', DeleteMethod())
        rpc.register('admin', admin_group)

        # Test with SuperAdminContext (satisfies all levels)
        ctx = SuperAdminContext(request_id='req-1', user_id=1, role='admin', can_delete=True)
        result = rpc.call_method('admin.delete', {'value': 99}, context=ctx)

        self.assertEqual(result['deleted'], 99)
        self.assertEqual(result['by'], 1)

    def test_middleware_can_access_context(self):
        """MethodGroup middleware can access context."""

        @dataclass
        class Params:
            value: int

        executed_with_role = []

        class AdminGroup(MethodGroup):
            def execute_method(self, method: Method, params, context: AdminContext):
                executed_with_role.append(context.role)
                return super().execute_method(method, params, context)

        class TestMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> int:
                return params.value

        rpc = JSONRPC()
        admin_group = AdminGroup()
        admin_group.register('test', TestMethod())
        rpc.register('admin', admin_group)

        ctx = AdminContext(request_id='req-1', user_id=1, role='superadmin')
        rpc.call_method('admin.test', {'value': 10}, context=ctx)

        self.assertEqual(executed_with_role, ['superadmin'])


class TestContextEdgeCases(unittest.TestCase):
    """Test edge cases with context handling."""

    def test_group_without_context_param_in_execute_method(self):
        """Group that overrides execute_method WITHOUT context parameter.

        This tests that we catch the error at registration time, not runtime.
        A group that doesn't accept context parameter cannot register methods
        that require context.
        """

        @dataclass
        class Params:
            value: int

        # Group with execute_method that DOESN'T accept context
        class LoggingGroup(MethodGroup):
            def execute_method(self, method: Method, params):  # No context!
                # This group cannot pass context to methods
                return super().execute_method(method, params)

        class CtxMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> dict:
                return {'value': params.value, 'user_id': context.user_id}

        group = LoggingGroup()

        # Should raise TypeError at registration time (not runtime!)
        with self.assertRaises(TypeError) as cm:
            group.register('test', CtxMethod())

        # The error should explain that group doesn't accept context parameter
        self.assertIn('method requires context', str(cm.exception))
        self.assertIn('execute_method() does not accept context parameter', str(cm.exception))

    def test_group_with_context_param_but_not_using_it(self):
        """Correct way to override execute_method without using context.

        Even if the group doesn't use context, the signature MUST include
        'context=None' to allow context to be passed through.
        Don't add type hint if you don't care about context type.
        """

        @dataclass
        class Params:
            value: int

        # CORRECT: Include context parameter without type hint
        # This makes context_type = None (no validation)
        class LoggingGroup(MethodGroup):
            def __init__(self):
                super().__init__()
                self.log = []

            def execute_method(self, method: Method, params, context=None):
                self.log.append(f'Executing {method.__class__.__name__}')
                # Pass context to parent even if we don't use it
                return super().execute_method(method, params, context)

        class CtxMethod(Method):
            def execute(self, params: Params, context: AdminContext) -> dict:
                return {'value': params.value, 'user_id': context.user_id}

        rpc = JSONRPC()
        group = LoggingGroup()
        group.register('test', CtxMethod())
        rpc.register('group', group)

        ctx = AdminContext(request_id='123', user_id=99, role='admin')
        result = rpc.call_method('group.test', {'value': 42}, context=ctx)

        # Should work correctly - context passed through
        self.assertEqual(result['value'], 42)
        self.assertEqual(result['user_id'], 99)
        self.assertEqual(group.log, ['Executing CtxMethod'])


class TestContextBackwardCompatibility(unittest.TestCase):
    """Test backward compatibility with existing code."""

    def test_old_methods_work_without_context(self):
        """Methods without context parameter continue working."""

        @dataclass
        class Params:
            a: int
            b: int

        class AddMethod(Method):
            def execute(self, params: Params) -> int:
                return params.a + params.b

        rpc = JSONRPC()
        rpc.register('add', AddMethod())

        # Old style - no context
        result = rpc.call_method('add', {'a': 1, 'b': 2})
        self.assertEqual(result, 3)

    def test_mixed_methods_in_same_rpc(self):
        """Can have both context and non-context methods in same RPC."""

        @dataclass
        class Params:
            value: int

        class NoCtxMethod(Method):
            def execute(self, params: Params) -> int:
                return params.value * 2

        class CtxMethod(Method):
            def execute(self, params: Params, context: BaseContext) -> dict:
                return {'value': params.value, 'request': context.request_id}

        rpc = JSONRPC(context_type=BaseContext)
        rpc.register('double', NoCtxMethod())
        rpc.register('info', CtxMethod())

        ctx = BaseContext(request_id='req-123')

        # Both work with same context
        result1 = rpc.call_method('double', {'value': 5}, context=ctx)
        result2 = rpc.call_method('info', {'value': 10}, context=ctx)

        self.assertEqual(result1, 10)
        self.assertEqual(result2, {'value': 10, 'request': 'req-123'})

    def test_rpc_without_context_type_works(self):
        """RPC without context_type specified works normally."""

        @dataclass
        class Params:
            value: int

        class SimpleMethod(Method):
            def execute(self, params: Params) -> int:
                return params.value

        rpc = JSONRPC()  # No context_type specified

        rpc.register('test', SimpleMethod())
        result = rpc.call_method('test', {'value': 42})

        self.assertEqual(result, 42)


if __name__ == '__main__':
    unittest.main()
