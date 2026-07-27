# Async Methods

`python-jsonrpc-lib` supports both sync and async methods in the same server. Define `async def execute()` for IO-bound operations; keep `def execute()` for CPU-bound or simple logic. `rpc.handle_async()` handles both transparently.

The choice of sync vs async in `execute()` is independent from whether the framework is async — a sync method works fine in FastAPI, and an async method works fine when called through `handle_async()` in a plain asyncio script.

## Sync vs Async

Use `def execute()` when the work is CPU-bound or trivial. Use `async def execute()` when the method awaits IO — database queries, HTTP calls, file reads.

```python title="sync_method.py"
from dataclasses import dataclass
from jsonrpc import Method

@dataclass
class ProcessParams:
    numbers: list[int]

@dataclass
class ProcessResult:
    total: int
    count: int

class SyncProcess(Method):
    def execute(self, params: ProcessParams) -> ProcessResult:
        # CPU work — sync is fine here
        return ProcessResult(total=sum(params.numbers), count=len(params.numbers))
```

```python title="async_method.py"
from dataclasses import dataclass
from jsonrpc import Method

@dataclass
class FetchParams:
    url: str

@dataclass
class FetchResult:
    status: int
    length: int

class AsyncFetch(Method):
    async def execute(self, params: FetchParams) -> FetchResult:
        # IO-bound — async avoids blocking the event loop
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(params.url) as response:
                content = await response.read()
        return FetchResult(status=response.status, length=len(content))
```

## Mixing Sync and Async

Register sync and async methods on the same `JSONRPC` instance. `handle_async()` dispatches correctly to each.

```python title="mixed.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class AddParams:
    a: int
    b: int

class SyncAdd(Method):
    def execute(self, params: AddParams) -> int:
        return params.a + params.b

@dataclass
class SearchParams:
    query: str

@dataclass
class SearchResult:
    id: int
    title: str

class AsyncSearch(Method):
    async def execute(self, params: SearchParams) -> list[SearchResult]:
        import asyncio
        await asyncio.sleep(0.1)  # Simulate async IO
        return [SearchResult(id=1, title=f"Result: {params.query}")]

rpc = JSONRPC(version='2.0')
rpc.register('add', SyncAdd())
rpc.register('search', AsyncSearch())
```

## Calling Methods

```python title="calling.py"
import asyncio

request_json = '{"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": 1}'

# Sync handle — works for sync methods only
response = rpc.handle(request_json)

# Async handle — works for both sync and async methods
response = await rpc.handle_async(request_json)

# Internal direct call (sync)
result = rpc.call_method('add', {'a': 1, 'b': 2})

# Internal direct call (async)
result = await rpc.call_method_async('search', {'query': 'python'})
```

!!! warning "Don't call `rpc.handle()` from async code"
    `rpc.handle()` blocks the event loop if any method is async. Always use `rpc.handle_async()` in asyncio-based frameworks (FastAPI, aiohttp, etc.).

## Concurrent Batch Execution

When `handle_async()` receives a batch request, the methods run concurrently via `asyncio.gather` — but **not all at once**. `max_concurrent` (default 64) caps how many run simultaneously, so the wall-clock time is roughly:

```
ceil(N / max_concurrent) × duration
```

For a batch of 3 that is one round: three 100ms calls finish in ~100ms, not 300ms. For a batch of 100 at the default limit it is two rounds — ~200ms for 100ms calls, not 100ms.

The limit exists so one batch cannot open a hundred sockets at once and exhaust a connection pool. Match it to the pool it feeds: if your database pool holds 10 connections, `max_concurrent=10` is the honest number, and a higher one only queues work inside the driver instead of inside the library.

```python title="concurrent_batch.py"
import asyncio
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class FetchUserParams:
    user_id: int

@dataclass
class UserResult:
    user_id: int
    name: str

class FetchUser(Method):
    async def execute(self, params: FetchUserParams) -> UserResult:
        await asyncio.sleep(0.1)  # Simulate DB call
        return UserResult(user_id=params.user_id, name=f"User {params.user_id}")

rpc = JSONRPC(version='2.0')
rpc.register('get_user', FetchUser())

# Batch request — all 3 execute concurrently
batch_request = '''[
  {"jsonrpc": "2.0", "method": "get_user", "params": {"user_id": 1}, "id": 1},
  {"jsonrpc": "2.0", "method": "get_user", "params": {"user_id": 2}, "id": 2},
  {"jsonrpc": "2.0", "method": "get_user", "params": {"user_id": 3}, "id": 3}
]'''

# All 3 finish in ~0.1s total (not 0.3s): 3 is below max_concurrent
response = await rpc.handle_async(batch_request)

# A batch of 100 of the same calls takes ~0.2s, not ~0.1s:
# ceil(100 / 64) = 2 rounds at the default limit.
```

## Error Handling in Async

