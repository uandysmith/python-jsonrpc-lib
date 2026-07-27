# Changelog

## 0.4.0

The release theme is **middleware that actually covers what it is mounted over**,
plus a strict pass over what the library accepts off the wire and puts back on it.

Most of this release comes out of an independent security review of 0.3.2 by
**ReinforcedAI**, whose findings arrived with a runnable proof of concept for
every claim — including one that turned an ordinary arithmetic comparison into a
bypass of any host-side bound check. Thank you.

### Breaking: group middleware moved to a new hook

`execute_method()` only ever ran on the group that owned the method, so a guard,
logger or limiter mounted above a subgroup never ran at all — the call reached the
method with no error and no log line. Cross-cutting logic now belongs in
`around_call()`, which runs for every group on the resolved path:

```python
class Unauthenticated(JSONRPCError):
    code = -32010
    message = 'Authentication required'

# 0.3.x - ran only if this group owned the method directly
class RequireAuthGroup(MethodGroup):
    def execute_method(self, method: Method, params: Any, context: AppContext) -> Any:
        if context.user_id is None:
            raise Unauthenticated()
        return super().execute_method(method, params, context)

# 0.4.0 - runs for every call into this group's subtree
class RequireAuthGroup(MethodGroup):
    def around_call(
        self, call: CallInfo, context: AppContext, call_next: Callable[[AppContext], Any]
    ) -> Any:
        if context.user_id is None:
            raise Unauthenticated()
        return call_next(context)

    async def around_call_async(
        self, call: CallInfo, context: AppContext, call_next: Callable[[AppContext], Awaitable[Any]]
    ) -> Any:
        if context.user_id is None:
            raise Unauthenticated()
        return await call_next(context)
```

Annotate `context`: as with `execute_method()`, that annotation is the group's
declared `context_type` and is checked at registration. `execute_method()` keeps
its exact signature and semantics — still the executor, still leaf-only — so a
group wrapping its own methods needs no change. Rename any `MethodGroup`
attribute of yours called `around_call`, `around_call_async` or `_owner`.

**Behaviour changes:**

