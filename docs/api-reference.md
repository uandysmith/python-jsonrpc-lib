# API Reference

## Installation

```bash
pip install python-jsonrpc-lib
```

## Main Classes

### `JSONRPC`

```python
from jsonrpc import JSONRPC
```

**Constructor:**

```python
JSONRPC(
    version: str = '2.0',
    validate_results: bool = False,
    context_type: type | None = None,
    allow_batch: bool | None = None,
    allow_dict_params: bool | None = None,
    allow_list_params: bool | None = None,
    max_batch: int = 100,
    max_request_size: int = 1024 * 1024,
    max_batch_size: int = -1,
    max_concurrent: int | None = None,
    expose_internal_errors: bool = False,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `version` | `'1.0'` \| `'2.0'` | `'2.0'` | JSON-RPC protocol version |
| `validate_results` | `bool` | `False` | Check return values against annotations, result dataclass fields included. On in development, off in production — [see below](#checking-return-values-validate_results) |
| `context_type` | `type \| None` | `None` | Expected context type for validation |
| `allow_batch` | `bool \| None` | `None` | Batch requests (default: spec-compliant) |
| `allow_dict_params` | `bool \| None` | `None` | Object params (default: spec-compliant) |
| `allow_list_params` | `bool \| None` | `None` | Array params (default: spec-compliant) |
| `max_batch` | `int` | `100` | Max requests per batch (`-1` = unlimited) |
| `max_request_size` | `int` | `1048576` | Largest body accepted, refused before it is parsed (`-1` = unlimited) |
| `max_batch_size` | `int` | `-1` | Same, applied only when the body is a batch (`-1` = only `max_request_size` applies) |
| `max_concurrent` | `int \| None` | `None` | Max concurrent coroutines in async batch (`None` = `64`, `-1` = unlimited) |
| `expose_internal_errors` | `bool` | `False` | Send the caught exception's text in the `-32603` message. Off by default: the caller gets a bare `Internal error` and the full exception goes to the log |

**Default params mode (spec-compliant):**

| Version | `allow_batch` | `allow_dict_params` | `allow_list_params` |
|---------|---------------|---------------------|---------------------|
| v1.0 | `False` | `False` | `True` |
| v2.0 | `True` | `True` | `True` |

**Methods:**

```python
# Handle JSON-RPC request (sync). Returns None for a notification.
# `bytes` must be UTF-8; other encodings are a parse error.
rpc.handle(raw_data: str | bytes, context: Any = None) -> str | None

# Handle JSON-RPC request (async)
await rpc.handle_async(raw_data: str | bytes, context: Any = None) -> str | None

# Direct method call (sync, no JSON)
rpc.call_method(
    method: str,
    params: dict | list | None = None,
    context: Any = None,
    validate_result: bool = False,
) -> Any

# Direct method call (async, no JSON)
await rpc.call_method_async(
    method: str,
    params: dict | list | None = None,
    context: Any = None,
    validate_result: bool = False,
) -> Any

# Register method or group
rpc.register(name: str | None, target: Method | MethodGroup) -> None

# Unregister method or group by name or dotted path
rpc.unregister(path: str) -> None

# Decorator for rapid prototyping (v2.0 only)
@rpc.method
@rpc.method("custom_name")
```

**Overridable hooks:**

```python
# Deserialize incoming JSON bytes/string to a Python object.
# Called once per request, before routing or validation.
def deserialize(self, data: str | bytes) -> Any: ...

# Serialize a response dict (or list of dicts for batch) to a JSON string.
def serialize(self, data: Any) -> str: ...

# Convert the method's return value to a JSON-serializable object.
# Override to handle custom types (datetime, Decimal, UUID, etc.)
# before they reach the JSON serializer.
def serialize_result(self, result: Any) -> Any: ...
```

All three hooks work together. Overriding `deserialize` + `serialize` is sufficient for a
full JSON library swap:

```python
import orjson

class FastRPC(JSONRPC):
    def deserialize(self, data):
        return orjson.loads(data)

    def serialize(self, data):
        return orjson.dumps(data).decode()
```

Override `serialize_result` to handle custom types before serialization:

```python
from datetime import datetime
from decimal import Decimal

class MyRPC(JSONRPC):
    def serialize_result(self, result):
        if isinstance(result, datetime):
            return result.isoformat()
        if isinstance(result, Decimal):
            return str(result)
        return super().serialize_result(result)
