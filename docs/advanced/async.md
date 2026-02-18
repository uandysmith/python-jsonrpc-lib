# Async Methods

`jsonrpc-lib` supports both sync and async methods in the same server. Define `async def execute()` for IO-bound operations; keep `def execute()` for CPU-bound or simple logic. `rpc.handle_async()` handles both transparently.

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

When `handle_async()` receives a batch request, all methods run concurrently via `asyncio.gather`. N async methods each taking 100ms complete in ~100ms total, not N×100ms.

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

# All 3 finish in ~0.1s total (not 0.3s)
response = await rpc.handle_async(batch_request)
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
from jsonrpc.errors import InvalidParamsError

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
            raise InvalidParamsError("Authentication required")

        # DB session comes from context — already open, will be closed by the caller
        rows = await context.db_session.execute(
            "SELECT id, name FROM items WHERE user_id = $1",
            context.user_id,
        )
        return [ItemRow(id=r.id, name=r.name) for r in rows.fetchall()]

rpc = JSONRPC(version='2.0', context_type=AsyncContext)
rpc.register('fetch_items', FetchItems())
```

## Key Points

- `async def execute()` — for IO-bound methods (database, HTTP, filesystem)
- `rpc.handle_async()` — handles both sync and async methods; always use in async frameworks
- Batch requests via `handle_async()` execute **concurrently** — significant speedup for IO-heavy batches
- Errors from async methods propagate the same way as from sync methods

## What's Next?

→ [Batch Requests](batch.md) - Multiple calls in one round-trip