- A group overriding `execute_method()` but owning only subgroups raises `TypeError` at registration: that hook could never run there. This is the arrangement that used to fail open silently
- Two wrappers on one path, one overriding only the synchronous hook and the other only the asynchronous one, are refused at mount: no entry point could run that chain without skipping one of them
- A group overriding only the synchronous hook of either pair refuses an async method below it — a synchronous wrapper cannot await the rest of the chain. Async dispatch used to pick the hook from the method's async-ness rather than from what the group overrode, so adding one `async def` to a guarded group silently disabled the guard for it
- `-32603` carries a bare `Internal error`; the exception text goes to the log only. `expose_internal_errors=True` restores the old behaviour. Never sanitized: `JSONRPCError` subclasses a method raises deliberately, and dispatch's own wiring diagnostics ("Method 'x' is async, use dispatch_async() instead"), which say only what the caller already knows
- `NaN`, `Infinity` and values overflowing to them are rejected both ways: `-32700` for the literal tokens, `-32602` for `1e400`, and serialization raises rather than emitting tokens no conforming parser accepts. `nan > limit` is `False`, so a host check written as `if amount > limit: raise` used to pass for `NaN`
- Byte input must be UTF-8; `json.loads` sniffs UTF-16 and UTF-32 for bytes
- A body that is not a JSON object is rejected with `-32600`. A body consisting of a JSON *string* containing a request used to be unwrapped and executed — below the `deserialize()` hook, so a replaced parser never saw it
- Params matching no variant of a union are rejected instead of reaching the method as the raw dict `json.loads` produced, which defeated unknown-key, per-field, required-field and `Literal` checks at once. Regression introduced in 0.3.2
- `MAX_NESTING_DEPTH` holds however the recursion is spelled. The guard's own exception was caught by the union handler directly above it, so `child: Node | None` accepted arbitrary depth
- A batch whose response cannot be serialized no longer collapses to one `id: null` error: serialization retries per entry, so the bad result becomes an error carrying its own id and every sibling keeps its receipt. The methods have already committed by then
- `Literal[1, 2, 3]` no longer accepts JSON `true` (`True == 1` in Python)
- Registering a group into itself, or into anything already below it, raises `ValueError`. The ownership guard asks whether a group is already mounted somewhere, and the outermost group of a tree is not — so registering it into one of its own descendants was accepted, and the resulting cycle in the owner chain made the next `register()` anywhere in that tree loop forever, at import time, with no error
- Registering one instance twice raises, in every shape. Ownership is recorded at registration rather than at RPC injection, so bottom-up trees are covered, and `register(None, group)` checks the target as well as the receiver — two instances could otherwise share one root method table
- A method's `context_type` is checked against the RPC's at mount time wherever it sits in the tree; the old check was vacuous inside any plain group
- `around_call()`'s `context` annotation is extracted as the group's `context_type`, like `execute_method()`'s
- Registering a `Method` subclass that never implemented `execute()` raises `TypeError`
- `validate_results=True` accepts a correct dataclass result and rejects a bare dict. It was checked with the rule for inbound params, so every dataclass return type failed on every call with `Expected return type 'Report', got 'Report'`. It still runs after `execute()` returns: it reports a contract violation, it cannot prevent one
- `validate_results=True` now checks a result dataclass's **fields**, and the fields of every dataclass inside a list or dict, not only the outer type. A dataclass enforces nothing at runtime — `Row(score='high')` is ordinary Python — so `isinstance()` answered "is this a Row", which is not the question the flag is named after, and a string went out under a schema promising `number`. The message names the path (`Expected return type 'float' at 'rows[1].score', got 'str'`) rather than repeating the outer type twice. A cyclic result is bounded by `MAX_NESTING_DEPTH` instead of recursing until the stack ends. Validating a 2000-row page costs about 1.9x the unvalidated response; the flag is off by default
- Refused requests are logged at `INFO` — that arm wrote nothing at any level, so enumeration and schema probing left no trace. Failed notifications moved from `DEBUG` to `WARNING`
- Error messages no longer interpolate the caller's payload, and type names no longer fall back to a repr carrying an address in memory
- The few messages that must quote what the caller sent — an unknown parameter, a method not found, a bad `jsonrpc` member — clip it at 128 characters and say how long it was. Otherwise the response size is a function of the request: a 900 KB parameter name produced a 1.8 MB answer, doubled by the copy in `error.data`. The request `id` is still echoed whole, as the spec requires
- `ClassVar` entries and the `KW_ONLY` sentinel are no longer settable parameters, and neither are `init=False` fields. Counting those broke a method three ways at once: the caller could not supply one, positional params bound to the wrong fields, and one without a default was demanded but impossible to satisfy
- A params dataclass with a field the validator cannot fill from JSON — `tuple`, `set`, `Enum`, `datetime`, `UUID`, `Decimal`, `bytes` — is refused at class definition, nested dataclasses included. Such a field used to register happily and then answer `-32602` to every call, blaming the caller's string for a type that would have refused everything. Declare the field as what arrives on the wire and convert it in `__post_init__`
- The same check covers `dict` keys, which are always strings in JSON however the annotation is spelled: `dict[int, str]`, `dict[float, str]`, `dict[Literal[1], str]` and six other shapes registered and then refused every populated payload. `dict[str, X]`, `dict[Any, X]`, `dict[int | str, X]` and `dict[Literal['a', 1], X]` are all still accepted — a string satisfies each of them
- `params: P | None` raises `TypeError` at class definition. The `| None` was quietly discarded, so the `if params is None` branch the author wrote could never run and a call with no params answered "Missing required parameters" against a signature that plainly permits their absence. Declare `params: P` and default the fields
- A params dataclass declaring an `InitVar` is refused at class definition. `fields()` does not report it, so the caller was told it was an unknown parameter while `__init__` required it anyway - the method could not be called by any route, and answered `-32603` to every request
- A rejection from a params dataclass's `__post_init__` becomes `-32602` instead of `-32603` plus a traceback per malformed request. `InvalidParamsError` carries its message and `data` to the caller — that is what to raise when you want yours read. A plain `ValueError` or `ArithmeticError` is also `-32602` but with a fixed message, its own text going to the log: `datetime.fromisoformat`, `int`, `Decimal`, `UUID` and `json.loads` all embed the caller's own string in the message they raise, and this is the channel the library documents as the way to validate. `AssertionError` and `TypeError` deliberately stay `-32603`: `assert` vanishes under `-O`, and at that exact call `TypeError` is how a mis-built params dataclass announces itself, so mapping it would blame the caller for the server
- A union that matches no variant now reports why each one was rejected, instead of only that none matched
- `MAX_NESTING_DEPTH` is enforced against the payload rather than the annotation. A field typed `list` or `Any` has no type arguments, so the walk never descended and never counted a level - any depth got through
- An `int` sent for a `float` field arrives as a `float`. `int.is_integer()` only exists from 3.12, so on 3.11 the obvious call on a "float" raised `AttributeError`
- Two dataclasses sharing a class name no longer share one OpenAPI schema. The second used to reuse the first's, so one of the two methods was documented with the other's fields; the loser now gets its module path prefixed
- A dataclass without its own docstring no longer carries `@dataclass`'s generated one into the spec, where `Outer(inner: __main__.Inner)` read like a leak
- `-32601` no longer names internal groups, including the root as the string `'None'`. The detail goes to the log
- `e.code` and `e.message` report the values the error was raised with. They were class attributes shadowed by private ones, so `ServerError('boom', code=-32050)` read back as `-32000 'Server error'` - the obvious handler logged the wrong thing
- A registration refused mid-way leaves the subtree reusable. Mounting attached the rpc reference on the way down and validated as it went, so a rejected subtree kept the attribute, and the ownership guard then refused to register it anywhere else - permanently
- The default `max_concurrent` for an async batch is 64 rather than `os.cpu_count()`. A coroutine awaiting a socket consumes no CPU, so the core count never bounded anything meaningful; on a 4-core machine it turned a 100-call I/O batch into 25 sequential rounds
- New `max_request_size`, default 1 MiB, checked on the raw body before it is parsed. `max_batch` counted requests and nothing bounded how large one could be — and a single request is not a batch, so `max_batch` never applied to it at all: 16.9 MB of integers cost 6.9 seconds of solid CPU and 90 MB of heap, which under `handle_async()` is the whole event loop, because nothing on the validation path awaits. `max_batch_size` applies a second, tighter limit to batch bodies only, for a host that raises the first for one large method. Both take `-1` for unlimited

