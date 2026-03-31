"""Main JSONRPC class for handling JSON-RPC protocol."""

import asyncio
import inspect
import json
import logging
import os
from collections.abc import Callable
from dataclasses import field, fields, make_dataclass
from typing import Any, cast, get_type_hints

from .errors import (
    InternalError,
    InvalidRequestError,
    JSONRPCError,
    MethodNotFoundError,
    ParseError,
)
from .method import Method, MethodGroup
from .request import parse_request
from .response import _dataclass_to_dict, build_error_response, build_response
from .types import Request, Version
from .validation import is_batch

logger = logging.getLogger('jsonrpc-lib')


def _validate_decorator_function(func: Callable[..., Any], sig: inspect.Signature, hints: dict[str, Any]) -> None:
    """Validate that function is suitable for @rpc.method decoration.

    Args:
        func: Function to validate
        sig: Function signature from inspect.signature()
        hints: Type hints from get_type_hints()

    Raises:
        TypeError: If function has missing type hints, unsupported parameters, or other issues
        ValueError: If function signature is invalid
    """
    func_name = func.__name__

    for param in sig.parameters.values():
        if param.annotation is inspect.Parameter.empty:
            raise TypeError(
                f"Function '{func_name}' parameter '{param.name}' is missing type hint. "
                f'The @rpc.method decorator requires type hints for all parameters. '
                f'Example: def {func_name}({param.name}: int, ...) -> ReturnType'
            )

    if 'return' not in hints or hints['return'] is type(None):
        raise TypeError(
            f"Function '{func_name}' is missing return type annotation. "
            f'The @rpc.method decorator requires a return type. '
            f"Add '-> ReturnType' to the function signature. "
            f'Example: def {func_name}(...) -> int:'
        )

    # Check for 'context' parameter (not supported in simplified decorator API)
    if 'context' in sig.parameters:
        raise TypeError(
            f"Function '{func_name}' cannot have 'context' parameter. "
            f'The @rpc.method decorator does not support context for simplicity. '
            f'This is a prototyping-only feature. '
            f'For production use with context support, create a Method subclass instead.'
        )


def _create_params_dataclass(func_name: str, sig: inspect.Signature, hints: dict[str, Any]) -> type | None:
    """Create dataclass for function parameters.

    Args:
        func_name: Function name (used for dataclass name)
        sig: Function signature from inspect.signature()
        hints: Type hints from get_type_hints()

    Returns:
        Dataclass type for parameters, or None if function has no parameters

    Example:
        For: def add(a: int, b: int) -> int
        Returns: dataclass with fields a: int, b: int
    """
    field_defs: list[Any] = []

    for param in sig.parameters.values():
        if param.name == 'self':
            continue

        param_type = hints[param.name]

        if param.default is inspect.Parameter.empty:
            field_defs.append((param.name, param_type))
        else:
            field_defs.append((param.name, param_type, field(default=param.default)))

    if not field_defs:
        return None

    # Create dataclass with capitalized function name + 'Params' suffix
    # Example: 'add' -> 'AddParams', 'fetch_data' -> 'FetchDataParams'
    dataclass_name = f'{func_name.title().replace("_", "")}Params'

    return make_dataclass(dataclass_name, field_defs, frozen=False)


def _create_method_class(func: Callable[..., Any], params_type: type | None, return_type: type, is_async: bool) -> type:
    """Create Method subclass for decorated function.

    Args:
        func: Original function to wrap
        params_type: Dataclass type for parameters (or None if no params)
        return_type: Return type annotation
        is_async: Whether function is async

    Returns:
        Method subclass with execute() method that calls original function

    The execute() method must have explicit type annotations so Method.__init_subclass__()
    can extract params_type and result_type for OpenAPI generation.

    The class docstring is copied from the original function for OpenAPI documentation.
    """
    if is_async:

        class DecoratedAsyncMethod(Method):
            async def execute(self, params: params_type) -> return_type:  # type: ignore[valid-type, override]
                if params is None:
                    return await func()  # type: ignore[no-any-return]
                kwargs = {f.name: getattr(params, f.name) for f in fields(params)}
                return await func(**kwargs)

        if func.__doc__:
            DecoratedAsyncMethod.__doc__ = func.__doc__

        return DecoratedAsyncMethod
    else:

        class DecoratedMethod(Method):
            def execute(self, params: params_type) -> return_type:  # type: ignore[valid-type, override]
                if params is None:
                    return func()  # type: ignore[no-any-return]
                kwargs = {f.name: getattr(params, f.name) for f in fields(params)}
                return func(**kwargs)

        if func.__doc__:
            DecoratedMethod.__doc__ = func.__doc__

        return DecoratedMethod


