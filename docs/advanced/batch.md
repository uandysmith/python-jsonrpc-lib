# Batch Requests

!!! note "JSON-RPC 2.0 Feature"
    Batch requests are only supported in JSON-RPC 2.0 by default.

A batch request is a JSON array of multiple JSON-RPC requests sent in a single HTTP call. The server processes all of them and returns an array of responses. This reduces round-trip overhead when you need to call several methods at once.

With `rpc.handle_async()`, async methods in a batch run **concurrently** — the total time is the slowest method, not the sum.

## Basic Batch

Send an array, get an array back. Each response has the same `id` as its request, so the client can match them regardless of order.

```json title="batch_request.json"
[
  {"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": 1},
  {"jsonrpc": "2.0", "method": "add", "params": {"a": 5, "b": 3}, "id": 2},
  {"jsonrpc": "2.0", "method": "greet", "params": {"name": "World"}, "id": 3}
]
```

```json title="batch_response.json"
[
  {"jsonrpc": "2.0", "result": 3, "id": 1},
  {"jsonrpc": "2.0", "result": 8, "id": 2},
  {"jsonrpc": "2.0", "result": "Hello, World!", "id": 3}
]
```

## Setup

No special configuration needed for batch — it's enabled by default in v2.0. Register methods normally; `rpc.handle()` detects the array and processes each item.

```python title="batch_setup.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class AddParams:
    a: int
    b: int

@dataclass
class GreetParams:
    name: str
    greeting: str = "Hello"

class Add(Method):
    def execute(self, params: AddParams) -> int:
        return params.a + params.b

class Greet(Method):
    def execute(self, params: GreetParams) -> str:
        return f"{params.greeting}, {params.name}!"

rpc = JSONRPC(version='2.0')
rpc.register('add', Add())
rpc.register('greet', Greet())

# Handle batch — same rpc.handle() call, no special setup
batch_json = '''[
  {"jsonrpc": "2.0", "method": "add", "params": {"a": 10, "b": 5}, "id": 1},
  {"jsonrpc": "2.0", "method": "greet", "params": {"name": "Alice"}, "id": 2}
]'''

response = rpc.handle(batch_json)
# '[{"jsonrpc": "2.0", "result": 15, "id": 1}, {"jsonrpc": "2.0", "result": "Hello, Alice!", "id": 2}]'
```

## Mixed Requests and Notifications

A batch can mix regular requests (with `id`) and notifications (without `id`). Notifications are executed but produce no response entry — the response array only contains results for requests that had an `id`.

```json title="mixed_batch.json"
[
  {"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": 1},
  {"jsonrpc": "2.0", "method": "log", "params": {"message": "batch started"}},
  {"jsonrpc": "2.0", "method": "add", "params": {"a": 5, "b": 3}, "id": 2}
]
```

The `log` notification executes but doesn't appear in the response:

```json title="mixed_batch_response.json"
[
  {"jsonrpc": "2.0", "result": 3, "id": 1},
  {"jsonrpc": "2.0", "result": 8, "id": 2}
]
```

## Error Handling in Batch

Each request in a batch is independent. One failure doesn't cancel the others — the response array includes both successful results and errors.

```json title="batch_with_error.json"
[
  {"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": 1},
  {"jsonrpc": "2.0", "method": "nonexistent", "params": {}, "id": 2},
  {"jsonrpc": "2.0", "method": "add", "params": {"a": "not_int", "b": 3}, "id": 3}
]
```

```json title="batch_error_response.json"
[
  {"jsonrpc": "2.0", "result": 3, "id": 1},
  {"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found: nonexistent"}, "id": 2},
  {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Parameter 'a' expected type 'int', got 'str'"}, "id": 3}
]
```

Isolation extends to serialization. If one method returns a value the serializer
cannot encode, only that entry becomes a `-32603` — carrying its own `id` — and
every sibling keeps its result. This matters because the methods in a batch have
already run by then: a response that dropped the siblings' receipts would leave
the client with retrying as its only move, re-executing everything that already
committed.

!!! note "A batch of only notifications returns `None`"
    Notifications produce no response entries, so a batch containing nothing else
    produces no response at all. Check the return value, as always.

## Async Batch (Concurrent Execution)

With `handle_async()`, all async methods in the batch run concurrently via `asyncio.gather`. Three database calls that each take 100ms finish in ~100ms total rather than 300ms.

```python title="async_batch.py"
import asyncio
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class FetchParams:
    user_id: int

@dataclass
class UserResult:
    user_id: int
    name: str

class FetchUser(Method):
    async def execute(self, params: FetchParams) -> UserResult:
        await asyncio.sleep(0.1)  # Simulate DB call
        return UserResult(user_id=params.user_id, name=f"User {params.user_id}")

rpc = JSONRPC(version='2.0')
rpc.register('get_user', FetchUser())

batch = '''[
  {"jsonrpc": "2.0", "method": "get_user", "params": {"user_id": 1}, "id": 1},
  {"jsonrpc": "2.0", "method": "get_user", "params": {"user_id": 2}, "id": 2},
  {"jsonrpc": "2.0", "method": "get_user", "params": {"user_id": 3}, "id": 3}
]'''

# Executes all 3 concurrently — takes ~0.1s instead of 0.3s
response = await rpc.handle_async(batch)
```

## Configuring Batch Support

Batch is on by default for v2.0 and off for v1.0. Both can be overridden:

