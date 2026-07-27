# Philosophy & Design

python-jsonrpc-lib is built around one idea: the protocol is small, the code should match. You define types, the library validates. You write methods, the library routes. You annotate, the library documents.

---

## Core Principles

### 1. Type Safety

Parameters are declared as dataclasses. Every incoming request is validated against those types before your code runs.

Return types are checked too, but on different terms: it is opt-in (`validate_results=True`) and it runs **after** `execute()` returns. It is there to catch an implementation drifting from its declared type, and it reports that violation rather than preventing it — a method that changed state has already changed it by the time the check fires.

That asymmetry is deliberate, and it is why the two have different defaults. Params validation defends the server from the caller, so it is always on and there is no flag to turn it off. Result validation defends you from yourself, so it is off by default and belongs in development and in your test suite, where a failure costs nothing and the annotation it checks is the same one your OpenAPI spec publishes. In production it costs about twice the response and tells you only what a test could have told you earlier.

```python title="type_safety.py"
from dataclasses import dataclass
from jsonrpc import Method

@dataclass
class UserParams:
    email: str
    age: int
    premium: bool = False

@dataclass
class UserResult:
    user_id: int
    email: str

class CreateUser(Method):
    def execute(self, params: UserParams) -> UserResult:
        # params is fully validated and typed before reaching here
        return UserResult(user_id=123, email=params.email)
```

One dataclass definition covers validation, IDE autocomplete, mypy support, and OpenAPI schema — all at once.

---

### 2. Transport-Agnostic

`rpc.handle()` takes a JSON string, returns a JSON string — or `None`, for a notification the spec says must not be answered. It knows nothing about HTTP, sockets, or frameworks. The transport is entirely your concern, including deciding what "no response" looks like on it.

```python title="transport_agnostic.py"
from fastapi import Response
from jsonrpc import JSONRPC

rpc = JSONRPC(version='2.0')
# ... register methods ...

# Flask
def flask_rpc(request):
    response = rpc.handle(request.data)
    if response is None:                       # a notification has no answer
        return '', 204
    return response, 200, {'Content-Type': 'application/json'}

# FastAPI
async def fastapi_rpc(request):
    response = await rpc.handle_async(await request.body())
    if response is None:
        return Response(status_code=204)
    return Response(content=response, media_type='application/json')

# TCP socket
def socket_rpc(connection):
    response = rpc.handle(connection.recv(4096).decode())
    if response is not None:
        connection.send(response.encode())
```

Same `rpc` object works everywhere. Tests don't need HTTP mocking — just call `rpc.handle()` directly.

---

### 3. Progressive API

Three API levels exist so you can start simple and grow without rewriting. Each level builds on the previous one.

```python title="progressive_api.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method, MethodGroup

rpc = JSONRPC(version='2.0')

# Level 1: Decorator — for prototyping and simple scripts
@rpc.method
def quick_add(x: int, y: int) -> int:
    return x + y

# Level 2: Method class — for production code with clear structure
@dataclass
class MulParams:
    x: int
    factor: int = 2

class Multiply(Method):
    def execute(self, params: MulParams) -> int:
        return params.x * params.factor

rpc.register('multiply', Multiply())

# Level 3: MethodGroup — for namespacing, routing, and middleware
@dataclass
class UserParams:
    name: str

@dataclass
class UserResult:
    user_id: int
    name: str

class CreateUser(Method):
    def execute(self, params: UserParams) -> UserResult:
        return UserResult(user_id=1, name=params.name)

class DeleteUser(Method):
    def execute(self, params: UserParams) -> str:
        return f'deleted {params.name}'

users = MethodGroup()
users.register('create', CreateUser())
users.register('delete', DeleteUser())

admin = MethodGroup()
admin.register('users', users)

rpc.register('admin', admin)
# Method path: admin.users.create
```

**When to use each:**
- **Decorator**: Quick prototypes, simple APIs, scripts
- **Method classes**: Production code, testable units, context
- **Groups**: Large APIs, namespacing, middleware chains

---

### 4. Built-in OpenAPI Generation

!!! success "Unique Feature"
    Most JSON-RPC libraries have no documentation generation — you write schemas by hand.

python-jsonrpc-lib reads your type hints and docstrings and produces a complete OpenAPI 3.0 specification. Nothing extra to write.

```python title="openapi_generation.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC
from jsonrpc.openapi import OpenAPIGenerator

rpc = JSONRPC(version='2.0')

@dataclass
class SearchParams:
    query: str
    limit: int = 10

@dataclass
class SearchResult:
    id: int
    title: str

class Search(Method):
    def execute(self, params: SearchParams) -> list[SearchResult]:
        """Search items by query."""
        return []

rpc.register('search', Search())

generator = OpenAPIGenerator(rpc, title='My API', version='1.0.0')
spec = generator.generate()

# spec is a complete OpenAPI 3.0 dict:
# - Method descriptions from docstrings
# - Parameter types from dataclass fields
# - Required vs optional from default values
# - Nested schemas from nested dataclasses
```

