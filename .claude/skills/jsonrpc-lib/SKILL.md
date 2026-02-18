---
name: jsonrpc-lib
description: Use when writing code that uses the jsonrpc-lib library — creating RPC methods, registering them, organizing with groups, handling errors, or adding context and middleware.
user-invocable: false
---

# jsonrpc-lib usage guide

## Always use Method classes

**Never use the `@rpc.method` decorator** unless explicitly prototyping. It has no context, no middleware, no MethodGroup support, and is unsuitable for production. Default to `Method` subclasses:

```python
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class AddParams:
    a: int
    b: int

@dataclass
class AddResult:
    sum: int

class Add(Method):
    def execute(self, params: AddParams) -> AddResult:
        return AddResult(sum=params.a + params.b)

rpc = JSONRPC(version='2.0')
rpc.register('add', Add())

response = rpc.handle('{"jsonrpc":"2.0","method":"add","params":{"a":1,"b":2},"id":1}')
# '{"jsonrpc": "2.0", "result": {"sum": 3}, "id": 1}'
```

## execute() rules

- `params` type **must be a dataclass** — never `dict`, `list`, or `TypedDict`
- `params` is always the second parameter and must be named `params` exactly
- Return type annotation is **required**
- For structured return values, **prefer a dataclass** over returning a raw dict
- Optional third parameter `context: ContextType` for per-request data

```python
# Wrong — TypeError at class definition time
class Bad(Method):
    def execute(self, params: dict) -> dict:
        return {"result": params["x"]}

# Wrong — no return type
class AlsoBad(Method):
    def execute(self, params: SomeParams):
        return 42

# Correct
@dataclass
class SomeParams:
    x: int

@dataclass
class SomeResult:
    value: int
    label: str

class Good(Method):
    def execute(self, params: SomeParams) -> SomeResult:
        return SomeResult(value=params.x * 2, label="doubled")
```

## MethodGroup — namespacing and middleware

`MethodGroup()` takes no arguments. Name is set during registration.
Always pass an **instance**, not a class.

```python
from jsonrpc import JSONRPC, Method, MethodGroup

math = MethodGroup()
math.register('add', Add())      # instance, not Add
math.register('subtract', Sub())

rpc = JSONRPC(version='2.0')
rpc.register('math', math)
# Available: "math.add", "math.subtract"

# Nested groups
admin = MethodGroup()
users = MethodGroup()
users.register('create', CreateUser())
admin.register('users', users)
rpc.register('admin', admin)
# Available: "admin.users.create"
```

## Context — per-request data

```python
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method, JSONRPCError

@dataclass
class AuthContext:
    user_id: int
    is_admin: bool

@dataclass
class DeleteParams:
    resource_id: int

class DeleteResource(Method):
    def execute(self, params: DeleteParams, context: AuthContext) -> str:
        if not context.is_admin:
            raise JSONRPCError("Forbidden", code=-32000)
        return f"deleted {params.resource_id}"

rpc = JSONRPC(version='2.0', context_type=AuthContext)
rpc.register('delete', DeleteResource())

ctx = AuthContext(user_id=42, is_admin=True)
response = rpc.handle(json_string, context=ctx)
```

## Middleware via MethodGroup

Override `execute_method()` to add cross-cutting behavior:

```python
import time
from jsonrpc import MethodGroup

class TimingGroup(MethodGroup):
    def execute_method(self, method, params, context=None):
        start = time.time()
        result = super().execute_method(method, params, context)
        print(f"{method.__class__.__name__}: {time.time() - start:.4f}s")
        return result

group = TimingGroup()
group.register('add', Add())
```

## Async

```python
class AsyncFetch(Method):
    async def execute(self, params: FetchParams) -> FetchResult:
        data = await some_async_call(params.url)
        return FetchResult(data=data)

response = await rpc.handle_async(json_string)
response = await rpc.handle_async(json_string, context=ctx)
```

## Errors

Raise from inside `execute()` to return a JSON-RPC error response:

```python
from jsonrpc import JSONRPCError, ServerError

raise JSONRPCError("Something failed")            # -32603 InternalError
raise ServerError("Rate limit exceeded", code=-32029)  # implementation-defined
```

Error classes and their codes:

| Code | Class |
|------|-------|
| -32700 | `ParseError` |
| -32600 | `InvalidRequestError` |
| -32601 | `MethodNotFoundError` |
| -32602 | `InvalidParamsError` |
| -32603 | `InternalError` |
| -32001 | `InvalidResultError` |
| -32000 to -32099 | `ServerError` |

## OpenAPI generation

```python
from jsonrpc import JSONRPC, OpenAPIGenerator

generator = OpenAPIGenerator(rpc, title='My API', version='1.0.0')
spec = generator.generate()  # OpenAPI 3.0 dict
```

Docstrings on `execute()` become method descriptions. Dataclass fields become parameters. Optional fields (with defaults) are marked not-required automatically.

## Common mistakes

| Wrong | Right |
|-------|-------|
| `group.register(MyMethod)` | `group.register('name', MyMethod())` |
| `MethodGroup(prefix='math')` | `MethodGroup()` — no arguments |
| `params: dict` or `params: list` | `params: MyDataclass` |
| No return type annotation | `-> MyResult:` required |
| `rpc.handle(data_dict)` | `rpc.handle(json_string)` — JSON string only |
| `def execute(self, data: Params)` | `def execute(self, params: Params)` — must be `params` |
