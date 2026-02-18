# Protocol Versions

JSON-RPC is a small protocol — the [2.0 specification](https://www.jsonrpc.org/specification) fits on two pages. That simplicity is a feature: the protocol is easy to implement correctly, and correctness matters at integration boundaries.

`python-jsonrpc-lib` is **strict by default**. This means it generates responses exactly as the spec defines them, and it rejects requests that the spec would call invalid. The reasoning: a standard-compliant client should just work, and a client that sends non-standard requests should know about it rather than receive silently incorrect behavior.

Permissive mode is always available — but it's an explicit opt-in, not the default.

## Why Strict Compliance?

Most JSON-RPC libraries are "close enough" — they produce something that looks like JSON-RPC but cut corners on the spec. This is fine until it isn't: a client library, a proxy, or a spec-compliant test tool hits an edge case and gets unexpected behavior.

Strict compliance gives three concrete benefits:

1. **Any standard client works out of the box** — no special configuration needed for the client side
2. **Protocol errors surface early** — a client sending wrong params format gets `-32600` immediately, not a confusing application error later
3. **Interoperability across tooling** — API gateways, proxies, test runners, OpenAPI validators all speak the spec

Picking v1.0 or v2.0 is a commitment to that version's wire format. The defaults enforce it.

## JSON-RPC 1.0 vs 2.0

The two versions are not compatible. They have different param formats, different response structures, and different feature sets. Choose at initialization — don't mix.

| Feature | v1.0 | v2.0 |
|---------|------|------|
| Params format | Array only: `[1, 2]` | Object only: `{"a": 1}` |
| Batch requests | Not in spec | Supported |
| Notifications | Not supported | No `id` field |
| `jsonrpc` field | Absent | Required `"2.0"` |
| `error` field | Always present (null on success) | Only on error |
| `result` field | Always present (null on error) | Only on success |

**Use v2.0 for new projects.** The spec is cleaner: responses only carry what's relevant, object params match dataclasses naturally, and batch + notifications are available when needed.

**Use v1.0 only when an existing client requires it** — legacy systems, embedded devices, third-party integrations that haven't migrated.

## JSON-RPC 1.0 Wire Format

v1.0 always sends both `result` and `error` in every response. The spec says the unused field must be `null`. This means every response carries extra bytes and parsers must check which field is populated.

```json title="v1_request.json"
{
  "method": "add",
  "params": [1, 2],
  "id": 1
}
```

```json title="v1_response.json"
{
  "result": 3,
  "error": null,
  "id": 1
}
```

```json title="v1_error.json"
{
  "result": null,
  "error": {"code": -32601, "message": "Method not found"},
  "id": 1
}
```

Note: v1.0 params are positional arrays. `[1, 2]` maps to the first two dataclass fields in declaration order.

## JSON-RPC 2.0 Wire Format

v2.0 fixes the null ambiguity: responses carry either `result` or `error`, never both. The `jsonrpc: "2.0"` field lets any parser identify the version immediately.

```json title="v2_request.json"
{
  "jsonrpc": "2.0",
  "method": "add",
  "params": {"a": 1, "b": 2},
  "id": 1
}
```

```json title="v2_response.json"
{
  "jsonrpc": "2.0",
  "result": 3,
  "id": 1
}
```

```json title="v2_error.json"
{
  "jsonrpc": "2.0",
  "error": {"code": -32601, "message": "Method not found"},
  "id": 1
}
```

## Setting Up Protocol Versions

Both versions use the same `Method` and `MethodGroup` API. The version affects only the wire format, not the application code.

```python title="versions.py"
from jsonrpc import JSONRPC

# JSON-RPC 1.0 (strict defaults)
rpc_v1 = JSONRPC(version='1.0')
# - allow_batch=False       (not in spec)
# - allow_dict_params=False (spec requires arrays)
# - allow_list_params=True  (arrays required)

# JSON-RPC 2.0 (spec-compliant defaults)
rpc_v2 = JSONRPC(version='2.0')
# - allow_batch=True         (part of spec)
# - allow_dict_params=True   (objects allowed)
# - allow_list_params=True   (arrays allowed)
```

## Permissive Mode

Sometimes you need to accept non-standard requests — for example, a v1.0 client that sends dict params or needs batch support. Override individual constraints without switching versions.

```python title="permissive.py"
# v1.0 accepting dict params (non-standard, but some clients send them)
rpc = JSONRPC(version='1.0', allow_dict_params=True)

# v1.0 with batch (non-standard extension)
rpc = JSONRPC(version='1.0', allow_batch=True)

# v2.0 restricting to object-only params (stricter than spec)
rpc = JSONRPC(version='2.0', allow_list_params=False)
```

For v1.0, permissive mode is intentionally opt-in — it hides protocol mismatches that are better caught early. For v2.0, both parameter formats are allowed by default per specification.

## Method Definition for v1.0

Methods look identical for both versions. The library handles the param mapping — positional array for v1.0 is matched to dataclass fields by order.

```python title="v1_method.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class AddParams:
    a: int
    b: int

class Add(Method):
    def execute(self, params: AddParams) -> int:
        return params.a + params.b

rpc = JSONRPC(version='1.0')
rpc.register('add', Add())
```

**v1.0 request (array params — `a=10`, `b=5` by position):**

```json title="v1_add_request.json"
{
  "method": "add",
  "params": [10, 5],
  "id": 1
}
```

```json title="v1_add_response.json"
{
  "result": 15,
  "error": null,
  "id": 1
}
```

## Notifications (v2.0 only)

A notification is a request without an `id` field. The server executes the method but sends no response. Useful for fire-and-forget logging, analytics, or event publishing where the caller doesn't need confirmation.

```json title="notification.json"
{
  "jsonrpc": "2.0",
  "method": "log",
  "params": {"message": "User logged in"}
}
```

No response is sent.

!!! note "`id: null` is NOT a notification"
    A request with `"id": null` gets a response. Only a **missing** `id` field is a notification.
    This is a common source of bugs in custom JSON-RPC parsers — `python-jsonrpc-lib` follows the spec precisely.

```json title="null_id_gets_response.json"
{"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": null}
→ {"jsonrpc": "2.0", "result": 3, "id": null}
```

## Error Codes

The spec reserves a range of error codes. `python-jsonrpc-lib` maps them exactly:

| Code | Name | When it fires |
|------|------|---------------|
| `-32700` | Parse error | Request is not valid JSON |
| `-32600` | Invalid request | JSON is valid, but not a valid JSON-RPC request |
| `-32601` | Method not found | Named method is not registered |
| `-32602` | Invalid params | Parameter type validation failed |
| `-32603` | Internal error | Unhandled exception in method |
| `-32001` | Invalid result | Return value doesn't match declared return type |
| `-32000` to `-32099` | Server errors | Available for application-defined errors |

## Custom Error Codes

Use `ServerError` with a custom code in the reserved range `-32000` to `-32099`:

```python title="custom_errors.py"
from dataclasses import dataclass
from jsonrpc import Method
from jsonrpc.errors import ServerError, InvalidParamsError

@dataclass
class Params:
    email: str

@dataclass
class Result:
    accepted: bool

class Subscribe(Method):
    def execute(self, params: Params) -> Result:
        if '@' not in params.email:
            raise InvalidParamsError("Invalid email format")  # -32602

        if not self._send(params.email):
            raise ServerError("Mail server unavailable", code=-32010)  # custom

        return Result(accepted=True)

    def _send(self, email: str) -> bool:
        return True  # stub
```

**Custom error response:**

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32010,
    "message": "Mail server unavailable"
  },
  "id": 1
}
```

## Key Points

- **v2.0 for new projects** — cleaner wire format, batch, notifications
- **v1.0 only when required** — legacy clients that can't be changed
- **Strict by default** — spec compliance catches mismatches early, ensures interoperability with any standard client
- **Permissive opt-in** — relax individual constraints for legacy compatibility, but knowingly
- **Notifications** — v2.0 only; `id: null` is not a notification, it gets a response

## What's Next?

→ [Middleware](middleware.md) - Custom MethodGroup behavior