Serve `spec` from `/openapi.json`, add RapiDoc or Swagger UI, and the API documents itself. The schema updates automatically when types change.

→ [Full OpenAPI tutorial](tutorial/07-openapi.md)

---

### 5. Fail-Fast Validation

Errors are caught as early as possible — ideally at class definition time, before anything is registered or called.

```python title="fail_fast.py"
from dataclasses import dataclass
from jsonrpc import Method

@dataclass
class Params:
    x: int

# Invalid execute() signature — caught at class definition, not at runtime
class BadMethod(Method):
    def execute(self, params: dict) -> int:  # dict is not a dataclass
        return 1
# ^ TypeError: BadMethod.execute() params type must be a dataclass or None, got <class 'dict'>
```

This check runs when Python loads the class — before `register()`, before the first request. The error appears during development, not in production.

Context type incompatibility is caught at registration time:

```python title="context_mismatch.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class BaseContext:
    user_id: int

@dataclass
class AdminContext:
    user_id: int
    is_admin: bool

class AdminAction(Method):
    def execute(self, params: None, context: AdminContext) -> str:
        return 'done'

rpc = JSONRPC(version='2.0', context_type=BaseContext)  # only provides BaseContext
rpc.register('action', AdminAction())
# ^ TypeError: Cannot register AdminAction: method context_type AdminContext
#   must be subclass of RPC context_type BaseContext
```

---

## Key Design Decisions

### Why Dataclasses?

Dataclasses are standard library (Python 3.11+). They give you typed `__init__`, `__repr__`, field defaults, and `asdict()` serialization — without any external dependencies.

**Alternatives considered:**

- **Pydantic** — excellent library, but an external dependency. Zero-dependency was a hard requirement.
- **Plain `dict`** — no type hints, no IDE support, validation must be written manually.
- **`TypedDict`** — type hints for static analysis only; no runtime validation.

```python
from dataclasses import dataclass

@dataclass
class Params:
    x: int
    label: str = 'default'

# One definition provides:
# - Runtime validation (required fields, correct types)
# - Default values for optional fields
# - IDE autocomplete and mypy support
# - OpenAPI schema (via field types)
# - No external packages required
```

---

### Why Separate Method and MethodGroup?

**Method** holds business logic — what to compute.
**MethodGroup** holds routing concerns — how to organize and intercept.

Keeping them separate means you can add middleware (logging, rate limiting, auth) to any group without touching the methods themselves.

```python title="separation.py"
import time
from dataclasses import dataclass
from jsonrpc import Method, MethodGroup

@dataclass
class PriceParams:
    quantity: int
    unit_price: float

class CalculatePrice(Method):
    def execute(self, params: PriceParams) -> float:
        return params.quantity * params.unit_price

class LoggingGroup(MethodGroup):
    def around_call(self, call, context, call_next):
        start = time.time()
        result = call_next(context)
        print(f'{call.path} took {time.time() - start:.4f}s')
        return result

group = LoggingGroup()
group.register('calculate', CalculatePrice())
```

`CalculatePrice` knows nothing about logging. `LoggingGroup` knows nothing about pricing. Either can change independently.

---

### Why Strict Mode by Default?

Strict mode enforces the spec: v1.0 requires array params only, v2.0 accepts both object and array params (per spec). Clients that send malformed requests get a clear error instead of silently getting an unexpected result.

```python title="strict_mode.py"
# v1.0 strict (default): array params only, no batch, no dict params
rpc_v1 = JSONRPC(version='1.0')
# valid:   {"method": "add", "params": [1, 2], "id": 1}
# invalid: {"method": "add", "params": {"a": 1}, "id": 1}

# v2.0 (default): both object and array params, batch allowed
rpc_v2 = JSONRPC(version='2.0')
# valid: {"jsonrpc": "2.0", "method": "add", "params": {"a": 1}, "id": 1}
# valid: {"jsonrpc": "2.0", "method": "add", "params": [1, 2], "id": 1}

# Restrictive mode (explicit opt-in to disallow array params)
rpc = JSONRPC(version='2.0', allow_list_params=False)
```

Restrictive mode is always available for cases where you want to enforce object-only params.

---

## Trade-offs & Non-Goals

### What python-jsonrpc-lib IS NOT

- **Not a web framework** — Use Flask/FastAPI for HTTP
- **Not a client library** — Server-side only
- **Not the fastest** — Optimized for correctness, not raw throughput
- **Not backwards compatible** — Requires Python 3.11+ for modern type system features

### Intentional Limitations

- **Decorator API is v2.0 only** — v1.0 is legacy; use Method classes for v1.0
- **No monkey-patching** — Clear, explicit code only
- **No "magic"** — Everything is explicit and traceable

---

## What's Next?

- [Tutorial: Hello World](tutorial/01-hello-world.md) - Build your first RPC server
- [Tutorial: OpenAPI Generation](tutorial/07-openapi.md) - Explore the OpenAPI feature
- [Integrations](integrations/flask.md) - Use with Flask and FastAPI