- `around_call()` / `around_call_async()` on `MethodGroup`, above. `CallInfo` — exported from the package root — carries `path`, `method`, validated `params` and the request `id` to correlate log lines with; passing a different context to `call_next()` enriches what the rest of the chain sees
- `-32602` fills in the spec's `data` field: `{"reason": "type_mismatch", "parameter": "age", "expected": "int", "received": "str"}`. Finding out which argument to fix meant parsing an English sentence, which any rewording broke. Every value there already appears in the message, and the rejected value itself is still never echoed
- Inheritance-aware type extraction: it runs only for classes defining their own `execute()`. A class that does not is an intermediate base carrying shared domain logic, inheriting the extracted attributes normally; previously any such class died at class-definition time. Template methods work too, and the same rule applies to `MethodGroup` subclasses without their own `execute_method()`
- `expose_internal_errors` constructor flag

**Protocol compliance:**

- An error response now echoes the request's id. A malformed `method` or `params` is not the "error in detecting the id" the spec allows null for - the id reads fine - and a client tracking calls by id could not match the answer to the call, so it waited for one that had already arrived. Two failing entries in a batch used to come back as two indistinguishable `id: null` objects
- Batch entries must carry `"jsonrpc": "2.0"`. Batching exists only in 2.0, and a 1.0-framed entry was answered in 1.0 framing, producing one array holding two response shapes. Single requests still accept 1.0 framing, which is what the spec's compatibility note recommends
- A 1.0 request whose `id` is null is a notification and gets no response. The two halves of the package disagreed: `build_notification(version='1.0')` produced exactly that shape and the server answered it
- A body nested deeply enough to exhaust the parser's stack answers `-32700` instead of `-32603`. `RecursionError` is a `RuntimeError`, so it slipped past the parse handler and logged a traceback per request

