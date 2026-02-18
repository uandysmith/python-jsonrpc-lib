"""Method and MethodGroup classes for JSON-RPC methods."""

import asyncio
import inspect
from dataclasses import is_dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Union,
    get_type_hints,
)

from .validation import (
    _unwrap_optional,
    validate_params,
    validate_result_type,
)

if TYPE_CHECKING:
    from .jsonrpc import JSONRPC


class Method:
    """Base class for RPC methods.

    Subclasses must:
    - Implement `execute(self, params: ParamsType) -> ResultType` with type hints
    - ParamsType must be a dataclass (or None for no params)
    - ResultType can be any type

    Type hints are REQUIRED and will be automatically extracted to set
    params_type and result_type attributes.

    Name is specified during registration, not in the class.

    Attributes:
        params_type: Auto-extracted from execute() params hint
        result_type: Auto-extracted from execute() return hint
        rpc: Reference to JSONRPC instance (injected on registration)
    """

    params_type: type = type(None)
    result_type: type = type(None)
    rpc: 'JSONRPC'
    accepts_context: bool = False
    context_type: type | None = None
    _is_async_method: bool = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Extract params_type and result_type from execute() signature."""
        super().__init_subclass__(**kwargs)

        try:
            hints = get_type_hints(cls.execute)

            sig = inspect.signature(cls.execute)
            params_list = list(sig.parameters.values())

            if len(params_list) < 2:
                raise TypeError(f"{cls.__name__}.execute() must have 'params' parameter")

            params_param = params_list[1]  # Skip 'self'

            if params_param.name != 'params':
                raise TypeError(
                    f"{cls.__name__}.execute() must have 'params' as second parameter, got '{params_param.name}'"
                )

            if 'params' not in hints:
                raise TypeError(f"{cls.__name__}.execute() must have type hint for 'params' parameter")

            params_type = hints['params']

            params_type = _unwrap_optional(params_type)

            if params_type is not type(None) and not is_dataclass(params_type):
                raise TypeError(f'{cls.__name__}.execute() params type must be a dataclass or None, got {params_type}')

            cls.params_type = params_type

            if 'context' in sig.parameters:
                if 'context' not in hints:
                    raise TypeError(f"{cls.__name__}.execute() has 'context' parameter but no type hint")

                cls.accepts_context = True
                cls.context_type = hints['context']

                context_param = params_list[2]
                if context_param.name != 'context':
                    raise TypeError(
                        f"{cls.__name__}.execute() third parameter must be 'context', got '{context_param.name}'"
                    )
            else:
                cls.accepts_context = False
                cls.context_type = None

            if 'return' not in hints:
                raise TypeError(f'{cls.__name__}.execute() must have return type annotation')

            cls.result_type = hints['return']
            cls._is_async_method = asyncio.iscoroutinefunction(cls.execute)

        except TypeError:
            raise
        except Exception as e:
            # Wrap other exceptions
            raise TypeError(f'Failed to infer types for {cls.__name__}: {e}') from e

    def execute(self, params: Any, context: Any = None) -> Any:
        """Execute the method with validated params and optional context.

        Args:
            params: Validated dataclass instance, or None if params_type is None
            context: Optional context object (only passed if accepts_context=True)

        Returns:
            Method result (any JSON-serializable value)
        """
        raise NotImplementedError(f'Method {self.__class__.__name__} must implement execute()')

    def _is_async(self) -> bool:
        """Check if execute() is an async method."""
        return self._is_async_method


class MethodGroup:
    """Hierarchical container for methods with support for nesting and middleware.

    MethodGroup acts as a tree structure supporting:
    - Nested subgroups (e.g., sudo -> user -> management)
    - Method registration with arbitrary names
    - Middleware via execute_method() override
    - Built-in routing and dispatch (replaces Dispatcher)

    Name is set during registration, not in constructor.

    Attributes:
        name: Group name (read-only property, None for root group)
        rpc: Reference to JSONRPC instance (injected on registration)

    Examples:
        # Basic usage
        math = MethodGroup()
        math.register('add', AddMethod())

        # Nested groups
        sudo = MethodGroup()
        user = MethodGroup()
        user.register('adduser', AddUserMethod())
        sudo.register('user', user)

        # Custom names
        math.register('add_2', AddXMethod(2))
        math.register('add_5', AddXMethod(5))

        # Middleware
        class SudoGroup(MethodGroup):
            def execute_method(self, method, params):
                if not self._check_auth():
                    raise PermissionError("Requires sudo")
                return super().execute_method(method, params)
    """

    rpc: 'JSONRPC'
    context_type: type | None = None  # Extracted from execute_method() signature
    accepts_context_param: bool = True  # Whether execute_method accepts context parameter

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Extract context_type and accepts_context_param from execute_method() signature."""
        super().__init_subclass__(**kwargs)

        # Check if execute_method is overridden
        if cls.execute_method is MethodGroup.execute_method:
            # Not overridden, base implementation accepts context
            cls.context_type = None
            cls.accepts_context_param = True
            return

        try:
            sig = inspect.signature(cls.execute_method)

            if 'context' in sig.parameters:
                # MethodGroup has context parameter - can pass context to methods
                cls.accepts_context_param = True
                hints = get_type_hints(cls.execute_method)

                if 'context' not in hints:
                    # context parameter without type hint → no validation (context_type = None)
                    # This allows: def execute_method(self, method, params, context=None)
                    cls.context_type = None
                else:
                    # context parameter with type hint → validate type
                    # This allows: def execute_method(self, method, params, context: AdminContext)
                    cls.context_type = hints['context']
            else:
                # execute_method overridden but no context parameter
                # Cannot pass context to methods!
                cls.accepts_context_param = False
                cls.context_type = None

        except Exception as e:
            raise TypeError(f'Failed to infer context_type for {cls.__name__}: {e}') from e

    def __init__(self) -> None:
        """Initialize method group (name set during registration)."""
        self._name: str | None = None  # Internal, set by parent
        self._methods: dict[str, Method] = {}
        self._subgroups: dict[str, MethodGroup] = {}

    @property
    def name(self) -> str | None:
        """Get group name (read-only)."""
        return self._name

    def register(self, name: str, target: Union[Method, 'MethodGroup']) -> None:
        """Register method or subgroup instance.

        Unified registration API supporting both methods and subgroups.

        Args:
            name: Registration name (must not contain '.', cannot be empty or None)
            target: Method instance or MethodGroup instance

        Raises:
            ValueError: If name is None, empty, contains '.', or already registered
            TypeError: If target is a class or invalid type

        Examples:
            group.register('add', AddMethod())
            group.register('user', user_subgroup)
        """
        # Validate name
        if name is None:
            raise ValueError('Name cannot be None in MethodGroup.register()')
        if not name or name == '':
            raise ValueError('Name cannot be empty string. Use None only in JSONRPC.register()')
        if '.' in name:
            raise ValueError(f"Name cannot contain '.': '{name}'")
        if name in self._methods:
            raise ValueError(f"Method '{name}' already registered")
        if name in self._subgroups:
            raise ValueError(f"Subgroup '{name}' already registered")

        if isinstance(target, type):
            raise TypeError(
                f"Cannot register class '{target.__name__}'. "
                f"Must register instance: register('{name}', {target.__name__}())"
            )

        # Validate context_type compatibility
        if isinstance(target, Method):
            # Fail-fast: check if method is already registered elsewhere
            if hasattr(target, 'rpc'):
                raise ValueError(
                    f"Method instance '{target.__class__.__name__}' is already registered. "
                    f'Create a new instance for each registration.'
                )

            # Check if group can pass context to method
            if target.accepts_context and not self.accepts_context_param:
                raise TypeError(
                    f'Cannot register {target.__class__.__name__}: '
                    f'method requires context but group execute_method() does not accept context parameter. '
                    f'Add context parameter to execute_method: '
                    f'def execute_method(self, method, params, context=None)'
                )

            # Check context type hierarchy
            if target.accepts_context and target.context_type is not None and self.context_type is not None:
                if not issubclass(target.context_type, self.context_type):
                    raise TypeError(
                        f'Cannot register {target.__class__.__name__}: '
                        f'method context_type {target.context_type.__name__} must be '
                        f'subclass of group context_type {self.context_type.__name__}'
                    )

        elif isinstance(target, MethodGroup):
            if target.context_type is not None and self.context_type is not None:
                if not issubclass(target.context_type, self.context_type):
                    raise TypeError(
                        f'Cannot register {target.__class__.__name__}: '
                        f'group context_type {target.context_type.__name__} must be '
                        f'subclass of parent context_type {self.context_type.__name__}'
                    )

        # Register based on type
        if isinstance(target, MethodGroup):
            # Subgroup
            target._name = name
            if hasattr(self, 'rpc') and self.rpc is not None:
                target._inject_rpc(self.rpc)
            self._subgroups[name] = target

        elif isinstance(target, Method):
            # Method instance
            if hasattr(self, 'rpc') and self.rpc is not None:
                target.rpc = self.rpc
            self._methods[name] = target

        else:
            raise TypeError(f'Expected Method or MethodGroup instance, got {type(target).__name__}')

    def unregister(self, name: str) -> None:
        """Unregister a method or subgroup by name.

        Args:
            name: Method or subgroup name (not a path)

        Raises:
            KeyError: If name not found in either methods or subgroups
        """
        if name in self._methods:
            del self._methods[name]
        elif name in self._subgroups:
            del self._subgroups[name]
        else:
            raise KeyError(f"'{name}' not found in group '{self._name}'")

    def resolve_path(self, path: str) -> tuple['MethodGroup', Method]:
        """Resolve method path to (group, method) tuple.

        This is the core routing logic that replaces Dispatcher._resolve_method().

        Args:
            path: Dot-separated path (e.g., "add", "user.add", "sudo.user.addgroup")

        Returns:
            Tuple of (final_group, method)

        Raises:
            KeyError: If path not found (method or subgroup missing)

        Examples:
            "add" → (self, self._methods["add"])
            "user.add" → (user_group, user_group._methods["add"])
            "sudo.user.add" → (user_group, user_group._methods["add"])
        """
        from .errors import MethodNotFoundError

        parts = path.split('.')

        # Single name - look in this group
        if len(parts) == 1:
            method = self._methods.get(path)
            if method:
                return (self, method)
            raise MethodNotFoundError(f"Method '{path}' not found in group '{self._name}'")

        # Multi-part path - navigate subgroups
        first, *rest = parts

        if first in self._subgroups:
            return self._subgroups[first].resolve_path('.'.join(rest))

        raise MethodNotFoundError(f"Path '{path}' not found: no subgroup '{first}' in group '{self._name}'")

    def dispatch(
        self,
        path: str,
        params: list[Any] | dict[str, Any] | None,
        id: str | int | None,
        validate_result: bool = False,
        context: Any = None,
    ) -> Any:
        """Dispatch method call synchronously (with middleware support).

        This method replaces Dispatcher.dispatch(). It can be overridden
        by subclasses to implement middleware logic.

        Args:
            path: Method path (e.g., "add", "user.add")
            params: Method parameters
            id: Request ID
            validate_result: Whether to validate result type
            context: Optional context object

        Returns:
            Method result

        Raises:
            MethodNotFoundError: If method not found
            InvalidParamsError: If params validation fails
            RuntimeError: If method is async (use dispatch_async instead)
        """
        group, method = self.resolve_path(path)

        validated_params = validate_params(params, method.params_type)

        if method._is_async():
            raise RuntimeError(f"Method '{path}' is async, use dispatch_async() instead")

        # Execute via middleware hook with context
        result = group.execute_method(method, validated_params, context=context)

        # Validate result if requested
        if validate_result and method.result_type is not None:
            validate_result_type(result, method.result_type)

        return result

    async def dispatch_async(
        self,
        path: str,
        params: list[Any] | dict[str, Any] | None,
        id: str | int | None,
        validate_result: bool = False,
        context: Any = None,
    ) -> Any:
        """Dispatch method call asynchronously (with middleware support).

        Override this method to implement async middleware.

        Args:
            path: Method path
            params: Method parameters
            id: Request ID
            validate_result: Whether to validate result
            context: Optional context object

        Returns:
            Method result
        """
        group, method = self.resolve_path(path)

        validated_params = validate_params(params, method.params_type)

        if method._is_async():
            result = await group.execute_method_async(method, validated_params, context=context)
        else:
            result = group.execute_method(method, validated_params, context=context)

        # Validate result if requested
        if validate_result and method.result_type is not None:
            validate_result_type(result, method.result_type)

        return result

    def execute_method(self, method: Method, params: Any, context: Any = None) -> Any:
        """Execute method synchronously (override for middleware).

        Override this in subclasses to add pre/post processing:

        Examples:
            class SudoGroup(MethodGroup):
                def execute_method(self, method, params, context):
                    if not self._check_sudo_rights():
                        raise PermissionError("Sudo required")
                    return super().execute_method(method, params, context)

        Args:
            method: Method instance to execute
            params: Validated params
            context: Optional context object

        Returns:
            Method result
        """
        # Runtime validation (ONLY here!)
        if method.accepts_context and context is not None and method.context_type is not None:
            if not isinstance(context, method.context_type):
                raise TypeError(
                    f'Expected context of type {method.context_type.__name__}, got {type(context).__name__}'
                )

        # Conditional context passing
        if method.accepts_context:
            return method.execute(params, context)
        else:
            return method.execute(params)

    async def execute_method_async(self, method: Method, params: Any, context: Any = None) -> Any:
        """Execute method asynchronously (override for middleware).

        Override this in subclasses for async middleware.

        Args:
            method: Method instance to execute
            params: Validated params
            context: Optional context object

        Returns:
            Method result
        """
        # Runtime validation (ONLY here!)
        if method.accepts_context and context is not None and method.context_type is not None:
            if not isinstance(context, method.context_type):
                raise TypeError(
                    f'Expected context of type {method.context_type.__name__}, got {type(context).__name__}'
                )

        # Conditional context passing
        if method.accepts_context:
            return await method.execute(params, context)
        else:
            return await method.execute(params)

    def list_methods(self, recursive: bool = False) -> list[str]:
        """List method names in this group.

        Args:
            recursive: If True, include subgroup methods with paths

        Returns:
            List of method names (without group prefix)

        Examples:
            recursive=False: ["add", "subtract"]
            recursive=True: ["add", "subtract", "user.add", "user.delete"]
        """
        result = list(self._methods.keys())

        if recursive:
            for subgroup_name, subgroup in self._subgroups.items():
                for method_name in subgroup.list_methods(recursive=True):
                    result.append(f'{subgroup_name}.{method_name}')

        return result

    def get_method(self, name: str) -> Method | None:
        """Get method by name (without path, local only).

        Args:
            name: Method name (not a path)

        Returns:
            Method instance or None
        """
        return self._methods.get(name)

    def get_subgroup(self, name: str) -> 'MethodGroup | None':
        """Get subgroup by name.

        Args:
            name: Subgroup name

        Returns:
            MethodGroup instance or None
        """
        return self._subgroups.get(name)

    def get_all_groups(self) -> dict[str, 'MethodGroup']:
        """Get all subgroups (shallow, not recursive).

        Returns:
            Dict of {name: group}
        """
        return dict(self._subgroups)

    def _inject_rpc(self, rpc: 'JSONRPC') -> None:
        """Inject RPC reference into group and all children (recursive).

        Args:
            rpc: JSONRPC instance
        """
        self.rpc = rpc

        for method in self._methods.values():
            method.rpc = rpc

        for subgroup in self._subgroups.values():
            subgroup._inject_rpc(rpc)
