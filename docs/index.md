# python-jsonrpc-lib

**Simple, yet solid.** JSON-RPC is a small protocol — a method name, some parameters, a result. python-jsonrpc-lib keeps it that way. You write ordinary Python functions and dataclasses; the library handles validation, routing, error responses, and even API documentation. No boilerplate, no surprises, no external dependencies.

---

## Why python-jsonrpc-lib?

**Built-in OpenAPI Generator** — Type hints and docstrings are already there in your code. python-jsonrpc-lib reads them and produces a full OpenAPI 3.0 spec automatically. Point RapiDoc or Swagger UI at it and you get an interactive API browser — no schema files to write or maintain.

**Type-safe by default** — Parameters are declared as dataclasses. The library validates every incoming request against the declared types before your code runs. Wrong type? Missing field? The caller gets a clear error before a single line of your logic executes.

**Zero dependencies** — Pure Python 3.11+. `pip install python-jsonrpc-lib` and you're done. Nothing to pin, nothing to audit beyond the library itself.

**Start simple, grow without friction** — `Method` classes are the recommended foundation: explicit, testable, and production-ready from day one. The `@rpc.method` decorator is there when you want something running in minutes with no ceremony, but comes with limitations. Add `MethodGroup` when you need namespacing, middleware, or hierarchical routing.

**Transport-agnostic** — `rpc.handle(request_json)` returns a response string. What carries it — HTTP, WebSockets, TCP, a message queue — is entirely up to you.

---

## Quick Start

```bash
pip install python-jsonrpc-lib
```

The quickest way to start is the decorator API. Register any annotated function and the library takes care of the rest:

```python title="server.py"
from jsonrpc import JSONRPC

rpc = JSONRPC(version='2.0')

@rpc.method
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@rpc.method
def greet(name: str, greeting: str = "Hello") -> str:
    """Greet someone."""
    return f"{greeting}, {name}!"

request = '{"jsonrpc": "2.0", "method": "add", "params": {"a": 5, "b": 3}, "id": 1}'
response = rpc.handle(request)
print(response)
```

Pass a JSON-RPC request string, get a JSON-RPC response string back:

```json title="request.json"
{
  "jsonrpc": "2.0",
  "method": "add",
  "params": {"a": 5, "b": 3},
  "id": 1
}
```

```json title="response.json"
{
  "jsonrpc": "2.0",
  "result": 8,
  "id": 1
}
```

If `a` is `"five"` instead of `5`, the caller gets a `-32602 Invalid params` error immediately — no exception handling needed on your end.

---

## Production-Ready Code

The decorator is great for getting started. For production, `Method` classes give you a clean, testable structure — one class per method, explicit parameter types:

```python title="production.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class AddParams:
    a: int
    b: int

class AddMethod(Method):
    def execute(self, params: AddParams) -> int:
        return params.a + params.b

rpc = JSONRPC(version='2.0')
rpc.register('math.add', AddMethod())
```

The `AddParams` dataclass is the contract between the caller and the method. Validation, IDE autocomplete, and OpenAPI schema all come from the same definition.

→ [Method Classes in depth](tutorial/02-method-classes.md)

---

## OpenAPI — Included

Most JSON-RPC libraries treat documentation as something you write separately, after the fact. python-jsonrpc-lib generates it from the code you've already written:

```python title="openapi_demo.py"
from jsonrpc import JSONRPC
from jsonrpc.openapi import OpenAPIGenerator

rpc = JSONRPC(version='2.0')

@rpc.method
def calculate(x: float, y: float, operation: str) -> float:
    """Perform arithmetic operation: +, -, *, /"""
    ops = {'+': x + y, '-': x - y, '*': x * y, '/': x / y}
    return ops.get(operation, 0.0)

generator = OpenAPIGenerator(rpc, title="Calculator API", version="1.0.0")
spec = generator.generate()  # Full OpenAPI 3.0 spec, ready to serve
```

Serve `spec` from a `/openapi.json` endpoint, add a RapiDoc or Swagger UI page, and your API is self-documented. Types, descriptions, required fields — all derived from the source.

→ [Full OpenAPI tutorial](tutorial/07-openapi.md)

---

## Where to Go from Here

This is the top of the slide. Follow the path at your own pace:

### Understand the library
- [**Philosophy**](philosophy.md) - Design decisions and the reasoning behind them

### Build step by step
- [**Tutorial: Hello World**](tutorial/01-hello-world.md) - Your first method, explained
- [**Tutorial: Parameters**](tutorial/03-parameters.md) - Dataclass validation in detail
- [**Tutorial: Context**](tutorial/05-context.md) - Authentication and per-request data
- [**Tutorial: OpenAPI**](tutorial/07-openapi.md) - Interactive API documentation

### Integrate with a framework
- [**Flask**](integrations/flask.md) - Classic WSGI setup
- [**FastAPI**](integrations/fastapi.md) - Async + built-in interactive docs

### Go deeper
- [**Async Methods**](advanced/async.md) - Concurrent execution with `handle_async`
- [**Batch Requests**](advanced/batch.md) - Multiple calls in one round-trip
- [**Middleware**](advanced/middleware.md) - Logging, rate limiting, auth guards via `MethodGroup`

---

**License**: MIT — [GitHub](https://github.com/uandysmith/python-jsonrpc-lib)
