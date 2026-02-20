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
    max_concurrent: int | None = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `version` | `'1.0'` \| `'2.0'` | `'2.0'` | JSON-RPC protocol version |
| `validate_results` | `bool` | `False` | Validate return types against annotations |
| `context_type` | `type \| None` | `None` | Expected context type for validation |
| `allow_batch` | `bool \| None` | `None` | Batch requests (default: spec-compliant) |
| `allow_dict_params` | `bool \| None` | `None` | Object params (default: spec-compliant) |
| `allow_list_params` | `bool \| None` | `None` | Array params (default: spec-compliant) |
| `max_batch` | `int` | `100` | Max requests per batch (`-1` = unlimited) |
| `max_concurrent` | `int \| None` | `None` | Max concurrent coroutines in async batch (`None` = `os.cpu_count()`, `-1` = unlimited) |

**Default params mode (spec-compliant):**

| Version | `allow_batch` | `allow_dict_params` | `allow_list_params` |
|---------|---------------|---------------------|---------------------|
| v1.0 | `False` | `False` | `True` |
| v2.0 | `True` | `True` | `True` |

**Methods:**

```python
# Handle JSON-RPC request (sync)
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

**Available in `execute()`:**
- `self.rpc` - access to parent JSONRPC instance for internal calls

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
class CustomGroup(MethodGroup):
    def execute_method(self, method, params, context=None):
        # Before execution
        result = super().execute_method(method, params, context)
        # After execution
        return result
```

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
    **kwargs,
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
    scheme="bearer",
    bearerFormat="JWT",
)
generator.add_security_requirement("BearerAuth")
spec = generator.generate()
```

---

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

---

## Type System

### Supported Parameter Types

| Python Type | JSON type | Notes |
|-------------|-----------|-------|
| `int` | number (integer) | No float coercion |
| `float` | number | Accepts int values too |
| `str` | string | |
| `bool` | boolean | Must be exactly `true/false` |
| `list` | array | Untyped |
| `list[T]` | array | Each element validated as T |
| `dict` | object | Untyped |
| `dict[K, V]` | object | Keys and values validated |
| `@dataclass` | object | Recursively validated |
| `T \| None` | T or null | Optional type |
| `T \| U` | T or U | Union type (first match wins) |
| `Literal["a", "b"]` | enum string | Restricted values |
| `Any` | any | No validation |
| `None` | null or absent | No parameters |

### Nested Type Example

```python
@dataclass
class Config:
    features: dict[str, bool]
    mode: Literal["dev", "prod"]
    tags: list[str] | None = None
    max_workers: int = 4
```

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
from jsonrpc.errors import InvalidParamsError
from jsonrpc.openapi import OpenAPIGenerator

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
            raise InvalidParamsError("Authentication required")
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

### 0.3.1 (First Public Release)

- JSON-RPC 1.0 and 2.0 support
- Dataclass-based parameter validation
- Built-in OpenAPI generation
- Hierarchical context support
- Decorator API for prototyping
- Async/sync methods
- Batch request handling
- Strict mode by default
- Zero external dependencies
