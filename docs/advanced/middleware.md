# Middleware

## Two hooks, and the difference between them

`MethodGroup` is the extension point for cross-cutting concerns. There are two hooks, and picking the wrong one is the single most consequential mistake you can make with this library:

| Hook | Runs on | Use it for |
|---|---|---|
| `around_call()` | **every group on the resolved path**, outermost first | guards, logging, caching, rate limiting — anything that must cover a namespace |
| `execute_method()` | **only the group the method is registered on** | changing how that group's own methods are invoked |

A method registered as `api.v1.search.items` is owned by the innermost group. `execute_method()` runs there and nowhere else. `around_call()` runs on `api`, then `v1`, then `search`, then the call reaches the method.

!!! danger "If you are upgrading from 0.3.x"
    In 0.3.x, `execute_method()` was the only hook, and dispatch called it on the owning group alone. A guard mounted above a subgroup **never ran** — the request reached the method with no error and no log line. If you have a `MethodGroup` subclass that guards, logs, or limits calls into nested subgroups, move that logic to `around_call()`. Mounting a group that overrides `execute_method()` but owns only subgroups is now rejected at registration, so the broken arrangement fails at startup instead of silently letting calls through.

The examples below are **reference implementations** showing common patterns. They are intentionally simplified: no persistence, no distributed coordination, no production-grade edge cases. Treat them as a starting point and adapt them to your stack.

## Context for Middleware

All examples on this page share a common typed context. In a real app your context will carry whatever your transport layer can provide — user ID from a session, IP from the request, tenant ID from a subdomain, and so on.

```python title="context.py"
from dataclasses import dataclass

@dataclass
class AppContext:
    user_id: int | None
    ip_address: str
```

## The `around_call` signature

```python
def around_call(self, call: CallInfo, context: AppContext, call_next):
    # before
    result = call_next(context)   # continue down the chain
    # after
    return result
```

- `call` carries `call.path` (the full dotted path as requested), `call.method` (the `Method` instance) and `call.params` (already validated).
- `context` is what the caller passed to `handle()`, possibly replaced by a group further out.
- `call_next(context)` continues the chain. The innermost one invokes the owning group's `execute_method()`. **Not calling it vetoes the call.** Pass a different context to enrich what everything below sees.

Annotate `context`. That annotation is the group's declared `context_type`, and
the library checks it at registration: a method demanding a context the group
does not promise is refused there rather than at the first request. Leaving it
off is legal and simply means the group declares nothing.

Async dispatch uses `around_call_async()`, whose default awaits the rest of the chain:

```python
async def around_call_async(self, call: CallInfo, context: AppContext, call_next):
    result = await call_next(context)
    return result
```

!!! warning "Override both, or neither"
    A synchronous `around_call()` cannot await an async method — its `call_next` would hand it an unfinished coroutine, and any post-processing would run against that instead of the result. So a group that overrides only `around_call()` **refuses to accept an async method below it at registration time**. If your namespace contains any `async def execute`, override both hooks. The same rule applies to the `execute_method()` / `execute_method_async()` pair.

---

## Logging Middleware

Logging is the most universally useful middleware. Once you have it, debugging production issues goes from "guessing" to "reading logs". The example below records the method path, authenticated user, and wall-clock duration. On failure it logs the error and re-raises, so the original exception propagates unchanged.

Note that it logs `call.path`, not the method's class name: the path is what identifies a call, and one class can be registered under several names.

What you might want to add in a real implementation: structured logging (JSON), correlation IDs, log levels per method, sampling for high-throughput endpoints.

```python title="logging_middleware.py"
import time
import logging
from jsonrpc import CallInfo, MethodGroup

logger = logging.getLogger('rpc')

class LoggingGroup(MethodGroup):
    def around_call(self, call: CallInfo, context: AppContext, call_next):
        user = context.user_id or "anonymous"
        start = time.perf_counter()

        logger.info(f"→ {call.path} [user={user}]")
        try:
            result = call_next(context)
            duration = time.perf_counter() - start
            logger.info(f"← {call.path} completed in {duration:.3f}s")
            return result
        except Exception as e:
            duration = time.perf_counter() - start
            logger.error(f"FAILED: {call.path} failed in {duration:.3f}s: {e}")
            raise

    async def around_call_async(self, call: CallInfo, context: AppContext, call_next):
        user = context.user_id or "anonymous"
        start = time.perf_counter()

        logger.info(f"→ {call.path} [user={user}]")
        try:
            result = await call_next(context)
            duration = time.perf_counter() - start
            logger.info(f"← {call.path} completed in {duration:.3f}s")
            return result
        except Exception as e:
            duration = time.perf_counter() - start
            logger.error(f"FAILED: {call.path} failed in {duration:.3f}s: {e}")
            raise
```

---

## Rate Limiting Middleware

Rate limiting protects the server from being overwhelmed by a single caller — whether that is a misbehaving client or an abuse attempt. The example uses an in-process sliding window counter keyed by `user_id` (or by IP for anonymous requests).

