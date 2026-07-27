# python-jsonrpc-lib

**Simple, yet solid.** JSON-RPC 1.0/2.0 for Python.

JSON-RPC is a small protocol: a method name, some parameters, a result. python-jsonrpc-lib keeps it that way. You write ordinary Python functions and dataclasses; the library handles validation, routing, error responses, and API documentation. No framework lock-in, no external dependencies, no boilerplate.

## Install

```bash
pip install python-jsonrpc-lib
```

## Quickstart

Define methods as classes with typed parameters. The library validates inputs, routes calls, and builds responses automatically.

```python
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method, MethodGroup

@dataclass
class AddParams:
    a: int
    b: int

class Add(Method):
    def execute(self, params: AddParams) -> int:
        return params.a + params.b

@dataclass
class GreetParams:
    name: str
    greeting: str = 'Hello'

class Greet(Method):
    def execute(self, params: GreetParams) -> str:
        return f'{params.greeting}, {params.name}!'

rpc = JSONRPC(version='2.0')
rpc.register('add', Add())
rpc.register('greet', Greet())

response = rpc.handle('{"jsonrpc": "2.0", "method": "add", "params": {"a": 5, "b": 3}, "id": 1}')
# '{"jsonrpc": "2.0", "result": 8, "id": 1}'
```

Pass in a JSON string, get a JSON string back. What carries it over the wire is up to you.

If `a` is `"five"` instead of `5`, the caller receives a `-32602 Invalid params` error immediately — no exception handling on your end.

That covers the types JSON itself has. For a value JSON cannot express — a date, an `Enum`, a `Decimal` — take it as a `str` and convert it in `__post_init__`, raising `ValueError` on anything you will not accept; that also becomes `-32602`. See [Parameters](docs/tutorial/03-parameters.md#types-json-cannot-express).

The same `AddParams` dataclass drives validation, IDE autocomplete, and the OpenAPI schema.

## Why python-jsonrpc-lib?

- **Zero dependencies** — pure Python 3.11+. Nothing to pin, nothing to audit beyond the library itself.
- **Type validation from dataclasses** — declare parameters as a dataclass, get automatic validation and clear error messages for free.
- **OpenAPI docs auto-generated** — type hints and docstrings you already wrote become a full OpenAPI 3.0 spec. Point any Swagger-compatible UI at it and your API is self-documented.
- **Transport-agnostic** — `rpc.handle(json_string)` returns a string, or `None` for a notification. HTTP, WebSocket, TCP, message queue: your choice.
- **Spec-compliant by default** — v1.0 and v2.0 rules enforced out of the box, configurable when you need to support legacy clients.

## Namespacing and Middleware

Use `MethodGroup` to organize methods into namespaces and add cross-cutting concerns:

```python
from jsonrpc.errors import JSONRPCError

math = MethodGroup()
math.register('add', Add())

rpc = JSONRPC(version='2.0')
rpc.register('math', math)

# "math.add" is now available

# Cross-cutting concerns go in around_call(), which runs for every group
# on the path -- so a guard mounted here covers everything nested below it.
# Refuse with a JSONRPCError subclass: anything else becomes a bare
# -32603 Internal error, and the caller cannot tell a refusal from a fault.
class Unauthenticated(JSONRPCError):
    code = -32010
    message = 'Authentication required'

class RequireAuth(MethodGroup):
    def around_call(self, call, context, call_next):
        if context.user_id is None:
            raise Unauthenticated()
        return call_next(context)
```

## Quick Prototyping

For scripts and throwaway code, the `@rpc.method` decorator registers functions directly (v2.0 only):

```python
rpc = JSONRPC(version='2.0')

@rpc.method
def add(a: int, b: int) -> int:
    return a + b
```

For production use, prefer `Method` classes — they support context, middleware, and groups.

## Documentation

Full documentation with tutorials, integration guides, and API reference:

- [Tutorial: Hello World](docs/tutorial/01-hello-world.md) — first method, explained
- [Tutorial: Parameters](docs/tutorial/03-parameters.md) — dataclass validation in detail
- [Tutorial: Context](docs/tutorial/05-context.md) — authentication and per-request data
- [Tutorial: OpenAPI](docs/tutorial/07-openapi.md) — interactive API documentation
- [Flask integration](docs/integrations/flask.md)
- [FastAPI integration](docs/integrations/fastapi.md)
- [Philosophy](docs/philosophy.md) — design decisions and trade-offs
- [API Reference](docs/api-reference.md)

## Claude Code Integration

If you use [Claude Code](https://claude.ai/claude-code), a skill for this library is available. It gives Claude built-in knowledge of jsonrpc-lib's API: creating methods, registering them, organizing with groups, handling errors, and adding context and middleware — without having to look up docs.

To use it, add the skill file to your project's `.claude/skills/` directory.

## License

MIT