```python title="batch_config.py"
batch_request = '''[
  {"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": 1},
  {"jsonrpc": "2.0", "method": "add", "params": {"a": 3, "b": 4}, "id": 2}
]'''

# v2.0 — batch enabled by default
rpc_v2 = JSONRPC(version='2.0')
rpc_v2.handle(batch_request)  # Works

# v1.0 — batch disabled by default (not part of the v1.0 spec)
rpc_v1 = JSONRPC(version='1.0')
rpc_v1.handle(batch_request)  # Returns error -32600

# Enable batch for v1.0 (non-standard extension)
rpc_v1_permissive = JSONRPC(version='1.0', allow_batch=True)
rpc_v1_permissive.handle(batch_request)  # Works

# Disable batch for v2.0 (e.g. to limit abuse surface)
rpc_v2_no_batch = JSONRPC(version='2.0', allow_batch=False)
rpc_v2_no_batch.handle(batch_request)  # Returns error -32600
```

**Error when batch is disabled:**

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32600,
    "message": "Invalid Request: Batch requests not allowed"
  },
  "id": null
}
```

## Batch Size and Concurrency Limits

```python title="batch_limits.py"
rpc = JSONRPC(
    version='2.0',
    max_batch=50,             # Reject batches of more than 50 items (default: 100, -1 = unlimited)
    max_request_size=262144,  # Reject bodies over 256 KiB (default: 1 MiB, -1 = unlimited)
    max_batch_size=131072,    # ...and batch bodies over 128 KiB (default: -1, i.e. only the above)
    max_concurrent=8,         # Max concurrent coroutines in async batch (default: 64, -1 = unlimited)
)
```

**`max_batch` counts requests. It says nothing about how large they are** — and
neither did anything else before 0.4.0, so a hundred-item batch of 12.9 MB was
accepted by the defaults, and a *single* request is not a batch at all, so
`max_batch` never applied to it: 16.9 MB of integers cost 6.9 seconds of solid
CPU and 90 MB of heap. Under `handle_async()` that is the whole event loop, since
nothing on the validation path awaits.

**`max_request_size`** is the barrier for that, and it is checked on the raw body
before anything parses it — the parse is where the cost starts, so a body this
server will not serve never becomes objects. The response is `-32600` with
`id: null`, the one case the spec sanctions a null id, since nothing has read the
request yet.

**`max_batch_size`** applies the same check only when the body is a batch. It
exists for the host that raises `max_request_size` because one method
legitimately receives a large document, and does not want that headroom
multiplied by `max_batch`.

!!! note "This is a floor, not a substitute for the transport"
    Your web server should reject an oversized body before your process reads
    it: `client_max_body_size` in nginx, `MAX_CONTENT_LENGTH` in Flask,
    `client_max_size` in aiohttp. The limit here exists because this is the layer
    that knows the params are a list of two million things, and because
    `rpc.handle()` is also called from queue consumers and socket servers that
    have no such setting.

**`max_batch`** caps the total number of requests accepted in a single batch call. When exceeded, the entire batch is rejected with `-32600 Invalid Request` before any method executes.

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32600,
    "message": "Invalid Request: Batch too large: 51 requests, maximum is 50"
  },
  "id": null
}
```

**`max_concurrent`** throttles how many async method calls run simultaneously inside `handle_async()`. Without a limit, a batch of 100 items would launch 100 coroutines at once — overwhelming connection pools and downstream services.

The default is **64**: below `max_batch` so the limiter actually does something, and above what typical client pools hold (httpx 10, aiohttp 100, asyncpg 10–20) so one batch does not stall on it. It is deliberately not derived from the CPU count — a coroutine waiting on a socket uses no CPU, so cores say nothing about how many can wait at once.

Set it to match the pool the methods actually use:

```python title="concurrency_limit.py"
import asyncio
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class FetchParams:
    user_id: int

class FetchUser(Method):
    async def execute(self, params: FetchParams) -> dict:
        await asyncio.sleep(0.05)  # Simulated DB call
        return {'id': params.user_id, 'name': f'User {params.user_id}'}

# Limit to 4 concurrent DB calls regardless of batch size
rpc = JSONRPC(version='2.0', max_concurrent=4)
rpc.register('get_user', FetchUser())
```

`max_concurrent` only applies to `handle_async()`. Synchronous `handle()` is unaffected.

## Client-Side Batch

A simple helper for building and sending batch requests from a Python client:

```python title="batch_client.py"
import requests
import json

def batch_rpc(url: str, calls: list[dict]) -> list[dict]:
    response = requests.post(url, json=calls)
    return response.json()

# Build batch
batch = [
    {"jsonrpc": "2.0", "method": "add", "params": {"a": i, "b": i}, "id": i}
    for i in range(1, 6)
]

# Execute batch — one HTTP call for 5 operations
results = batch_rpc("http://localhost:5000/rpc", batch)
for r in results:
    print(f"id={r['id']}: {r['result']}")
```

## Key Points

- **v2.0 only** by default — explicitly enable for v1.0 if needed
- **Async batch** runs concurrently via `asyncio.gather` — time scales with the slowest item, not the sum
- **Each request independent** — errors in one item don't affect others, including serialization errors
- **Notifications** don't produce response entries
- **No special endpoint** — same `rpc.handle()` / `rpc.handle_async()` call
- **`max_batch=100`** — batches of more than this many requests are rejected with `-32600` before execution
- **`max_request_size=1 MiB`** — larger bodies are rejected with `-32600` before they are parsed; `max_batch` bounds the count, this bounds the volume
- **`max_concurrent=64`** — limits simultaneous coroutines in async batch; use `-1` to disable

## What's Next?

→ [Protocol Versions](protocols.md) - JSON-RPC 1.0 vs 2.0 differences
