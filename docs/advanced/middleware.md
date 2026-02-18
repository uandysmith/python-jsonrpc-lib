# Middleware

## Custom MethodGroup

`MethodGroup` is the extension point for cross-cutting concerns. Override `execute_method()` to inject behavior before and/or after every method call in the group — without touching the methods themselves.

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

---

## Logging Middleware

Logging is the most universally useful middleware. Once you have it, debugging production issues goes from "guessing" to "reading logs". The example below records the method name, authenticated user, and wall-clock duration. On failure it logs the error and re-raises, so the original exception propagates unchanged.

What you might want to add in a real implementation: structured logging (JSON), correlation IDs, log levels per method, sampling for high-throughput endpoints.

```python title="logging_middleware.py"
import time
import logging
from jsonrpc import MethodGroup

logger = logging.getLogger('rpc')

class LoggingGroup(MethodGroup):
    def execute_method(self, method, params, context: AppContext):
        method_name = method.__class__.__name__
        user = context.user_id or "anonymous"
        start = time.perf_counter()

        logger.info(f"→ {method_name} [user={user}]")
        try:
            result = super().execute_method(method, params, context)
            duration = time.perf_counter() - start
            logger.info(f"← {method_name} completed in {duration:.3f}s")
            return result
        except Exception as e:
            duration = time.perf_counter() - start
            logger.error(f"FAILED: {method_name} failed in {duration:.3f}s: {e}")
            raise
```

---

## Rate Limiting Middleware

Rate limiting protects the server from being overwhelmed by a single caller — whether that is a misbehaving client or an abuse attempt. The example uses an in-process sliding window counter keyed by `user_id` (or by IP for anonymous requests).

This is enough for a single-process deployment. For a multi-process or distributed setup you would replace the in-memory `dict` with Redis or another shared store. You might also want to differentiate limits per method, per role, or per plan tier.

```python title="rate_limit_middleware.py"
from collections import defaultdict
import time
from jsonrpc import MethodGroup
from jsonrpc.errors import ServerError

class RateLimitGroup(MethodGroup):
    def __init__(self, max_calls: int = 60, window: int = 60):
        super().__init__()
        self.max_calls = max_calls
        self.window = window
        self.calls: dict[str, list[float]] = defaultdict(list)

    def execute_method(self, method, params, context: AppContext):
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
        return super().execute_method(method, params, context)
```

---

## Authentication Middleware

`RequireAuthGroup` is a guard: it blocks any call where `context.user_id is None` and lets everything else through. It does not validate tokens — that is the transport layer's job (see below).

This separation is intentional. The transport layer (Flask/FastAPI route handler) is the right place to validate credentials: it has access to HTTP headers, cookies, and framework-specific helpers like JWT libraries or session stores. By the time a request reaches the RPC layer the context is already populated with a trusted `user_id`, or `None` if the caller is unauthenticated. `RequireAuthGroup` simply enforces the requirement.

```python title="auth_middleware.py"
from jsonrpc import MethodGroup
from jsonrpc.errors import InvalidParamsError

class RequireAuthGroup(MethodGroup):
    def execute_method(self, method, params, context: AppContext):
        if context.user_id is None:
            raise InvalidParamsError("Authentication required")
        return super().execute_method(method, params, context)
```

**Transport layer — where token validation actually happens:**

```python title="flask_transport.py"
from flask import Flask, request
from jwt import decode, InvalidTokenError

app = Flask(__name__)

@app.route('/rpc', methods=['POST'])
def handle_rpc():
    user_id = None
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if token:
        try:
            payload = decode(token, SECRET_KEY, algorithms=['HS256'])
            user_id = payload['user_id']
        except InvalidTokenError:
            pass

    ctx = AppContext(user_id=user_id, ip_address=request.remote_addr)
    return rpc.handle(request.data, context=ctx)
```

!!! note "Why not validate inside the middleware?"
    Middleware runs inside the RPC layer, which knows nothing about HTTP, tokens, or sessions. Putting validation there would couple the protocol layer to your auth library. Keeping it in the transport handler makes each layer responsible for exactly one thing.

---

## Caching Middleware

Caching is useful for methods that are expensive to compute but whose result changes infrequently — think search suggestions, catalog data, or config lookups. The example stores results in an in-process dict with a TTL.

Note that the cache key is based on method name and params only — not on `context`. This makes sense for public, read-only data. If results differ per user (e.g. a personalised feed), include the user ID in the key: `f"{method.__class__.__name__}:{context.user_id}:{json.dumps(params, ...)}"`.

For production consider replacing the dict with Redis or Memcached so the cache is shared across processes and survives restarts.

```python title="caching_middleware.py"
import hashlib
import json
import time
from jsonrpc import MethodGroup

class CachingGroup(MethodGroup):
    def __init__(self, ttl_seconds: int = 60):
        super().__init__()
        self.ttl = ttl_seconds
        self._cache: dict[str, object] = {}
        self._timestamps: dict[str, float] = {}

    def _cache_key(self, method, params) -> str:
        raw = f"{method.__class__.__name__}:{json.dumps(params, sort_keys=True, default=str)}"
        return hashlib.md5(raw.encode()).hexdigest()

    def execute_method(self, method, params, context: AppContext):
        key = self._cache_key(method, params)
        now = time.time()

        if key in self._cache and now - self._timestamps[key] < self.ttl:
            return self._cache[key]

        result = super().execute_method(method, params, context)
        self._cache[key] = result
        self._timestamps[key] = now
        return result
```

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

authed = RequireAuthGroup()
authed.register('search', core)

rate_limited = RateLimitGroup(max_calls=30, window=60)
rate_limited.register('protected', authed)

cached = CachingGroup(ttl_seconds=60)
cached.register('api', rate_limited)

logged = LoggingGroup()
logged.register('v1', cached)

rpc.register('public', logged)
# Method: public.v1.api.protected.search.items
# Chain: Logging → Caching → Rate Limiting → Auth → Search
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

---

## Key Points

- Define a typed `AppContext` dataclass — use it throughout all middleware
- Access `context.user_id` directly — no `hasattr`, no `isinstance`
- Transport layer (Flask/FastAPI) constructs and validates the context
- Middleware enforces constraints, does not mutate context
- Chain multiple groups for layered behavior
- Order matters: outermost group's middleware executes first

!!! tip "These are starting points, not production libraries"
    The examples on this page use in-process storage and have no persistence, no distributed coordination, and no thread safety guarantees beyond what CPython's GIL provides. Adapt them to your infrastructure — swap the dict for Redis, add locks where needed, hook into your logging framework.

## What's Next?

→ [API Reference](../api-reference.md) - Complete technical reference