**Performance:**

- Dispatch resolution is memoized per path, and encoding and decoding reuse one encoder and one decoder instead of building them per call
- Result serialization walks the structure directly instead of calling `dataclasses.asdict()`, which copies every non-atomic leaf - waste, since the result goes straight into `json.dumps()` and is discarded
- Params are checked and converted in a single descent. The two passes walked the whole value twice - on a list of a few thousand objects that was most of the request - and scalar fields now settle before any of the annotation machinery is consulted. A payload that fails validation is re-checked at the top level only, so a request with several faults at once still earns the complaint it always did
- The dataclass introspection caches are keyed weakly, so a params type generated at runtime - which `@rpc.method` does for every decorated function - no longer lives for the life of the process

Measured against the state before these fixes: the same 2000-row payload is 21% faster as a result and 53% faster as params, a nested-dataclass call 38% faster, batches of 100 about 36% faster, internal `call_method` 42% faster, and a 100-call I/O batch 90% faster (the `max_concurrent` default above).

**Bug fixes:**

- `parse_response()` rejected the server's own reply to a request carrying `"id": null`. That is a request, not a notification; an absent id is still an error
- A return annotation the checker cannot handle (`tuple[int, ...]`, `set[int]`) was reported as `-32602`, blaming the caller for the server's return type. Now `-32001`
- A field annotated `Any` accepts `null`; it used to be refused with the self-contradicting `expected type 'Any', got 'NoneType'`
- An integer too large to become a float is `-32602`, not `-32603`. `float(10**400)` raises `OverflowError`, which is an `ArithmeticError` and so was caught by nothing on the validation path: a 420-byte body from an unauthenticated caller produced `Internal error` plus a full traceback at ERROR, once per request, wherever a `float` appeared — directly, in a list, in a dict, under a union. The same value returned by a method declared `-> float` used to pass `validate_results=True` and go out as a 400-digit integer while the schema promised `number`
- A `float` field refusing a non-finite value says so. Both it and the out-of-range integer used to report `expected type 'float', got 'float'` and `got 'int'` — the first self-contradicting, the second the opposite of the truth
- A union whose variants all simply have the wrong shape reports the field and the whole union, as it always did, rather than listing each variant saying "not me". Variants that failed for a substantive reason still report it
- The "method not found" message named the root group as `None`
- `generate_json()` no longer hands the spec to `serialize()`. That hook is documented as overridable with the narrower `def serialize(self, data)` and with libraries such as orjson that have no `indent` keyword, so every such override raised `TypeError` from inside the library. A spec is not a response
- Unregistering every root-level method lets `register(None, group)` succeed. The refusal was remembered rather than rechecked, so it kept firing — telling the caller to clear methods they had already cleared, with no way to satisfy it short of a new `JSONRPC`

## 0.3.2

**Bug fixes:**

- `add_security_scheme`: replaced `**kwargs` with `options: dict` parameter, fixing inability to create `apiKey` schemes (conflicting `name` parameter, `in` as Python reserved word)
- `_convert_value`: Union types containing multiple dataclasses now correctly try all variants instead of crashing on the first mismatch
- `simplify_id` flag now consistently applies to JSONRPCError schema in OpenAPI output
- `unregister()` now clears `.rpc` attribute, allowing re-registration of the same Method instance
- `max_concurrent` parameter is now validated (`-1` or `>= 1`); previously `0` caused a silent deadlock
- `version` parameter is now validated at init; invalid values like `'3.0'` raise `ValueError`
- Fixed `bearer_format` typo in tests (should be `bearerFormat` per OpenAPI spec)
- Fixed OpenAPI tutorial example to match actual generated output

## 0.3.1 (First Public Release)

- JSON-RPC 1.0 and 2.0 support
- Dataclass-based parameter validation
- Built-in OpenAPI generation
- Hierarchical context support
- Decorator API for prototyping
- Async/sync methods
- Batch request handling
- Strict mode by default
- Zero external dependencies
