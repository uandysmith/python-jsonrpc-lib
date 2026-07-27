"""Method and MethodGroup classes for JSON-RPC methods."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, is_dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Union,
    get_type_hints,
)

from .errors import _DispatchWiringError, clip
from .validation import (
    _unwrap_optional,
    find_initvar_fields,
    find_unsupported_annotations,
    validate_params,
    validate_result_type,
)

if TYPE_CHECKING:
    from .jsonrpc import JSONRPC

logger = logging.getLogger('jsonrpc-lib')


def _invalidate_routes(group: 'MethodGroup') -> None:
    """Drop the memoized routes a change at this group can have invalidated.

    That is exactly this group and its ancestors. A cache holds paths resolved
    from its own group downwards, so only the groups this one sits under can have
    an entry that passes through it; a sibling branch cannot, and neither can
    anything below. Single ownership is what makes that list complete - a group
    belongs to one tree, in one place.

    Not a JSONRPC attribute, and not a module-level counter either. The cache
    lives on MethodGroup because that is what resolves a path, and a MethodGroup
    is built, mutated and dispatched on long before - or entirely without - any
    JSONRPC to hang a counter on. A module-level epoch avoided that by making
    every registration in the process invalidate every cache in it, which is
    correct but says something false about what depends on what, and puts a
    global read on the dispatch path to say it.
    """
    node: MethodGroup | None = group
    seen: set[int] = set()
    while node is not None and id(node) not in seen:
        node._route_cache.clear()
        seen.add(id(node))
        node = node._owner


@dataclass(frozen=True, slots=True)
class CallInfo:
    """Everything a middleware group needs to know about the call it is wrapping.

    Attributes:
        path: Full dotted path as the caller requested it (e.g. 'api.users.get')
        method: The resolved Method instance
        params: Validated params (a dataclass instance, or None for no-params methods)
        id: The request id, for correlating a middleware's log lines with the
            response the caller received. None for a notification, and also None
            for a call made through dispatch() without one - so it identifies a
            request when there is one to identify, and is not a sequence number.
    """

    path: str
    method: 'Method'
    params: Any
    id: str | int | None = None


class Method:
    """Base class for RPC methods.

    Subclasses must:
    - Implement `execute(self, params: ParamsType) -> ResultType` with type hints
    - ParamsType must be a dataclass (or None for no params)
    - ResultType can be any type

    Type hints are REQUIRED and will be automatically extracted to set
    params_type and result_type attributes.

    Extraction runs only for classes that define their own `execute()`. A class
    that does not is an intermediate base: it may carry shared domain logic and
    inherits the extracted attributes through normal attribute lookup. Such a
    class cannot be registered - only its concrete subclasses can.

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
    _owner: 'MethodGroup | None' = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Extract params_type and result_type from execute() signature."""
        super().__init_subclass__(**kwargs)

        if 'execute' not in cls.__dict__:
            # Intermediate base: params_type, result_type, accepts_context,
            # context_type and _is_async_method stay inherited.
            return

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

            # `params: P | None` used to be quietly rewritten to `P` here, and
            # every trace of the `| None` was gone by the next line: params_type
            # was P, the `if params is None` branch the author wrote could never
            # run, and a call with no params answered "Missing required
            # parameters" against a signature that plainly permits their absence.
            # mypy checked the annotation, the library discarded it, and nothing
            # said so. Refused rather than supported because the two spellings
            # would mean different things - `P` with defaulted fields already
            # covers "callable with no params".
            unwrapped = _unwrap_optional(params_type)
            if unwrapped is not params_type:
                raise TypeError(
                    f'{cls.__name__}.execute() params type is {params_type}. An optional params type is not '
                    f'supported: the library builds the dataclass from whatever arrived, so None never reaches '
                    f'execute() and that branch is dead. Declare params: {getattr(unwrapped, "__name__", unwrapped)} '
                    f'and give every field a default if the method should be callable with no params.'
                )
            params_type = unwrapped

            if params_type is not type(None) and not is_dataclass(params_type):
                raise TypeError(f'{cls.__name__}.execute() params type must be a dataclass or None, got {params_type}')

            if params_type is not type(None):
                unsupported = find_unsupported_annotations(params_type)
                if unsupported:
                    raise TypeError(
                        f'{cls.__name__}.execute() params type {params_type.__name__} has field(s) whose '
                        f'type cannot be filled from JSON: {", ".join(unsupported)}. '
                        f'Parameters are limited to what JSON can express - strings, numbers, booleans, '
                        f'arrays, objects, null, and dataclasses built from those. For anything else, '
                        f'declare the field as the type that arrives on the wire (usually str) and convert '
                        f'it in __post_init__, raising InvalidParamsError on input you will not accept.'
                    )

                initvars = find_initvar_fields(params_type)
                if initvars:
                    raise TypeError(
                        f'{cls.__name__}.execute() params type {params_type.__name__} declares InitVar '
                        f'field(s) {initvars}. An InitVar cannot be filled from the wire - '
                        f'dataclasses.fields() does not report it, so the caller is told it is an unknown '
                        f'parameter, while __init__ requires it anyway. Make it a regular field, or move '
                        f'the derivation into the method.'
                    )

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
    - Middleware via around_call() (whole path) or execute_method() (owning group)
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

        # Middleware covering every method below this group, nested or not.
        # Refuse with a JSONRPCError subclass: anything else is a -32603 with no
        # text, and a traceback in the log per unauthorized attempt.
        class Forbidden(JSONRPCError):
            code = -32010
            message = 'Forbidden'

        class SudoGroup(MethodGroup):
            def around_call(self, call, context, call_next):
                if not self._check_auth(context):
                    raise Forbidden('Requires sudo')
                return call_next(context)
    """

    rpc: 'JSONRPC'
    context_type: type | None = None  # Extracted from execute_method() signature
    accepts_context_param: bool = True  # Whether execute_method accepts context parameter
    _owner: 'MethodGroup | None' = None

    # Which hooks this class overrides. Computed once per class so dispatch can
    # skip the whole chain machinery when nobody wraps anything.
    _wraps_sync: bool = False
    _wraps_async: bool = False
    _wraps: bool = False
    _execs_sync: bool = False
    _execs_async: bool = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Record overridden hooks and extract context_type from the group's own hook."""
        super().__init_subclass__(**kwargs)

        cls._wraps_sync = cls.around_call is not MethodGroup.around_call
        cls._wraps_async = cls.around_call_async is not MethodGroup.around_call_async
        cls._wraps = cls._wraps_sync or cls._wraps_async
        cls._execs_sync = cls.execute_method is not MethodGroup.execute_method
        cls._execs_async = cls.execute_method_async is not MethodGroup.execute_method_async

        hook: Any
        own_execute_method = 'execute_method' in cls.__dict__
        if own_execute_method:
            hook = cls.execute_method
        elif 'around_call' in cls.__dict__:
            hook = cls.around_call
        elif 'around_call_async' in cls.__dict__:
            hook = cls.around_call_async
        else:
            # No hook of its own: inherit context_type / accepts_context_param.
            return

        try:
            sig = inspect.signature(hook)

            if 'context' in sig.parameters:
                # The group is handed a context - annotate it to have the type checked.
                # This allows: def around_call(self, call, context: AdminContext, call_next)
                # and:         def execute_method(self, method, params, context: AdminContext)
                # Without an annotation there is nothing to validate against.
                cls.accepts_context_param = True
                hints = get_type_hints(hook)
                cls.context_type = hints.get('context')
            elif own_execute_method:
                # execute_method overridden but no context parameter: this group
                # cannot pass context to its methods at all.
                cls.accepts_context_param = False
                cls.context_type = None
            else:
                # around_call sits above the executor and cannot stop the context
                # from reaching the method, so it says nothing about that.
                cls.context_type = None

        except Exception as e:
            raise TypeError(f'Failed to infer context_type for {cls.__name__}: {e}') from e

    def __init__(self) -> None:
        """Initialize method group (name set during registration)."""
        self._name: str | None = None  # Internal, set by parent
        self._methods: dict[str, Method] = {}
        self._subgroups: dict[str, MethodGroup] = {}
        self._route_cache: dict[str, tuple[list[MethodGroup], Method, list[MethodGroup] | None]] = {}

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
            TypeError: If target is a class, an abstract Method, or an invalid type

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
            _check_not_abstract(target)
            _check_unowned(target)

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
            _check_unowned(target)

            # A group cannot be mounted inside itself or inside anything it
            # already contains. _check_unowned() does not catch it: the outermost
            # group of a tree has no owner, so registering it into one of its own
            # descendants looked like registering a fresh group. The result was a
            # cycle in the _owner chain, and _ancestors() walks that chain with no
            # guard - so the next register() anywhere in the tree hung the process
            # at import time, silently and forever.
            if target is self or target in self._ancestors():
                raise ValueError(
                    f'Cannot register {_group_label(target)} into '
                    f'{_group_label(self)}: it already contains it, and a group '
                    f'cannot be its own ancestor.'
                )

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
            _check_hook_pairs(target, ancestors=[*self._ancestors(), self, target])
            target._name = name
            target._owner = self
            self._subgroups[name] = target
            _invalidate_routes(self)
            if getattr(self, 'rpc', None) is not None:
                try:
                    target._mount(self.rpc)
                except Exception:
                    # _mount validates before it attaches anything, so the
                    # subtree is untouched here and the rollback is complete.
                    del self._subgroups[name]
                    target._owner = None
                    target._name = None
                    _invalidate_routes(self)
                    raise

        elif isinstance(target, Method):
            # Method instance
            _check_method_hooks(target, [*self._ancestors(), self])
            target._owner = self
            self._methods[name] = target
            _invalidate_routes(self)
            if getattr(self, 'rpc', None) is not None:
                try:
                    _check_method_context_type(target, self.rpc)
                except Exception:
                    del self._methods[name]
                    target._owner = None
                    _invalidate_routes(self)
                    raise
                target.rpc = self.rpc

        else:
            raise TypeError(f'Expected Method or MethodGroup instance, got {type(target).__name__}')

    def unregister(self, name: str) -> None:
        """Unregister a method or subgroup by name.

        Clears ownership so the instance can be registered again.

        Args:
            name: Method or subgroup name (not a path)

        Raises:
            KeyError: If name not found in either methods or subgroups
        """
        if name in self._methods:
            method = self._methods.pop(name)
            method._owner = None
            if hasattr(method, 'rpc'):
                del method.rpc
        elif name in self._subgroups:
            subgroup = self._subgroups.pop(name)
            subgroup._owner = None
            subgroup._name = None
            subgroup._clear_rpc()
        else:
            raise KeyError(f"'{name}' not found in group '{self._name}'")
        _invalidate_routes(self)

    def _ancestors(self) -> list['MethodGroup']:
        """Groups from the outermost ancestor down to self (self included).

        register() refuses to create a cycle in the _owner chain, so the `seen`
        guard should never fire. It is here because the failure it prevents is a
        silent infinite loop rather than an exception - and because this walk is
        what register() itself calls, so a cycle formed by any future path would
        take the guard down with it. Registration is boot-time; the set costs
        nothing that matters.
        """
        chain = [self]
        seen = {id(self)}
        node = self._owner
        while node is not None and id(node) not in seen:
            chain.append(node)
            seen.add(id(node))
            node = node._owner
        chain.reverse()
        return chain

    def resolve_path(self, path: str) -> tuple['MethodGroup', Method]:
        """Resolve method path to (group, method) tuple.

        Args:
            path: Dot-separated path (e.g., "add", "user.add", "sudo.user.addgroup")

        Returns:
            Tuple of (final_group, method)

        Raises:
            MethodNotFoundError: If path not found (method or subgroup missing)

        Examples:
            "add" → (self, self._methods["add"])
            "user.add" → (user_group, user_group._methods["add"])
            "sudo.user.add" → (user_group, user_group._methods["add"])
        """
        chain, method = self._resolve_chain(path)
        return (chain[-1], method)

    def _resolve_chain(self, path: str) -> tuple[list['MethodGroup'], Method]:
        """Resolve a path to every group along it (outermost first) plus the method.

        Unlike resolve_path() this keeps the ancestors, which is what middleware
        needs: a guard mounted above a subgroup has to see calls into it.
        """
        from .errors import MethodNotFoundError

        parts = path.split('.')

        if len(parts) == 1:
            method = self._methods.get(path)
            if method is None:
                logger.debug('No method %r in group %r', path, self._name)
                raise MethodNotFoundError(f"Method '{clip(path)}' not found")
            return ([self], method)

        chain = [self]
        group = self

        for i in range(len(parts) - 1):
            part = parts[i]
            subgroup = group._subgroups.get(part)
            if subgroup is None:
                # The caller learns only that the path they asked for does not
                # exist. Which level it broke at, and what the groups are called,
                # is the server's internal shape - it goes to the log.
                logger.debug('No subgroup %r in group %r while resolving %r', part, group._name, path)
                raise MethodNotFoundError(f"Method '{clip(path)}' not found")
            chain.append(subgroup)
            group = subgroup

        leaf = parts[-1]
        method = group._methods.get(leaf)
        if method is None:
            logger.debug('No method %r in group %r while resolving %r', leaf, group._name, path)
            raise MethodNotFoundError(f"Method '{clip(path)}' not found")

        return (chain, method)

    def _route(self, path: str) -> tuple[list['MethodGroup'], Method, list['MethodGroup'] | None]:
        """Resolve a path to (chain, method, wrapping_groups), memoized per path.

        Only successful resolutions are cached, so an unknown-method flood cannot
        grow the cache. The whole cache is dropped whenever the registry changes.
        """
        entry = self._route_cache.get(path)
        if entry is not None:
            return entry

        chain, method = self._resolve_chain(path)
        wrappers = [g for g in chain if g._wraps] or None
        entry = (chain, method, wrappers)
        self._route_cache[path] = entry
        return entry

    def dispatch(
        self,
        path: str,
        params: list[Any] | dict[str, Any] | None,
        id: str | int | None,
        validate_result: bool = False,
        context: Any = None,
    ) -> Any:
        """Dispatch method call synchronously (with middleware support).

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
            RuntimeError: If the method or any middleware on the path is async
        """
        chain, method, wrappers = self._route(path)

        validated_params = validate_params(params, method.params_type)

        if method._is_async_method:
            raise _DispatchWiringError(f"Method '{path}' is async, use dispatch_async() instead")

        group = chain[-1]

        if wrappers is None:
            result = group.execute_method(method, validated_params, context=context)
        else:
            for wrapper in wrappers:
                if wrapper._wraps_async and not wrapper._wraps_sync:
                    raise _DispatchWiringError(
                        f'Group {_group_label(wrapper)} overrides around_call_async() only, '
                        f'use dispatch_async() instead'
                    )

            call = CallInfo(path=path, method=method, params=validated_params, id=id)

            def terminal(ctx: Any) -> Any:
                return group.execute_method(method, validated_params, context=ctx)

            result = _run_chain(wrappers, call, context, terminal)

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

        Args:
            path: Method path
            params: Method parameters
            id: Request ID
            validate_result: Whether to validate result
            context: Optional context object

        Returns:
            Method result
        """
        chain, method, wrappers = self._route(path)

        validated_params = validate_params(params, method.params_type)

        group = chain[-1]
        is_async = method._is_async_method

        if wrappers is None:
            if is_async:
                result = await group.execute_method_async(method, validated_params, context=context)
            else:
                result = group.execute_method(method, validated_params, context=context)
        elif not is_async and not any(w._wraps_async for w in wrappers):
            # Nothing on this path is async: run the synchronous chain, so a
            # sync-only guard still covers the call under handle_async().
            call = CallInfo(path=path, method=method, params=validated_params, id=id)

            def terminal(ctx: Any) -> Any:
                return group.execute_method(method, validated_params, context=ctx)

            result = _run_chain(wrappers, call, context, terminal)
        else:
            for wrapper in wrappers:
                if wrapper._wraps_sync and not wrapper._wraps_async:
                    raise _DispatchWiringError(
                        f'Group {_group_label(wrapper)} overrides around_call() but not around_call_async(), '
                        f'and this call is asynchronous. Override around_call_async() so the wrapper also '
                        f'covers async methods.'
                    )

            call = CallInfo(path=path, method=method, params=validated_params, id=id)

            if is_async:

                async def terminal_async(ctx: Any) -> Any:
                    return await group.execute_method_async(method, validated_params, context=ctx)
            else:

                async def terminal_async(ctx: Any) -> Any:
                    return group.execute_method(method, validated_params, context=ctx)

            result = await _run_chain_async(wrappers, call, context, terminal_async)

        # Validate result if requested
        if validate_result and method.result_type is not None:
            validate_result_type(result, method.result_type)

        return result

    def around_call(self, call: CallInfo, context: Any, call_next: Callable[[Any], Any]) -> Any:
        """Wrap a call (override for middleware that must cover nested groups).

        Called for EVERY group on the resolved path, outermost first. `call_next`
        continues down the chain; the innermost one runs the owning group's
        execute_method(). Not calling it vetoes the call.

        Unlike execute_method(), which only ever runs on the group that owns the
        method, this hook fires for ancestors too - so a guard mounted above a
        namespace actually covers the namespace.

        Args:
            call: Path, method instance and validated params
            context: Context object for this call
            call_next: Continue the chain; pass the context the rest should see

        Returns:
            Method result (possibly post-processed)

        Examples:
            class RequireAuthGroup(MethodGroup):
                def around_call(self, call, context, call_next):
                    if context.user_id is None:
                        raise InvalidParamsError('Authentication required')
                    return call_next(context)

            class TenantGroup(MethodGroup):
                def around_call(self, call, context, call_next):
                    return call_next(replace(context, tenant=lookup(context)))
        """
        return call_next(context)

    async def around_call_async(self, call: CallInfo, context: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        """Async variant of around_call() (override for async middleware).

        A group that overrides around_call() but not this hook is rejected at
        registration time when an async method is mounted below it, because a
        synchronous wrapper cannot await the rest of the chain: it would run its
        post-processing on an unfinished coroutine.

        Args:
            call: Path, method instance and validated params
            context: Context object for this call
            call_next: Continue the chain; must be awaited

        Returns:
            Method result (possibly post-processed)
        """
        return await call_next(context)

    def execute_method(self, method: Method, params: Any, context: Any = None) -> Any:
        """Execute method synchronously (override to wrap the owning group's calls).

        This hook runs on the group that owns the method and nowhere else. To
        wrap calls into nested subgroups, override around_call() instead.

        Whatever you raise here reaches the caller through the same rules as a
        method's own exception: a `JSONRPCError` subclass keeps its code and its
        message, anything else becomes a bare `-32603 Internal error` with a
        traceback in the log. So an authorization refusal has to be the former,
        or every unauthorized attempt is indistinguishable from a server fault in
        both the response and the log.

        Examples:
            class Forbidden(JSONRPCError):
                code = -32010
                message = 'Forbidden'

            class SudoGroup(MethodGroup):
                def execute_method(self, method, params, context):
                    if not self._check_sudo_rights():
                        raise Forbidden('Sudo required')
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
        """Execute method asynchronously (override for async middleware).

        Like execute_method(), this runs only on the owning group.

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

    def _mount(self, rpc: 'JSONRPC') -> None:
        """Validate the subtree against `rpc`, then attach it.

        Two passes on purpose. Validation walks the tree without touching it, so
        a subtree that fails leaves nothing behind: the caller's rollback only
        has to undo its own bookkeeping, and the instance can be registered
        somewhere else afterwards. Doing both in one pass meant a group could
        end up attached to an RPC that had rejected it, and then be
        unregisterable and unmountable for the rest of the process.

        The walk starts with whatever the groups *above* this one already
        established, because a subtree mounted into a live parent is validated
        from itself downwards and would otherwise never see them.
        """
        inherited_sync, inherited_async = _inherited_wrappers(self._owner)
        self._validate_tree(rpc, inherited_sync, inherited_async)
        self._inject_rpc(rpc)

    def _validate_tree(
        self,
        rpc: 'JSONRPC',
        _sync_wrapper: 'MethodGroup | None' = None,
        _async_wrapper: 'MethodGroup | None' = None,
    ) -> None:
        """Check the subtree against `rpc`, mutating nothing.

        Mount time is the first moment the whole ancestor chain of every method
        is known, so the checks that need it live here rather than in register().

        Args:
            rpc: JSONRPC instance the subtree is being mounted on
            _sync_wrapper: Nearest ancestor that wraps calls synchronously only
            _async_wrapper: Nearest ancestor that wraps calls asynchronously only
        """
        if (self._execs_sync or self._execs_async) and self._subgroups and not self._methods:
            raise TypeError(
                f'{_group_label(self)} overrides execute_method() but owns no methods of its own, '
                f'only subgroups. That hook runs on the group a method is registered on and '
                f'nowhere else, so it would never run and the calls below it would go unwrapped. '
                f'Move the logic to around_call()/around_call_async(), which run for every group '
                f'on the path.'
            )

        if _sync_wrapper is None and self._wraps_sync and not self._wraps_async:
            _sync_wrapper = self
        if _async_wrapper is None and self._wraps_async and not self._wraps_sync:
            _async_wrapper = self

        if _sync_wrapper is not None and _async_wrapper is not None:
            raise TypeError(
                f'Groups {_group_label(_sync_wrapper)} and {_group_label(_async_wrapper)} are on the '
                f'same path, but one wraps calls only synchronously and the other only '
                f'asynchronously. No entry point can run that chain without skipping one of them, '
                f'so both dispatch() and dispatch_async() would refuse every call below here. '
                f'Give both groups both hooks.'
            )

        for name, method in self._methods.items():
            if _sync_wrapper is not None and method._is_async_method:
                raise TypeError(
                    f"Cannot mount async method '{method.__class__.__name__}' (as '{name}'): group "
                    f'{_group_label(_sync_wrapper)} on its path overrides around_call() but not '
                    f'around_call_async(), so the wrapper would be skipped for it. '
                    f'Override around_call_async() on that group.'
                )
            _check_method_context_type(method, rpc)

        for subgroup in self._subgroups.values():
            subgroup._validate_tree(rpc, _sync_wrapper, _async_wrapper)

    def _inject_rpc(self, rpc: 'JSONRPC') -> None:
        """Attach the subtree to `rpc`, checking nothing.

        Only ever called after _validate_tree() has passed, so this cannot fail
        partway and leave half the tree attached.

        Args:
            rpc: JSONRPC instance
        """
        self.rpc = rpc

        for method in self._methods.values():
            method.rpc = rpc

        for subgroup in self._subgroups.values():
            subgroup._inject_rpc(rpc)

    def _clear_rpc(self) -> None:
        """Clear RPC reference from group and all children (recursive)."""
        if hasattr(self, 'rpc'):
            del self.rpc

        for method in self._methods.values():
            if hasattr(method, 'rpc'):
                del method.rpc

        for subgroup in self._subgroups.values():
            subgroup._clear_rpc()


def _run_chain(
    wrappers: list[MethodGroup],
    call: CallInfo,
    context: Any,
    terminal: Callable[[Any], Any],
) -> Any:
    """Compose around_call() of every wrapping group, outermost first."""
    call_next = terminal
    for group in reversed(wrappers):
        call_next = _bind(group, call, call_next)
    return call_next(context)


def _bind(group: MethodGroup, call: CallInfo, call_next: Callable[[Any], Any]) -> Callable[[Any], Any]:
    def step(context: Any) -> Any:
        return group.around_call(call, context, call_next)

    return step


async def _run_chain_async(
    wrappers: list[MethodGroup],
    call: CallInfo,
    context: Any,
    terminal: Callable[[Any], Awaitable[Any]],
) -> Any:
    """Compose around_call_async() of every wrapping group, outermost first."""
    call_next = terminal
    for group in reversed(wrappers):
        call_next = _bind_async(group, call, call_next)
    return await call_next(context)


def _bind_async(
    group: MethodGroup,
    call: CallInfo,
    call_next: Callable[[Any], Awaitable[Any]],
) -> Callable[[Any], Awaitable[Any]]:
    async def step(context: Any) -> Any:
        return await group.around_call_async(call, context, call_next)

    return step


def _group_label(group: MethodGroup) -> str:
    """Human-readable identification of a group for error messages."""
    if group._name is None:
        return type(group).__name__
    return f"{type(group).__name__} (mounted as '{group._name}')"


def _check_not_abstract(target: Method) -> None:
    """Reject a Method subclass that never implemented execute()."""
    if type(target).execute is Method.execute:
        raise TypeError(
            f'{type(target).__name__} does not implement execute() and cannot be registered '
            f'(abstract base classes may be inherited, not mounted)'
        )


def _check_unowned(target: Method | MethodGroup) -> None:
    """Reject an instance that is already mounted somewhere.

    Ownership is recorded at registration, not at RPC injection, so registering
    into a group that is not attached to a JSONRPC yet is covered too.
    """
    if target._owner is not None or hasattr(target, 'rpc'):
        kind = 'Method' if isinstance(target, Method) else 'MethodGroup'
        raise ValueError(
            f"{kind} instance '{target.__class__.__name__}' is already registered. "
            f'Create a new instance for each registration.'
        )


def _check_method_hooks(method: Method, ancestors: list[MethodGroup]) -> None:
    """Reject a method whose async-ness routes around a half-overridden hook pair."""
    owner = ancestors[-1]

    if method._is_async_method and owner._execs_sync and not owner._execs_async:
        raise TypeError(
            f'Cannot register async {method.__class__.__name__} into '
            f'{type(owner).__name__}: the group overrides execute_method() but not '
            f'execute_method_async(), so async calls would skip it. Override '
            f'execute_method_async(), or move the logic to around_call()/around_call_async().'
        )
    if not method._is_async_method and owner._execs_async and not owner._execs_sync:
        raise TypeError(
            f'Cannot register synchronous {method.__class__.__name__} into '
            f'{type(owner).__name__}: the group overrides execute_method_async() but not '
            f'execute_method(), so synchronous calls would skip it. Override execute_method().'
        )

    if method._is_async_method:
        for group in ancestors:
            if group._wraps_sync and not group._wraps_async:
                raise TypeError(
                    f'Cannot register async {method.__class__.__name__}: group {_group_label(group)} '
                    f'on its path overrides around_call() but not around_call_async(), so the wrapper '
                    f'would be skipped for it. Override around_call_async() on that group.'
                )


def _inherited_wrappers(owner: 'MethodGroup | None') -> tuple['MethodGroup | None', 'MethodGroup | None']:
    """The one-sided wrappers already standing above a subtree about to mount.

    A tree assembled bottom-up and then handed to register() is validated from
    its root, so every group sees the ones above it. A subtree registered into a
    group that is *already* mounted is validated from itself, and used to begin
    as though nothing were above it - so the same two groups on the same path
    were refused in the first case and accepted in the second, where every call
    below them then failed with -32603 because no entry point can run that chain.

    Args:
        owner: Group the subtree is being attached to, or None for the root

    Returns:
        (nearest sync-only wrapper, nearest async-only wrapper) among the
        ancestors, matching what a top-down walk would have carried down: the
        outermost of each kind, since that is the one such a walk records first.
    """
    sync_wrapper: MethodGroup | None = None
    async_wrapper: MethodGroup | None = None

    chain: list[MethodGroup] = []
    node = owner
    while node is not None:
        chain.append(node)
        node = node._owner

    for group in reversed(chain):  # outermost first
        if sync_wrapper is None and group._wraps_sync and not group._wraps_async:
            sync_wrapper = group
        if async_wrapper is None and group._wraps_async and not group._wraps_sync:
            async_wrapper = group

    return sync_wrapper, async_wrapper


def _check_hook_pairs(target: MethodGroup, ancestors: list[MethodGroup]) -> None:
    """Run the hook-pair checks for every method in a subtree being mounted."""
    for name, method in target._methods.items():
        try:
            _check_method_hooks(method, ancestors)
        except TypeError as e:
            raise TypeError(f"{e} (method registered as '{name}')") from None

    for subgroup in target._subgroups.values():
        _check_hook_pairs(subgroup, [*ancestors, subgroup])


def _check_method_context_type(method: Method, rpc: 'JSONRPC') -> None:
    """Validate a method's context_type against the RPC it is being mounted on.

    MethodGroup.register() can only compare against the group's own context_type,
    which is None for every plain group. This is the check that actually covers
    a method nested anywhere in the tree.
    """
    if not method.accepts_context:
        return
    if method.context_type is None or rpc.context_type is None:
        return
    if not issubclass(method.context_type, rpc.context_type):
        raise TypeError(
            f'Cannot register {method.__class__.__name__}: '
            f'method context_type {method.context_type.__name__} must be '
            f'subclass of RPC context_type {rpc.context_type.__name__}'
        )