```

!!! note "parse_request / parse_response and json.loads"
    `deserialize()` is the only place `json.loads` is called in the server path.
    The module-level `parse_request()` and `parse_response()` utilities also accept
    raw JSON strings and call `json.loads` internally — but `JSONRPC` always passes
    them an already-parsed `dict`, so they never call `json.loads` during request
    handling. If you override `deserialize()`, the full server path is covered.

**Examples:**

```python
rpc = JSONRPC(version='2.0')
response = rpc.handle('{"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": 1}')

# Async
response = await rpc.handle_async(request_bytes, context=ctx)

# Direct call
result = rpc.call_method('math.add', {'a': 1, 'b': 2})
```

---

### `Method`

```python
from jsonrpc import Method
```

**Base class for all RPC methods.**

```python
class MyMethod(Method):
    def execute(self, params: ParamsType) -> ReturnType:
        ...

    # Optional: async
    async def execute(self, params: ParamsType) -> ReturnType:
        ...

    # Optional: with context
    def execute(self, params: ParamsType, context: ContextType) -> ReturnType:
        ...
```

**Signature rules:**
- `params` must be 2nd parameter (after `self`)
- `params` type hint must be a `@dataclass` or `None`
- Return type annotation is required
- Optional 3rd parameter `context` with type hint

**Inheritance:**

Type extraction runs for classes that define their own `execute()`. A class that
does not is an intermediate base — it may carry shared domain logic, and it
inherits `params_type`, `result_type`, `accepts_context` and `context_type`
through ordinary attribute lookup. Registering such a base raises `TypeError`;
only concrete subclasses can be mounted.

```python
class BillingMethod(Method):
    """Domain base: helpers shared by every billing method, no execute()."""

    def charge(self, account, amount):
        ...

class Refund(BillingMethod):
    def execute(self, params: RefundParams) -> RefundResult:
        ...
```

The template-method shape works too: a base defines `execute()` and the params
contract, subclasses override only hook methods and share the extracted types.

**Available in `execute()`:**
- `self.rpc` - access to parent JSONRPC instance for internal calls

!!! note "One instance, one mount"
    A `Method` or `MethodGroup` instance belongs to exactly one place in exactly
    one tree; registering it twice raises `ValueError`. Instances are shared
    across every request and every thread, so keep per-request state in
    `context`, never on `self`.

**Examples:**

```python
from dataclasses import dataclass
from jsonrpc import Method

@dataclass
class AddParams:
    a: int
    b: int

class Add(Method):
    def execute(self, params: AddParams) -> int:
        # Call another method internally
        return self.rpc.call_method('other.method', {'x': params.a})
```

---

### `MethodGroup`

```python
from jsonrpc import MethodGroup
```

**Container for organizing methods with a common prefix.**

```python
group = MethodGroup()

# Register method
group.register('method_name', MethodInstance())

# Register subgroup
group.register('subgroup_name', SubGroup())

# Unregister method or subgroup (unified)
group.unregister('method_name')
group.unregister('subgroup_name')
```

**Override for middleware:**

```python
# Runs for EVERY group on the resolved path, outermost first.
# This is the hook for guards, logging, caching, rate limiting.
class CustomGroup(MethodGroup):
    def around_call(self, call: CallInfo, context, call_next):
        # Before execution
        result = call_next(context)   # not calling it vetoes the call
        # After execution
        return result

    async def around_call_async(self, call: CallInfo, context, call_next):
        return await call_next(context)

# Runs ONLY on the group that owns the method. Use it to change how that
# group's own methods are invoked, not as a guard over a namespace.
class OwningGroup(MethodGroup):
    def execute_method(self, method, params, context=None):
        return super().execute_method(method, params, context)