This is enough for a single-process deployment. For a multi-process or distributed setup you would replace the in-memory `dict` with Redis or another shared store. You might also want to differentiate limits per method, per role, or per plan tier.

```python title="rate_limit_middleware.py"
from collections import defaultdict
import time
from jsonrpc import CallInfo, MethodGroup
from jsonrpc.errors import ServerError

class RateLimitGroup(MethodGroup):
    def __init__(self, max_calls: int = 60, window: int = 60):
        super().__init__()
        self.max_calls = max_calls
        self.window = window
        self.calls: dict[str, list[float]] = defaultdict(list)

    def _check(self, context: AppContext) -> None:
        identifier = str(context.user_id) if context.user_id else context.ip_address
        now = time.time()

        self.calls[identifier] = [
            t for t in self.calls[identifier]
            if now - t < self.window
        ]

        if len(self.calls[identifier]) >= self.max_calls:
            remaining = int(self.window - (now - self.calls[identifier][0]))
            raise ServerError(
                f"Rate limit exceeded. Try again in {remaining}s",
                code=-32029
            )

        self.calls[identifier].append(now)

    def around_call(self, call: CallInfo, context: AppContext, call_next):
        self._check(context)
        return call_next(context)

    async def around_call_async(self, call: CallInfo, context: AppContext, call_next):
        self._check(context)
        return await call_next(context)
```

---

## Authentication Middleware

`RequireAuthGroup` is a guard: it blocks any call where `context.user_id is None` and lets everything else through. It does not validate tokens — that is the transport layer's job (see below).

This separation is intentional. The transport layer (Flask/FastAPI route handler) is the right place to validate credentials: it has access to HTTP headers, cookies, and framework-specific helpers like JWT libraries or session stores. `RequireAuthGroup` enforces the requirement; it cannot verify anything the transport did not.

!!! warning "The guard is only as good as the context"
    `RequireAuthGroup` trusts `context.user_id`. That value is trustworthy only if the transport handler **verified a credential** before building the context. A handler that copies a request header into `user_id` grants whatever the caller asserts, and this guard will happily wave it through. See the transport example below for what verification looks like.

```python title="auth_middleware.py"
from jsonrpc import CallInfo, MethodGroup
from jsonrpc.errors import JSONRPCError

class Unauthenticated(JSONRPCError):
    """No verified caller. -32000..-32099 is reserved for exactly this."""

    code = -32010
    message = 'Authentication required'

class RequireAuthGroup(MethodGroup):
    def _check(self, context: AppContext) -> None:
        if context.user_id is None:
            raise Unauthenticated()

    def around_call(self, call: CallInfo, context: AppContext, call_next):
        self._check(context)
        return call_next(context)

    async def around_call_async(self, call: CallInfo, context: AppContext, call_next):
        self._check(context)
        return await call_next(context)
```

!!! danger "Refuse with a `JSONRPCError`, or the refusal disappears"
    Only a `JSONRPCError` subclass keeps its code and its message on the way out.
    Everything else — `PermissionError`, `RuntimeError`, a framework's own
    `Forbidden` — is caught as an unhandled exception and answered with a bare
    `{"code": -32603, "message": "Internal error"}`, with a full traceback logged
    at ERROR level. Per unauthorized attempt. The caller cannot tell a refusal
    from a server fault, and neither can you, reading the log.

    `-32602 Invalid params` is a poor fit for the same reason in reverse: the
    caller cannot tell "you are not allowed" from "you misspelled a field". Pick
    a code in the implementation-defined range and mean it.

**Transport layer — where token validation actually happens:**

```python title="flask_transport.py"
from flask import Flask, request
from jwt import decode, InvalidTokenError

app = Flask(__name__)

@app.route('/rpc', methods=['POST'])
def handle_rpc():
    if not request.mimetype.startswith('application/json'):
        return {'error': 'Content-Type must be application/json'}, 415

    user_id = None
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if token:
        try:
            payload = decode(token, SECRET_KEY, algorithms=['HS256'])
            user_id = payload['user_id']
        except InvalidTokenError:
            pass

    ctx = AppContext(user_id=user_id, ip_address=request.remote_addr)

    response = rpc.handle(request.data, context=ctx)
    if response is None:          # notification: nothing to send back
        return '', 204
    return response, 200, {'Content-Type': 'application/json'}
```

!!! note "Why not validate inside the middleware?"
    Middleware runs inside the RPC layer, which knows nothing about HTTP, tokens, or sessions. Putting validation there would couple the protocol layer to your auth library. Keeping it in the transport handler makes each layer responsible for exactly one thing.

---

## Caching Middleware

Caching is useful for methods that are expensive to compute but whose result changes infrequently — think search suggestions, catalog data, or config lookups. The example stores results in an in-process dict with a TTL.

Two details in the key deserve attention:

- **It keys on `call.path`, not on the method's class name.** The `MethodGroup` docstring teaches registering two instances of one class under different names (`ReportMethod(scope='mine')` and `ReportMethod(scope='everyone')`). Keyed by class name, those two produce identical key material and share one cache slot — a low-privilege sibling can then prime the slot a privileged sibling reads, in both directions.
- **It does not key on `context`.** That is correct only for data that is the same for every caller. If results differ per user, add the user to the key: `f"{call.path}:{context.user_id}:{...}"`.

```python title="caching_middleware.py"
import hashlib
import json
import time
from jsonrpc import CallInfo, MethodGroup

class CachingGroup(MethodGroup):
    def __init__(self, ttl_seconds: int = 60):
        super().__init__()
        self.ttl = ttl_seconds
        self._cache: dict[str, object] = {}
        self._timestamps: dict[str, float] = {}

    def _cache_key(self, call: CallInfo) -> str:
        raw = f"{call.path}:{json.dumps(call.params, sort_keys=True, default=str)}"
        return hashlib.md5(raw.encode()).hexdigest()

    def around_call(self, call: CallInfo, context: AppContext, call_next):
        key = self._cache_key(call)
        now = time.time()

        if key in self._cache and now - self._timestamps[key] < self.ttl:
            return self._cache[key]

        result = call_next(context)
        self._cache[key] = result
        self._timestamps[key] = now
        return result
```

!!! danger "A cache must sit above every guard, never below one"
    On a hit, `around_call` returns without calling `call_next`, so **everything below it is skipped** — inner groups, the owning group's `execute_method()`, the library's own runtime context check, and any authorization the method performs inside `execute()`. A user whose access was revoked is still served a primed key; a primed key is also unmetered if the rate limiter sits inside the cache.

    Mount the cache **innermost** among your middleware, so no guard sits below it — or make the key carry every input the guards depend on, which is the same thing said less safely. Per-user keys separate callers, but they do not re-run a check.

---

## Full Example

The four classes above compose cleanly. Each group wraps the next, forming a chain where the request passes through every layer on the way in and every layer on the way back out.

```python title="full_example.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, MethodGroup, Method

@dataclass
class SearchParams:
    query: str
    limit: int = 10

@dataclass
class SearchResult:
    id: int
    title: str
    user_id: int

class Search(Method):
    def execute(self, params: SearchParams, context: AppContext) -> list[SearchResult]:
        """Search items for authenticated user."""
        return [SearchResult(id=1, title=f"Result for: {params.query}", user_id=context.user_id)]

# Assemble middleware stack
rpc = JSONRPC(version='2.0', context_type=AppContext)

core = MethodGroup()
core.register('items', Search())

# Cache innermost: no guard may sit below it, or a cache hit would skip that guard.
cached = CachingGroup(ttl_seconds=60)
cached.register('search', core)

authed = RequireAuthGroup()
authed.register('protected', cached)

rate_limited = RateLimitGroup(max_calls=30, window=60)
rate_limited.register('api', authed)

logged = LoggingGroup()
logged.register('v1', rate_limited)

rpc.register('public', logged)
# Method: public.v1.api.protected.search.items
# Chain: Logging → Rate Limiting → Auth → Caching → Search
```

**JSON-RPC Request:**

```json title="request.json"
{
  "jsonrpc": "2.0",
  "method": "public.v1.api.protected.search.items",
  "params": {"query": "python"},
  "id": 1
}
```

**Transport call:**

```python
ctx = AppContext(user_id=42, ip_address="10.0.0.1")
response = rpc.handle(request_json, context=ctx)
```

An anonymous caller — `AppContext(user_id=None, ...)` — gets `-32010 Authentication required`, and `Search.execute()` never runs, no matter how deeply it is nested below the guard.

---

## Key Points

- Use `around_call()` for anything that must cover a namespace; `execute_method()` only ever runs on the group that owns the method
- Override `around_call_async()` too if any method below the group is `async def` — registration rejects the half-overridden pair
- Define a typed `AppContext` dataclass — use it throughout all middleware
- Access `context.user_id` directly — no `hasattr`, no `isinstance`
- Transport layer (Flask/FastAPI) constructs the context and is the only place that can *verify* a credential
- A guard raises; it does not mutate the context. To enrich the context, pass a new one to `call_next()`
- Order matters: the outermost group runs first on the way in and last on the way out
- Put caches innermost, below every guard

!!! tip "These are starting points, not production libraries"
    The examples on this page use in-process storage and have no persistence, no distributed coordination, and no thread safety guarantees beyond what CPython's GIL provides. Adapt them to your infrastructure — swap the dict for Redis, add locks where needed, hook into your logging framework.

!!! note "Groups and methods are shared across requests"
    A `Method` or `MethodGroup` instance is a singleton: one object serves every request, on every thread. Keep per-request data in `context`, never on `self`. The `self.calls` dict in `RateLimitGroup` above is deliberate shared state, not per-request state — and it is exactly the kind of thing that needs a lock in a threaded server.

## What's Next?

→ [API Reference](../api-reference.md) - Complete technical reference