class JSONRPC:
    """Main JSON-RPC protocol handler.

    Handles request parsing, method dispatch, and response building.
    Supports both JSON-RPC 1.0 and 2.0 specifications.

    Example:
        >>> rpc = JSONRPC(version="2.0")
        >>> math = MethodGroup()
        >>> math.register("add", AddMethod())
        >>> rpc.register("math", math)
        >>> response = rpc.handle('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}')
    """

    def __init__(
        self,
        version: Version = '2.0',
        validate_results: bool = False,
        context_type: type | None = None,
        allow_batch: bool | None = None,
        allow_dict_params: bool | None = None,
        allow_list_params: bool | None = None,
        max_batch: int = 100,
        max_concurrent: int | None = None,
    ) -> None:
        """Initialize JSON-RPC handler.

        Args:
            version: Default protocol version for responses
            validate_results: If True, validate method results against result_type
            context_type: Expected context type for validation (None = no validation)
            allow_batch: Allow batch requests (None = spec-compliant default)
            allow_dict_params: Allow dict/object params (None = spec-compliant default)
            allow_list_params: Allow array params (None = spec-compliant default)
            max_batch: Maximum number of requests in a batch (-1 for unlimited, default: 100)
            max_concurrent: Maximum concurrent coroutines in async batch (None = os.cpu_count(),
                -1 for unlimited, default: None). Sync batch is unaffected.

        Defaults (spec-compliant):
            v1.0: allow_batch=False, allow_dict_params=False, allow_list_params=True
            v2.0: allow_batch=True, allow_dict_params=True, allow_list_params=True
        """
        if version not in ('1.0', '2.0'):
            raise ValueError(f"version must be '1.0' or '2.0', got {version!r}")

        if max_concurrent is not None and max_concurrent != -1 and max_concurrent < 1:
            raise ValueError(f'max_concurrent must be -1 (unlimited) or >= 1, got {max_concurrent}')

        self.version = version
        self.validate_results = validate_results
        self.context_type = context_type
        self.max_batch = max_batch
        self.max_concurrent = max_concurrent

        if allow_batch is None:
            self.allow_batch = version == '2.0'
        else:
            self.allow_batch = allow_batch

        if allow_dict_params is None:
            self.allow_dict_params = version == '2.0'
        else:
            self.allow_dict_params = allow_dict_params

        if allow_list_params is None:
            self.allow_list_params = True
        else:
            self.allow_list_params = allow_list_params

        if max_concurrent is not None:
            self._effective_max_concurrent = max_concurrent
        else:
            self._effective_max_concurrent = os.cpu_count() or 4

        self._root_group = MethodGroup()
        self._root_group._name = None
        self._root_group._inject_rpc(self)

        self._has_direct_root_methods = False

    def deserialize(self, data: str | bytes) -> Any:
        """Deserialize incoming JSON to a Python object.

        Called once per request in handle() / handle_async() before any
        routing or validation. Override to substitute a faster JSON library:

        Example:
            >>> import orjson
            >>> class FastRPC(JSONRPC):
            ...     def deserialize(self, data):
            ...         return orjson.loads(data)

        Default: json.loads(data) from the standard library.
        """
        return json.loads(data)

    def serialize(self, data: Any, **kwargs: Any) -> str:
        """Serialize a response dict (or list of dicts for batch) to a JSON string.

        Override to substitute a faster JSON library:

        Example:
            >>> import orjson
            >>> class FastRPC(JSONRPC):
            ...     def serialize(self, data):
            ...         return orjson.dumps(data).decode()

        Default: json.dumps(data) from the standard library.
        """
        return json.dumps(data, **kwargs)

    def serialize_result(self, result: Any) -> Any:
        """Convert method result to a JSON-serializable value.

        Override to customize serialization of custom types (datetime, Decimal, etc.)
        or to replace the default dataclass conversion with a faster alternative.

        Default: recursively converts dataclass instances to dicts via dataclasses.asdict().

        Args:
            result: Raw method return value

        Returns:
            JSON-serializable value

        Example:
            >>> class MyRPC(JSONRPC):
            ...     def serialize_result(self, result):
            ...         if isinstance(result, datetime):
            ...             return result.isoformat()
            ...         if isinstance(result, Decimal):
            ...             return str(result)
            ...         return super().serialize_result(result)
        """
        return _dataclass_to_dict(result)

    def register(self, name: str | None, target: Method | MethodGroup) -> None:
        """Register method or group.

        Universal registration supporting:
        - Direct root methods: register('ping', PingMethod())
        - Named groups: register('math', math_group)
        - Explicit root group: register(None, root_group)

        Args:
            name: Registration name
                  - str (non-empty): register as named group/method
                  - None: register as root group (error if root already has methods)
            target: Method instance or MethodGroup instance

        Raises:
            ValueError: If name is '' (empty string) or conflicts occur
            TypeError: If target is a class or invalid type

        Examples:
            # Direct root method (virtual root)
            rpc.register('ping', PingMethod())

            # Named group
            rpc.register('math', math_group)

            # Explicit root group (only if no direct methods)
            rpc.register(None, root_group)
        """
        if name == '':
            raise ValueError(
                'Name cannot be empty string. Use None for root group, or non-empty string for named registration.'
            )

        if isinstance(target, type):
            raise TypeError(
                f"Cannot register class '{target.__name__}'. "
                f"Must register instance: register('{name}', {target.__name__}())"
            )

        if name is None:
            if not isinstance(target, MethodGroup):
                raise TypeError(
                    f'Cannot register Method directly with name=None. '
                    f"Use a non-empty name: register('method_name', {type(target).__name__}())"
                )

            if self._has_direct_root_methods:
                raise ValueError(
                    'Cannot register explicit root group: root already has directly '
                    'registered methods. Clear them first or use named groups.'
                )
            if self._root_group._methods or self._root_group._subgroups:
                raise ValueError('Root group already exists with methods/subgroups')

            target._inject_rpc(self)
            target._name = None
            self._root_group = target
            return

        if isinstance(target, Method):
            if '.' in name:
                raise ValueError(f"Method name cannot contain '.': '{name}'")

            # Fail-fast: check if method is already registered elsewhere
            if hasattr(target, 'rpc'):
                raise ValueError(
                    f"Method instance '{target.__class__.__name__}' is already registered "
                    f'in another JSONRPC/MethodGroup. Create a new instance for each registration.'
                )

            if target.accepts_context and target.context_type is not None and self.context_type is not None:
                if not issubclass(target.context_type, self.context_type):
                    raise TypeError(
                        f'Cannot register {target.__class__.__name__}: '
                        f'method context_type {target.context_type.__name__} must be '
                        f'subclass of RPC context_type {self.context_type.__name__}'
                    )

            target.rpc = self
            self._root_group._methods[name] = target
            self._has_direct_root_methods = True
            return

        if isinstance(target, MethodGroup):
            if '.' in name:
                raise ValueError(f"Group name cannot contain '.': '{name}'")

            if target.context_type is not None and self.context_type is not None:
                if not issubclass(target.context_type, self.context_type):
                    raise TypeError(
                        f'Cannot register {target.__class__.__name__}: '
                        f'group context_type {target.context_type.__name__} must be '
                        f'subclass of RPC context_type {self.context_type.__name__}'
                    )

            target._inject_rpc(self)
            self._root_group.register(name, target)
            return

        raise TypeError(f'Expected Method or MethodGroup instance, got {type(target).__name__}')

    def unregister(self, path: str) -> None:
        """Unregister a method or group by name or dotted path.

        Works for both methods and groups at any nesting level:
        - 'ping'       → unregister root-level method or group
        - 'math'       → unregister named group
        - 'math.add'   → unregister method inside a group

        Args:
            path: Name or dotted path to unregister

        Raises:
            ValueError: If path is empty
            KeyError: If path not found
        """
        if not path:
            raise ValueError('Path cannot be empty')

        parts = path.split('.')

        if len(parts) == 1:
            self._root_group.unregister(path)
        else:
            *group_path, leaf = parts
            group = self._root_group
            for part in group_path:
                subgroup = group.get_subgroup(part)
                if subgroup is None:
                    raise KeyError(f"Path '{path}' not found")
                group = subgroup
            group.unregister(leaf)

    def get_root_group(self) -> MethodGroup:
        """Get root method group (for OpenAPI generator).

        Returns:
            Root MethodGroup instance
        """
        return self._root_group

    def handle(self, raw_data: str | bytes, context: Any = None) -> str | None:
        """Process incoming JSON-RPC message and return response.

        Args:
            raw_data: Raw JSON string or bytes
            context: Optional context object passed to methods

        Returns:
            JSON response string, or None for notifications
        """
        try:
            try:
                data = self.deserialize(raw_data)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                err: JSONRPCError = ParseError(f'Invalid JSON: {e}')
                response = build_error_response(err, None, self.version)
                return self.serialize(response)

            if is_batch(data):
                if not self.allow_batch:
                    err = InvalidRequestError('Batch requests not allowed')
                    response = build_error_response(err, None, self.version)
                    return self.serialize(response)
                return self._handle_batch(data, context=context)

            return self._handle_single(data, context=context)

        except Exception as e:
            logger.error('Unhandled exception in handle()', exc_info=True)
            err = InternalError(str(e))
            response = build_error_response(err, None, self.version)
            return self.serialize(response)

    async def handle_async(self, raw_data: str | bytes, context: Any = None) -> str | None:
        """Process incoming JSON-RPC message asynchronously.

        Args:
            raw_data: Raw JSON string or bytes
            context: Optional context object passed to methods

        Returns:
            JSON response string, or None for notifications
        """
        try:
            try:
                data = self.deserialize(raw_data)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                err: JSONRPCError = ParseError(f'Invalid JSON: {e}')
                response = build_error_response(err, None, self.version)
                return self.serialize(response)

            if is_batch(data):
                if not self.allow_batch:
                    err = InvalidRequestError('Batch requests not allowed')
                    response = build_error_response(err, None, self.version)
                    return self.serialize(response)
                return await self._handle_batch_async(data, context=context)

            return await self._handle_single_async(data, context=context)

        except Exception as e:
            logger.error('Unhandled exception in handle_async()', exc_info=True)
            err = InternalError(str(e))
            response = build_error_response(err, None, self.version)
            return self.serialize(response)

    def _process_single(self, data: dict[str, Any], context: Any = None) -> dict[str, Any] | None:
        """Process a single request and return response as dict (or None for notifications)."""
        request_id: str | int | None = None
        version = self.version

        try:
            parsed = parse_request(
                data,
                allow_dict_params=self.allow_dict_params,
                allow_list_params=self.allow_list_params,
            )
            # Single dict input always returns single Request (not list)
            request = cast(Request, parsed)
            request_id = request.id
            version = request.version

            if request.is_notification:
                try:
                    self._root_group.dispatch(
                        request.method,
                        request.params,
                        request.id,
                        validate_result=self.validate_results,
                        context=context,
                    )
                except Exception:
                    logger.debug('Notification error suppressed (per JSON-RPC spec)', exc_info=True)
                return None

            result = self._root_group.dispatch(
                request.method,
                request.params,
                request.id,
                validate_result=self.validate_results,
                context=context,
            )

            serialized = self.serialize_result(result)
            return build_response(serialized, request_id, version)  # type: ignore[arg-type]

        except JSONRPCError as e:
            return build_error_response(e, request_id, version)
        except Exception as e:
            logger.error('Unhandled exception in method dispatch', exc_info=True)
            err = InternalError(str(e))
            return build_error_response(err, request_id, version)

    async def _process_single_async(self, data: dict[str, Any], context: Any = None) -> dict[str, Any] | None:
        """Process a single request asynchronously and return response as dict."""
        request_id: str | int | None = None
        version = self.version

        try:
            parsed = parse_request(
                data,
                allow_dict_params=self.allow_dict_params,
                allow_list_params=self.allow_list_params,
            )
            # Single dict input always returns single Request (not list)
            request = cast(Request, parsed)
            request_id = request.id
            version = request.version

            if request.is_notification:
                try:
                    await self._root_group.dispatch_async(
                        request.method,
                        request.params,
                        request.id,
                        validate_result=self.validate_results,
                        context=context,
                    )
                except Exception:
                    logger.debug('Notification error suppressed (per JSON-RPC spec)', exc_info=True)
                return None

            result = await self._root_group.dispatch_async(
                request.method,
                request.params,
                request.id,
                validate_result=self.validate_results,
                context=context,
            )

            serialized = self.serialize_result(result)
            return build_response(serialized, request_id, version)  # type: ignore[arg-type]

        except JSONRPCError as e:
            return build_error_response(e, request_id, version)
        except Exception as e:
            logger.error('Unhandled exception in async method dispatch', exc_info=True)
            err = InternalError(str(e))
            return build_error_response(err, request_id, version)

    def _handle_single(self, data: dict[str, Any], context: Any = None) -> str | None:
        """Handle a single request synchronously."""
        response = self._process_single(data, context=context)
        if response is None:
            return None
        try:
            return self.serialize(response)
        except (TypeError, ValueError) as e:
            logger.error('Serialization error in response', exc_info=True)
            request_id = response.get('id')
            err = InternalError(str(e))
            error_response = build_error_response(err, request_id, self.version)
            return self.serialize(error_response)

    async def _handle_single_async(self, data: dict[str, Any], context: Any = None) -> str | None:
        """Handle a single request asynchronously."""
        response = await self._process_single_async(data, context=context)
        if response is None:
            return None
        try:
            return self.serialize(response)
        except (TypeError, ValueError) as e:
            logger.error('Serialization error in response', exc_info=True)
            request_id = response.get('id')
            err = InternalError(str(e))
            error_response = build_error_response(err, request_id, self.version)
            return self.serialize(error_response)

    def _handle_batch(self, data: list[Any], context: Any = None) -> str | None:
        """Handle batch request synchronously."""
        if not data:
            error = InvalidRequestError('Empty batch request')
            response = build_error_response(error, None, self.version)
            return self.serialize(response)

        if self.max_batch != -1 and len(data) > self.max_batch:
            error = InvalidRequestError(f'Batch too large: {len(data)} requests, maximum is {self.max_batch}')
            response = build_error_response(error, None, self.version)
            return self.serialize(response)

        responses = []
        for item in data:
            result = self._process_single(item, context=context)
            if result is not None:
                responses.append(result)

        if not responses:
            return None

        try:
            return self.serialize(responses)
        except (TypeError, ValueError) as e:
            logger.error('Serialization error in batch response', exc_info=True)
            err = InternalError(str(e))
            error_response = build_error_response(err, None, self.version)
            return self.serialize(error_response)

    async def _handle_batch_async(self, data: list[Any], context: Any = None) -> str | None:
        """Handle batch request asynchronously (concurrent execution)."""
        if not data:
            error = InvalidRequestError('Empty batch request')
            response = build_error_response(error, None, self.version)
            return self.serialize(response)

        if self.max_batch != -1 and len(data) > self.max_batch:
            error = InvalidRequestError(f'Batch too large: {len(data)} requests, maximum is {self.max_batch}')
            response = build_error_response(error, None, self.version)
            return self.serialize(response)

        limit = self._effective_max_concurrent
        if limit == -1:
            results = await asyncio.gather(*[self._process_single_async(item, context=context) for item in data])
        else:
            sem = asyncio.Semaphore(limit)

            async def _limited(item: Any) -> Any:
                async with sem:
                    return await self._process_single_async(item, context=context)

            results = await asyncio.gather(*[_limited(item) for item in data])
        responses = [r for r in results if r is not None]

        if not responses:
            return None

        try:
            return self.serialize(responses)
        except (TypeError, ValueError) as e:
            logger.error('Serialization error in batch response', exc_info=True)
            err = InternalError(str(e))
            error_response = build_error_response(err, None, self.version)
            return self.serialize(error_response)

    def call_method(
        self,
        method: str,
        params: list[Any] | dict[str, Any] | None = None,
        id: str | int | None = None,
        validate_result: bool | None = None,
        context: Any = None,
    ) -> Any:
        """Call a registered method directly (no JSON serialization).

        Use this for internal method-to-method calls.

        Args:
            method: Full method name (e.g., "math.add")
            params: Method parameters
            id: Request ID (optional)
            validate_result: Override global validate_results setting.
                             None = use global setting, True/False = override.
            context: Optional context object passed to method

        Returns:
            Raw method result

        Raises:
            MethodNotFoundError: If method doesn't exist
            InvalidParamsError: If params validation fails
            InvalidResultError: If result validation fails
        """
        should_validate = validate_result if validate_result is not None else self.validate_results
        return self._root_group.dispatch(method, params, id, validate_result=should_validate, context=context)

    async def call_method_async(
        self,
        method: str,
        params: list[Any] | dict[str, Any] | None = None,
        id: str | int | None = None,
        validate_result: bool | None = None,
        context: Any = None,
    ) -> Any:
        """Call a registered method asynchronously.

        Use this for internal method-to-method calls with async methods.

        Args:
            method: Full method name (e.g., "math.add")
            params: Method parameters
            id: Request ID (optional)
            validate_result: Override global validate_results setting.
                             None = use global setting, True/False = override.
            context: Optional context object passed to method

        Returns:
            Raw method result

        Raises:
            MethodNotFoundError: If method doesn't exist
            InvalidParamsError: If params validation fails
            InvalidResultError: If result validation fails
        """
        should_validate = validate_result if validate_result is not None else self.validate_results
        return await self._root_group.dispatch_async(
            method, params, id, validate_result=should_validate, context=context
        )

    def list_methods(self) -> list[str]:
        """List all registered method names (with full paths).

        Returns:
            Sorted list of method paths (e.g., ["ping", "math.add", "sudo.user.delete"])
        """
        return sorted(self._root_group.list_methods(recursive=True))

    def get_method(self, path: str) -> 'Any | None':
        """Get method by full path.

        Args:
            path: Full method path (e.g., "math.add")

        Returns:
            Method instance or None if not found
        """
        try:
            _, method = self._root_group.resolve_path(path)
            return method
        except (KeyError, MethodNotFoundError):
            return None

    def method(self, name_or_func: str | Callable[..., Any] | None = None) -> Callable[..., Any]:
        """Decorator to register a function as a JSON-RPC method (prototyping only).

        This decorator provides a quick way to register functions as JSON-RPC
        methods for prototyping and simple use cases. It automatically:
        - Creates a dataclass from function parameters
        - Wraps the function in a Method subclass
        - Registers the method with the RPC instance

        LIMITATIONS (use Method subclass for production):
        - No context support
        - No group registration (root level only)
        - No custom middleware
        - Less control over parameter validation
        - No notification methods (-> None return type not supported)
        - PROTOTYPING ONLY - not recommended for production use

        Args:
            name_or_func: Optional method name (defaults to function.__name__)
                         Can be used as @rpc.method or @rpc.method("custom_name")

        Returns:
            The original function (for testing and introspection)

        Raises:
            TypeError: If function lacks type hints or has unsupported parameters
            ValueError: If method name contains '.' or is invalid

        Example - Basic usage:
            >>> rpc = JSONRPC()
            >>>
            >>> @rpc.method
            >>> def add(a: int, b: int) -> int:
            >>>     '''Add two numbers.'''
            >>>     return a + b
            >>>
            >>> # Use via JSON-RPC
            >>> rpc.handle('{"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": 1}')
            >>> '{"jsonrpc": "2.0", "result": 3, "id": 1}'
            >>>
            >>> # Original function still works
            >>> add(1, 2)
            >>> 3

        Example - Custom name:
            >>> @rpc.method("custom_add")
            >>> def my_add(a: int, b: int) -> int:
            >>>     return a + b

        Example - Default values:
            >>> @rpc.method
            >>> def greet(name: str, greeting: str = "Hello") -> str:
            >>>     return f"{greeting}, {name}!"

        Example - Async function:
            >>> @rpc.method
            >>> async def fetch(url: str) -> dict:
            >>>     return await http_get(url)

        For production use, create Method subclasses instead:
            >>> class Add(Method):
            >>>     def execute(self, params: AddParams) -> int:
            >>>         return params.a + params.b
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if isinstance(name_or_func, str):
                method_name = name_or_func
            else:
                method_name = func.__name__

            # Decorator only supports JSON-RPC 2.0 (prototyping feature)
            if self.version != '2.0':
                raise ValueError(
                    f'The @rpc.method decorator is only available for JSON-RPC 2.0 (current version: {self.version}). '
                    f'This restriction is intentional - the decorator is designed exclusively for prototyping. '
                    f'For JSON-RPC 1.0 or production use, create Method subclasses instead:\n\n'
                    f'    class {method_name.title()}(Method):\n'
                    f'        def execute(self, params: ParamsType) -> ResultType:\n'
                    f'            ...'
                )

            if not method_name or method_name == '':
                raise ValueError(
                    "Method name cannot be empty. Use function name or provide explicit name: @rpc.method('name')"
                )

            if '.' in method_name:
                raise ValueError(
                    f"Method name '{method_name}' cannot contain '.'. "
                    f'The @rpc.method decorator only supports root-level registration. '
                    f'Groups are not supported in this simplified API. '
                    f'For group registration, create Method subclasses and use MethodGroup.'
                )

            sig = inspect.signature(func)
            hints = get_type_hints(func)

            _validate_decorator_function(func, sig, hints)

            params_type = _create_params_dataclass(func.__name__, sig, hints)

            return_type = hints['return']

            is_async = asyncio.iscoroutinefunction(func)
            MethodClass = _create_method_class(func, params_type, return_type, is_async)

            method_instance = MethodClass()
            self.register(method_name, method_instance)

            return func

        if callable(name_or_func):
            return decorator(name_or_func)
        else:
            return decorator