```

`CallInfo` carries `path` (the full dotted path as requested), `method` (the
`Method` instance), `params` (already validated) and `id` (the request id, for
correlating log lines with the response the caller received — `None` for a
notification).

A group that overrides only the synchronous hook of either pair refuses to accept
an async method below it at registration time — a synchronous wrapper cannot
await the rest of the chain. Mounting a group that overrides `execute_method()`
but owns only subgroups is also refused: that hook could never run there.

See [Middleware](advanced/middleware.md) for worked examples.

---

### `OpenAPIGenerator`

```python
from jsonrpc.openapi import OpenAPIGenerator
```

**Generates OpenAPI 3.0 schema from registered methods.**

```python
generator = OpenAPIGenerator(
    rpc: JSONRPC,
    base_url: str = "/jsonrpc",
    title: str = "JSON-RPC API",
    version: str = "1.0.0",
    description: str | None = None,
    simplify_id: bool = True,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rpc` | `JSONRPC` | — | RPC instance with registered methods |
| `base_url` | `str` | `"/jsonrpc"` | Base URL path for the JSON-RPC endpoint |
| `title` | `str` | `"JSON-RPC API"` | API title in schema |
| `version` | `str` | `"1.0.0"` | API version in schema |
| `description` | `str \| None` | `None` | API description |
| `simplify_id` | `bool` | `True` | Use `{"type": "integer"}` for id fields. Set to `False` for spec-compliant `oneOf` with string and integer. |

**Methods:**

```python
# Generate OpenAPI spec as dict
spec: dict = generator.generate()

# Generate as JSON string
json_str: str = generator.generate_json(indent=2)

# Generate as YAML string (requires PyYAML)
yaml_str: str = generator.generate_yaml()

# Add security scheme
generator.add_security_scheme(
    name: str,
    scheme_type: Literal["apiKey", "http", "oauth2", "openIdConnect"],
    options: dict[str, Any] | None = None,
)

# Add global header parameter
generator.add_header(
    name: str,
    description: str,
    required: bool = False,
    schema: dict | None = None,
)

# Add global security requirement
generator.add_security_requirement(
    scheme_name: str,
    scopes: list[str] | None = None,
)
```

**Examples:**

```python
generator = OpenAPIGenerator(
    rpc,
    base_url="/rpc",
    title="My API",
    version="2.0.0",
)
generator.add_security_scheme(
    "BearerAuth",
    scheme_type="http",
    options={"scheme": "bearer", "bearerFormat": "JWT"},
)
generator.add_security_requirement("BearerAuth")
spec = generator.generate()
```

---

## Serialization hooks

Two overridable methods sit on the outbound path, and they do different jobs.

**`serialize(data, **kwargs) -> str`** turns the finished response into JSON. It
is the last step and sees the whole payload, so this is where custom types
belong:

```python title="custom_types.py"
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from jsonrpc import JSONRPC

def encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f'{type(value).__name__} is not JSON serializable')

class MyRPC(JSONRPC):
    def serialize(self, data, **kwargs):
        kwargs.setdefault('allow_nan', False)   # keep the non-finite float guard
        return json.dumps(data, default=encode, **kwargs)
```

This works for those types wherever they appear — nested in a dataclass, inside
a list, as a dict value.

!!! warning "Keep `allow_nan=False`"
    Overriding `serialize()` replaces the default that refuses `NaN` and
    `Infinity`. Without that argument they go back on the wire as bare tokens,
    which no conforming JSON parser accepts — the response becomes unreadable
    for the client rather than failing loudly for you.

**`serialize_result(result) -> Any`** is the dataclass-to-dict conversion, one
step earlier. Override it to make that walk faster; do **not** override it to
add a type. By the time `super().serialize_result()` returns, the value is a
plain dict and the custom object nested inside it has already gone past — there
is no point left to intercept it.

Both hooks cover the outbound direction only. Parameters arriving as JSON have
no equivalent: convert them in the params dataclass's `__post_init__`. See
[Parameters → Types JSON cannot express](tutorial/03-parameters.md#types-json-cannot-express).

## Error Classes

```python
from jsonrpc.errors import (
    RPCError,
    ParseError,
    InvalidRequestError,
    MethodNotFoundError,
    InvalidParamsError,
    InternalError,
    InvalidResultError,
    ServerError,
)
```

| Class | Code | When to use |
|-------|------|-------------|
| `ParseError` | `-32700` | Invalid JSON |
| `InvalidRequestError` | `-32600` | Invalid request structure |
| `MethodNotFoundError` | `-32601` | Method does not exist |
| `InvalidParamsError` | `-32602` | Parameter validation failed |
| `InternalError` | `-32603` | Internal server error |
| `InvalidResultError` | `-32001` | Return type mismatch |
| `ServerError(msg, code)` | `-32000` to `-32099` | Custom server errors |

**Usage:**

```python
from jsonrpc.errors import InvalidParamsError, ServerError

class MyMethod(Method):
    def execute(self, params: Params) -> dict:
        if not params.email:
            raise InvalidParamsError("Email is required")

        if database_down():
            raise ServerError("Database unavailable", code=-32010)

        return {"ok": True}