Exceptions from async methods are caught and converted to JSON-RPC errors exactly like sync ones. `InvalidParamsError` and `ServerError` map to protocol error codes; any other exception becomes a generic internal error.

```python title="async_errors.py"
from dataclasses import dataclass
from jsonrpc import Method
from jsonrpc.errors import InvalidParamsError, ServerError

@dataclass
class DownloadParams:
    url: str

@dataclass
class DownloadResult:
    size: int
    url: str

class DownloadFile(Method):
    async def execute(self, params: DownloadParams) -> DownloadResult:
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(params.url) as response:
                    if response.status == 404:
                        raise InvalidParamsError(f"File not found: {params.url}")
                    if response.status != 200:
                        raise ServerError(f"HTTP {response.status}")

                    content = await response.read()
                    return DownloadResult(size=len(content), url=params.url)

        except aiohttp.ClientError as e:
            raise ServerError(f"Network error: {e}")
```

## Async Context

Pass async resources (database sessions, connection pools) through context. The method receives them pre-initialized — no need to manage lifecycle inside `execute()`.

```python title="async_context.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method
from jsonrpc.errors import JSONRPCError

class Unauthenticated(JSONRPCError):
    code = -32010
    message = 'Authentication required'

@dataclass
class AsyncContext:
    db_session: object  # e.g. SQLAlchemy AsyncSession
    user_id: int | None

@dataclass
class ItemRow:
    id: int
    name: str

class FetchItems(Method):
    async def execute(self, params: None, context: AsyncContext) -> list[ItemRow]:
        if not context.user_id:
            raise Unauthenticated()

        # DB session comes from context — already open, will be closed by the caller
        rows = await context.db_session.execute(
            "SELECT id, name FROM items WHERE user_id = $1",
            context.user_id,
        )
        return [ItemRow(id=r.id, name=r.name) for r in rows.fetchall()]

rpc = JSONRPC(version='2.0', context_type=AsyncContext)
rpc.register('fetch_items', FetchItems())
```

!!! danger "A synchronous `execute()` blocks the whole event loop"
    `handle_async()` accepts synchronous methods, and it runs them **inline, on
    the event loop**. Nothing moves them to a thread. For the duration of a
    `def execute()`, no other request, no other coroutine and no heartbeat in
    the process makes progress — 20 synchronous methods of 50ms each serialize
    into a full second during which the worker is deaf.

    So a synchronous `execute()` in an async application is fine only for work
    measured in microseconds: arithmetic, dictionary lookups, formatting.
    Anything that waits — a file, a socket, a synchronous database driver,
    `time.sleep` — has to be `async def`, or the entire worker waits with it.

    If the work must stay synchronous, hand it to a thread yourself:

    ```python
    class Report(Method):
        async def execute(self, params: ReportParams) -> ReportResult:
            return await asyncio.to_thread(self._blocking_query, params)

        def _blocking_query(self, params: ReportParams) -> ReportResult:
            ...
    ```

    The library does not do this for you on purpose: which work belongs on a
    thread is a decision about your own code, and doing it silently would change
    the execution model for every synchronous method ever written against it.

## Shared State and Thread Safety

`Method` and `MethodGroup` instances are singletons. One object serves every
request — every thread of a threaded WSGI server, every task of an event loop.
The contract that follows:

- **Per-request data lives in `context`**, never on `self`. Storing the current
  user, the current transaction or a partial result on the instance means two
  concurrent requests share it.
- **Deliberate shared state on the instance needs its own synchronization.** A
  rate limiter's counters or a cache's dict are shared by design; that is fine,
  but concurrent mutation is your responsibility, and `dict` operations being
  atomic under CPython's GIL is not a guarantee you should rely on for
  check-then-act sequences.
- **Middleware objects are shared too.** `around_call()` runs concurrently for
  many requests on one group instance. Keep its locals local.

```python
class Counter(Method):
    def __init__(self):
        super().__init__()
        self.last_user = None            # WRONG: shared across requests

    def execute(self, params: P, context: Ctx) -> int:
        self.last_user = context.user_id  # racy, and leaks between callers
        return compute(params)
```

The registry itself — `register()` and `unregister()` — is a startup activity and
is **not** safe to run concurrently with itself. Build the method tree before you
start serving; mutating it from several threads at once can leave a subtree
mounted that no reader can distinguish from the one you intended.

## Key Points

- `async def execute()` — for IO-bound methods (database, HTTP, filesystem)
- Instances are shared across requests and threads — per-request state belongs in `context`
- `rpc.handle_async()` — handles both sync and async methods; always use in async frameworks
- Batch requests via `handle_async()` execute **concurrently** — significant speedup for IO-heavy batches
- Errors from async methods propagate the same way as from sync methods

## What's Next?

→ [Batch Requests](batch.md) - Multiple calls in one round-trip