```

**Structured detail on `-32602`:**

Every parameter rejection fills in the spec's `data` field, so a client does not
have to parse the message to find out which argument to fix:

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Parameter 'age' expected type 'int', got 'str'",
    "data": {"reason": "type_mismatch", "parameter": "age", "expected": "int", "received": "str"}
  },
  "id": 1
}
```

`reason` is always present. The values it takes are `type_mismatch`,
`missing_parameter`, `unknown_parameter`, `too_many_positional`,
`invalid_params_container`, `no_parameters_accepted`, `no_matching_variant`,
`rejected_by_validator` (a `ValueError` from your `__post_init__`) and
`params_shape_not_allowed`. `parameter` names the argument at fault wherever one
can be identified. The rejected value is never echoed back.

An `InvalidParamsError` you raise yourself carries whatever `data` you pass it,
or none:

```python
raise InvalidParamsError("Email is required", data={"reason": "missing_email"})
```

---

## Type System

### Supported Parameter Types

| Python Type | JSON type | Notes |
|-------------|-----------|-------|
| `int` | number (integer) | No float coercion |
| `float` | number | Accepts int values too. `NaN`/`Infinity` rejected — neither exists in JSON |
| `str` | string | |
| `bool` | boolean | Must be exactly `true/false` |
| `list` | array | Untyped |
| `list[T]` | array | Each element validated as T |
| `dict` | object | Untyped |
| `dict[K, V]` | object | Keys and values validated |
| `@dataclass` | object | Recursively validated |
| `T \| None` | T or null | Optional type |
| `T \| U` | T or U | Union type (first match wins; a value matching no variant is rejected) |
| `Literal["a", "b"]` | enum string | Restricted values, type-strict (`true` does not satisfy `Literal[1]`) |
| `Any` | any | No validation |
| `None` | null or absent | No parameters |

Nesting is bounded at `MAX_NESTING_DEPTH` (64) regardless of how the recursion is
spelled. Only real dataclass fields are parameters: `ClassVar` entries and the
`KW_ONLY` sentinel are not settable from the wire.

That table is the whole list. A params field annotated with anything else —
`tuple`, `set`, `Enum`, `datetime`, `date`, `UUID`, `Decimal`, `bytes` — is
refused when the method class is defined, nested dataclasses included: JSON
cannot express those, so no value the caller could send would ever be accepted.
Take the field as what arrives on the wire and convert it in `__post_init__`,
where a `raise InvalidParamsError` becomes a `-32602` the caller can read:

```python title="wire_types.py"
from dataclasses import dataclass
from datetime import datetime
from jsonrpc.errors import InvalidParamsError

@dataclass
class BookingParams:
    starts_at: str  # ISO-8601, not datetime

    def __post_init__(self):
        try:
            self.when = datetime.fromisoformat(self.starts_at)
        except ValueError:
            raise InvalidParamsError('starts_at must be an ISO-8601 timestamp') from None
```

Raise `InvalidParamsError`, not a bare `ValueError`: a `ValueError` is still a
`-32602`, but with a fixed message, because the ones raised by `fromisoformat`,
`int`, `Decimal` and `UUID` embed the caller's own string in their text.

Going the other way, a *result* may contain any type your `serialize()` hook
knows how to encode — see [Serialization hooks](#serialization-hooks) above.

### Nested Type Example

```python
@dataclass
class Config:
    features: dict[str, bool]
    mode: Literal["dev", "prod"]
    tags: list[str] | None = None
    max_workers: int = 4
```

### Checking return values: `validate_results`

```python title="validate_results.py"
import os
from jsonrpc import JSONRPC

# On where a failure is cheap, off where throughput matters.
rpc = JSONRPC(validate_results=os.getenv('ENV') != 'production')
```

Off by default. When on, the return value is checked against the method's return
annotation — the outer type and every field of a result dataclass, including the
dataclasses inside a `list` or `dict`, because a dataclass enforces nothing at
runtime and `Row(score='high')` is ordinary Python. A mismatch is `-32001` naming
the path:

```json
{"error": {"code": -32001, "message": "Expected return type 'float' at 'rows[1].score', got 'str'"}}
```

**Turn it on in development and in your test suite.** That is where it earns its
keep: it catches a method drifting from its declared type on the first call that
does it, and the annotation is also what the OpenAPI spec publishes — so a
mismatch means your published contract is already wrong. A test suite that runs
with it on will not ship that.

**Leave it off in production**, unless the traffic is small enough not to care.
Validating a 2000-row page costs about **1.9x** the unvalidated response, and the
cost scales with the size of the result, not the size of the request. Two things
make that trade easy:

- It is a check on *your own code*, not on the caller. Turning it off gives an
  attacker nothing — every inbound value is still validated, always, with no flag
  to disable it.
- It runs **after** `execute()` returns, so it reports a broken contract rather
  than preventing one. A method that wrote to the database and then returned the
  wrong type has already written. In production you find out from `-32001`
  instead of from a wrong-shaped result; in a test you find out before the deploy.

!!! note "A cyclic result"
    A result that refers back to itself is refused with
    `Return value nests deeper than 64 levels` rather than being walked forever.
    With the check off it reaches the serializer instead, which ends in
    `-32603` — either way it stops, but the first message is the useful one.

---

## Protocol Compliance

### Strict Mode (Default)

```python
# v1.0: arrays, no batch
rpc = JSONRPC(version='1.0')
# Accepts: {"method": "add", "params": [1, 2], "id": 1}

# v2.0: objects, batch allowed
rpc = JSONRPC(version='2.0')
# Accepts: {"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": 1}
```

!!! note "A 2.0 server also answers 1.0-framed requests"
    A request with no `jsonrpc` member is treated as 1.0 and answered in 1.0
    framing, whatever the server is configured as. The strictness flags
    (`allow_batch`, `allow_dict_params`, `allow_list_params`) come from the
    **server's** configured version, not the request's — so a 1.0-framed request
    to a 2.0 server enjoys 2.0 permissiveness. This is intentional; configure the
    flags explicitly if you need one specific behaviour.

### Permissive Mode

```python
rpc = JSONRPC(
    version='2.0',
    allow_batch=True,       # Already default for v2.0
    allow_dict_params=True, # Already default for v2.0
    allow_list_params=True, # Already default for v2.0
)
```

---

## Decorator API (v2.0 only)

!!! warning "Prototyping Only"
    The decorator API is designed for rapid prototyping. Use Method classes for production.

```python
rpc = JSONRPC(version='2.0')  # v2.0 required

# Simple decorator
@rpc.method
def add(a: int, b: int) -> int:
    return a + b

# Custom method name
@rpc.method("my_custom_name")
def some_function(x: str) -> str:
    return x.upper()

# Async
@rpc.method
async def fetch(url: str) -> dict:
    return await http_get(url)
```

**Rules:**
- All parameters must have type hints
- Return type annotation required
- No `context` parameter support
- Only root-level registration
- Original function still callable: `add(1, 2)` returns `3`

---

## Complete Usage Example

```python
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method, MethodGroup
from jsonrpc.errors import JSONRPCError
from jsonrpc.openapi import OpenAPIGenerator

class Unauthenticated(JSONRPCError):
    code = -32010
    message = 'Authentication required'


# Parameters
@dataclass
class SearchParams:
    query: str
    limit: int = 10

@dataclass
class AuthContext:
    user_id: int | None

# Methods
class Search(Method):
    def execute(self, params: SearchParams, context: AuthContext) -> list[dict]:
        """Search items."""
        if not context.user_id:
            raise Unauthenticated()
        return [{"id": 1, "title": f"Result: {params.query}"}]

# Setup
rpc = JSONRPC(version='2.0', context_type=AuthContext)

group = MethodGroup()
group.register('items', Search())
rpc.register('search', group)

# OpenAPI
generator = OpenAPIGenerator(rpc, title="My API", version="1.0.0")
spec = generator.generate()

# Handle request
ctx = AuthContext(user_id=42)
response = rpc.handle(
    '{"jsonrpc": "2.0", "method": "search.items", "params": {"query": "test"}, "id": 1}',
    context=ctx
)
```

---

## Changelog

See [CHANGELOG.md](https://github.com/uandysmith/python-jsonrpc-lib/blob/main/CHANGELOG.md)
for the full history.

**0.4.0 highlights:** group middleware moved to `around_call()`, which runs for
every group on the resolved path — `execute_method()` only ever ran on the group
owning the method, so guards mounted over a namespace were inert. Non-finite
floats, non-UTF-8 bytes and non-object payloads are rejected; a union matching no
variant fails closed; exception text no longer reaches callers by default;
`validate_results=True` works for dataclass results and now checks their fields;
intermediate `Method` base classes are allowed. Read the upgrade notes before upgrading.
