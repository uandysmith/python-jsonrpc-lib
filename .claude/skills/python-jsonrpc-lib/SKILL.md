---
name: python-jsonrpc-lib
description: How to write methods and method groups with python-jsonrpc-lib — Method classes and their typed signatures, sharing business logic through base classes, MethodGroup namespaces, around_call middleware, typed context, the validation rules that fail closed, and getting a correct OpenAPI spec out of the annotations. Use this skill whenever the code imports `jsonrpc` (JSONRPC, Method, MethodGroup, CallInfo), whenever the user mentions JSON-RPC methods, RPC groups, RPC middleware, notifications or batch requests in Python, and whenever they ask to add or restructure such a method — even if they never name the library. Params, results and context are all validated from annotations and the library fails closed in several places that are easy to get wrong from memory, so consult this rather than guessing.
user-invocable: false
---

# python-jsonrpc-lib

Transport-agnostic JSON-RPC 1.0/2.0. Methods are classes with typed signatures;
the library validates params against those annotations, routes by dotted path,
and returns a JSON string. It never touches HTTP — feeding it a request body and
sending its answer back is the host's job, and takes a few lines in any
framework.

The design bet is that **annotations are the contract**. Params types, result
types and context types are all extracted from `execute()` signatures and then
enforced at runtime and published as OpenAPI. An annotation that is vague is not
a style problem: it is a request that goes unvalidated and an endpoint that goes
undocumented.

Two things are worth getting right every time, and this guide is organized around
them: **validation that actually validates**, and **a spec that actually
describes the method**. Both come from the same annotations.

## The shape of a server

```python title="server.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method, MethodGroup

@dataclass
class OperandsParams:
    """The two operands of a binary arithmetic operation."""

    a: int
    b: int

@dataclass
class MathResult:
    """The outcome of an arithmetic operation, and which one produced it."""

    operation: str
    result: int

class Add(Method):
    """Add two numbers."""

    def execute(self, params: OperandsParams) -> MathResult:
        return MathResult(operation='add', result=params.a + params.b)

class Subtract(Method):
    """Subtract the second number from the first."""

    def execute(self, params: OperandsParams) -> MathResult:
        return MathResult(operation='subtract', result=params.a - params.b)

math = MethodGroup()
math.register('add', Add())               # an instance, never the class
math.register('subtract', Subtract())     # the same params and result types

rpc = JSONRPC(version='2.0')
rpc.register('math', math)                # the methods are now "math.add", "math.subtract"

response = rpc.handle('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}')
# '{"jsonrpc": "2.0", "result": {"operation": "add", "result": 3}, "id": 1}'
```

Both methods share one params type and one result type, so the family stays
consistent on the wire and the generated spec references a single `MathResult`
schema from both operations. That is the usual shape: params and result types
belong to a domain, not to a single method.

`@rpc.method` also exists, but it supports no context, no groups and no
middleware — anything outliving the afternoon gets rewritten as a subclass.

## Writing a method

`execute()` has a fixed shape, checked at class-definition time:

- `params` is the second parameter and is named exactly `params`
- its annotation is a **dataclass**, or `None` for a method that takes nothing
- the return annotation is required
- an optional third parameter `context: YourContext` carries per-request data

```python
# Rejected at class definition: params must be a dataclass
class Bad(Method):
    def execute(self, params: dict) -> dict: ...

# Rejected at class definition: no return annotation
class AlsoBad(Method):
    def execute(self, params: SomeParams): ...
```

### Return a dataclass, not a primitive or a dict

A dataclass return type is what makes the method self-describing. The same logic,
annotated two ways, produces very different specs:

```python
class MultiplyUntyped(Method):
    """Multiply two numbers."""

    def execute(self, params: OperandsParams) -> dict:
        return {'result': params.a * params.b}      # spec: {} - documents nothing

class Multiply(Method):
    """Multiply two numbers."""

    def execute(self, params: OperandsParams) -> MathResult:
        return MathResult(operation='multiply', result=params.a * params.b)   # spec: a full $ref
```

An unsupported annotation such as `-> datetime` is worse than undocumented: the
value is not JSON-serializable either, so the call answers `-32603` at runtime.
Keep result fields JSON-native and convert at the boundary — a `taken_at: str`
holding ISO-8601 rather than a leaked `datetime`. If custom types on the wire are
genuinely required, override `serialize_result()` on the `JSONRPC` subclass; the
dataclass is still what OpenAPI reads.

Even a single scalar deserves a wrapper. `-> int` tells a caller nothing about
what the integer means, and gives the spec one bare `{"type": "integer"}`.

### Params dataclasses

Field **order is part of the wire contract**: a caller may send params as a JSON
array, and positions map to fields in declaration order. Trailing fields with
defaults may be omitted; sending more values than there are fields is `-32602`.

```python
@dataclass
class SearchParams:
    """Free-text search over the catalogue."""

    query: str
    limit: int = 20                                  # optional in the spec too
    tags: list[str] = field(default_factory=list)    # mutable default needs a factory
```

Annotations that params and results may use:

| Annotation | JSON | Notes |
|---|---|---|
| `int` | number | `true` is not an int |
| `float` | number | ints accepted, unless too large to be one; `NaN`/`Infinity` refused |
| `str`, `bool` | string, boolean | |
| `list[T]` | array | every item validated |
| `dict[str, T]` | object | every value validated |
| a dataclass | object | validated recursively, nested to any depth |
| `T \| None` | T or null | |
| `T \| U` | either | the value must match a variant |
| `Literal['a', 'b']` | enum | type-strict: `1` does not satisfy `Literal[True]` |
| `Any` | anything | no validation, and an empty OpenAPI schema |
| `None` | — | the method takes no params |

`ClassVar` fields and the `KW_ONLY` sentinel are not parameters — declare
constants and keyword-only markers freely.

Anything outside that table is refused **when the method class is defined**, not
at call time: `tuple`, `set`, `Enum`, `datetime`, `date`, `UUID`, `Decimal`,
`bytes`, and a `dict` whose *key* type no string can satisfy (`dict[int, str]` —
JSON object keys are always strings; `dict[Any, T]`, `dict[int | str, T]` and
`dict[Literal['a', 'b'], T]` are all fine). `params: P | None` is refused too:
the library always builds the dataclass, so `None` never reaches `execute()` and
that branch would be dead — declare `params: P` and default every field if the
method should be callable with no params.

Take the wire type and convert it in `__post_init__`, on an `init=False` field:

```python
@dataclass
class BookingParams:
    """A booking for a given instant."""

    starts_at: str                          # ISO-8601 - what the caller sends
    when: datetime = field(init=False)      # init=False, so not a parameter

    def __post_init__(self):
        try:
            self.when = datetime.fromisoformat(self.starts_at)
        except ValueError:
            raise InvalidParamsError('starts_at must be an ISO-8601 timestamp') from None
```

**Raise `InvalidParamsError`, not `ValueError`.** Both are `-32602`, but only the
first carries its message to the caller. A bare `ValueError` gets a fixed message
and its own text goes to the log, because the `ValueError` raised here is usually
not one you wrote: `fromisoformat`, `int`, `Decimal`, `UUID` and `json.loads` all
embed the caller's own string in the message they raise.

### Calling other methods

`self.rpc` is injected at registration, so a method can compose others by path.
Internal calls go through the same middleware chain as inbound requests, so
guards apply to them too:

```python
class Checkout(Method):
    """Reserve stock, then charge."""

    def execute(self, params: CheckoutParams, context: AppContext) -> CheckoutResult:
        reservation = self.rpc.call_method('stock.reserve', {'sku': params.sku}, context=context)
        return CheckoutResult(order_id=self.charge(reservation), status='paid')
```

Use `call_method_async` from an `async def execute`.

## Sharing business logic

Three patterns, in increasing order of how much they share.

### A domain base with helpers

Type extraction runs only for the class that defines `execute()`. A class that
does not define one is an abstract base: it carries shared logic, and registering
it raises `TypeError` — bases are for inheriting, not for mounting.

```python title="domain_base.py"
class BillingMethod(Method):
    """Shared machinery for every billing method. No execute() - not mountable."""

    def load_account(self, context: AppContext) -> Account:
        account = accounts.get(context.user_id)
        if account is None:
            raise InvalidParamsError('No such account')
        return account

    def assert_not_frozen(self, account: Account) -> None:
        if account.frozen:
            raise InvalidParamsError('Account is frozen')

class Refund(BillingMethod):
    """Refund a settled charge."""

    def execute(self, params: RefundParams, context: AppContext) -> RefundResult:
        account = self.load_account(context)
        self.assert_not_frozen(account)
        return RefundResult(refund_id=refund(account, params.charge_id), status='pending')
```

### A template method that owns the contract

When a family of methods differs only in the middle, put `execute()` and the
params/result contract in the base and leave the subclasses a hook. They inherit
the extracted types, so the whole family validates and documents identically:

```python title="template_method.py"
@dataclass
class PageParams:
    """Offset pagination."""

    offset: int = 0
    limit: int = 50

@dataclass
class PageResult:
    """One page of rows."""

    rows: list[Row]
    total: int

class ListMethod(Method):
    """Base for every paginated listing."""

    def execute(self, params: PageParams) -> PageResult:
        rows, total = self.query(params.offset, params.limit)
        return PageResult(rows=[self.to_row(record) for record in rows], total=total)

    def query(self, offset: int, limit: int) -> tuple[list[Any], int]:
        raise NotImplementedError

    def to_row(self, record: Any) -> Row:
        return Row(id=record.id, label=str(record))

class ListOrders(ListMethod):
    """List orders, newest first."""

    def query(self, offset: int, limit: int) -> tuple[list[Any], int]:
        return orders.page(offset, limit), orders.count()
```

### Dependencies through the constructor

Methods are built once, at assembly time, so services belong in `__init__` —
`context` is for per-request data, not for wiring. This also lets one class serve
several registrations:

```python
class Report(Method):
    """Produce a report over the caller's chosen scope."""

    def __init__(self, store: ReportStore, scope: str) -> None:
        super().__init__()
        self.store = store
        self.scope = scope

    def execute(self, params: ReportParams, context: AppContext) -> ReportResult:
        return ReportResult(rows=self.store.rows(self.scope, context.user_id))

reports = MethodGroup()
reports.register('mine', Report(store, scope='mine'))
reports.register('everyone', Report(store, scope='everyone'))
```

Instances are singletons shared across every request and thread, so anything
per-request stays in locals or in `context`, never on `self`. One instance
belongs to exactly one registration — registering it twice raises `ValueError`;
`unregister('name')` releases it.

## Groups and namespaces

`MethodGroup()` takes no arguments; the name is given at registration, and the
dotted path follows the tree. Names cannot contain `.` — that is what nesting is
for.

```python
admin = MethodGroup()
users = MethodGroup()
users.register('create', CreateUser())
admin.register('users', users)
rpc.register('admin', admin)
# "admin.users.create"
```

## Middleware

Two hooks, and choosing the wrong one is the most consequential mistake
available here:

| Hook | Runs on |
|---|---|
| `around_call()` | **every group on the resolved path**, outermost first |
| `execute_method()` | **only the group the method is registered on** |

Anything that must cover a namespace — a guard, logging, rate limiting, caching —
belongs in `around_call()`. `execute_method()` is the executor; override it only
to change how a group invokes the methods it owns directly.

```python title="middleware.py"
from collections.abc import Awaitable, Callable
from typing import Any
from jsonrpc import CallInfo, MethodGroup
from jsonrpc.errors import JSONRPCError

class Unauthenticated(JSONRPCError):
    """-32000..-32099 is reserved for implementation-defined errors."""

    code = -32010
    message = 'Authentication required'

class RequireAuthGroup(MethodGroup):
    """Refuses anonymous callers anywhere below this group."""

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

- **Refuse with a `JSONRPCError` subclass.** Only those keep their code and text
  on the way out; `PermissionError`, `RuntimeError` or a framework's own
  `Forbidden` become a bare `-32603 Internal error` with a traceback logged at
  ERROR, once per unauthorized attempt.

- `call` carries `path` (the full dotted path as requested), `method` and
  validated `params`. Key caches and limiters on `call.path`, never on the
  method's class name — one class can be registered under several names, and they
  would share a slot.
- Not calling `call_next` vetoes the call — that is how a guard refuses; passing
  it a different context enriches what everything below sees.
- **Annotate `context`** — that annotation is the group's declared `context_type`
  and is checked at registration, exactly as on `execute_method()`.
- Override both hooks if any method below is `async def`. A synchronous wrapper
  cannot await the rest of the chain, so registration refuses the half-overridden
  pair rather than letting async calls slip past the guard.

A group overriding `execute_method()` while owning only subgroups is refused at
registration: that hook could never run there, and a guard written that way would
pass every nested call through untouched.

Order matters — the outermost group runs first on the way in, last on the way
out. Put caches innermost, below every guard: a cache that returns before
delegating skips everything under it, authorization included.

## Context

```python title="context.py"
@dataclass
class AppContext:
    """Everything the transport could establish about this request."""

    user_id: int | None
    ip_address: str

class WhoAmI(Method):
    """Report the authenticated caller."""

    def execute(self, params: None, context: AppContext) -> WhoAmIResult:
        return WhoAmIResult(user_id=context.user_id or 0)

rpc = JSONRPC(version='2.0', context_type=AppContext)
rpc.register('whoami', WhoAmI())
```

A method may declare a *narrower* context type than the RPC promises; a wider one
is refused when the tree is mounted, wherever the method sits.

The library authenticates nothing. `context.user_id` is trustworthy only if the
host verified a credential before building the context — a value copied out of a
request header is whatever the caller typed.

## What the library refuses

Beyond the per-annotation rules in the table above, the whole request fails
closed rather than degrading:

- **Unknown params** — a key matching no field is `-32602`, never ignored.
- **A union must match a variant.** `list[Recipient | Card]` rejects a payload
  matching neither rather than handing the method a raw dict.
- **Non-finite floats never reach a method or the wire.** That matters beyond
  tidiness: `nan > limit` is `False`, so one would walk straight through a range
  check written the obvious way.
- **Nesting is bounded** at 64 levels, whatever shape the recursion takes.
- **Bodies must be JSON objects**; byte input must be UTF-8.

`validate_results=True` additionally checks the return value against its
annotation — the outer type *and* every field of a result dataclass, including
the dataclasses inside a `list` or `dict`, since a dataclass enforces nothing at
runtime and `Row(score='high')` is ordinary Python. It runs *after* `execute()`
returns, so it reports a broken contract without preventing it — a method that
changed state has already changed it.

**Turn it on in development and in tests, off in production.** It catches a
method drifting from the annotation the OpenAPI spec publishes, which is worth
finding before a deploy and costs about twice the response afterwards. Turning it
off gives an attacker nothing: it checks your code, not the caller's input, and
inbound validation is always on with no flag to disable it.

```python
import os

rpc = JSONRPC(validate_results=os.getenv('ENV') != 'production')
```

## Errors

Raise from inside `execute()`:

```python
from jsonrpc.errors import InvalidParamsError, ServerError

raise InvalidParamsError('Account is frozen')          # -32602, the message reaches the caller
raise InvalidParamsError('Bad age', data={'field': 'age'})  # data= for a machine-readable payload
raise ServerError('Rate limit exceeded', code=-32029)  # implementation-defined range
```

Give an authorization refusal its own code rather than reusing `-32602`, so the
caller can tell "not allowed" from "you misspelled a field":

```python
class Unauthenticated(JSONRPCError):
    code = -32010
    message = 'Authentication required'
```

| Code | Class |
|------|-------|
| -32700 | `ParseError` |
| -32600 | `InvalidRequestError` |
| -32601 | `MethodNotFoundError` |
| -32602 | `InvalidParamsError` |
| -32603 | `InternalError` |
| -32001 | `InvalidResultError` |
| -32000 to -32099 | `ServerError` |

Any *other* exception becomes `-32603` with the bare message `Internal error`;
the real one goes to the `jsonrpc-lib` logger with its traceback. That is
deliberate — exception text carries connection strings and paths. Use
`JSONRPC(expose_internal_errors=True)` in development to see it on the wire.

So: raise a `JSONRPCError` subclass for anything the caller should read, and let
everything else fall through to the log. Refused requests are logged at `INFO`,
failures inside notifications at `WARNING`.

## Notifications, async, batch

`handle()` returns `str | None` — `None` for a notification, a request with no
`id`, which the specification says must not be answered. Check the result before
handing it to anything; an unknown or empty method name also takes the
notification branch, so `{"jsonrpc":"2.0","method":""}` reaches it from an
unauthenticated caller.

For `async def execute()`, drive the server with `await rpc.handle_async(...)`.
It serves sync and async methods alike; `handle()` cannot run an async method and
answers `-32603` saying so.

Batch requests (a JSON array, v2.0) go through the same entry points, run
concurrently under `handle_async()`, and are isolated: one item's failure —
including an unserializable result — becomes an error entry carrying its own id
while its siblings keep their results. A batch of only notifications returns
`None`.

## OpenAPI

The spec is generated from the same annotations, so a well-typed method is
already documented. Each method becomes its own path entry.

```python title="openapi_app.py"
from dataclasses import dataclass, field
from typing import Literal
from jsonrpc import JSONRPC, Method, MethodGroup, OpenAPIGenerator

@dataclass
class Contact:
    """How to reach a customer."""

    email: str
    phone: str | None = None

@dataclass
class CreateCustomerParams:
    """Everything needed to open an account."""

    name: str = field(metadata={'description': 'Legal name, as printed on invoices'})
    tier: Literal['free', 'pro', 'enterprise'] = field(metadata={'description': 'Billing tier'})
    contact: Contact = field(metadata={'description': 'Primary contact details'})
    tags: list[str] = field(default_factory=list)

@dataclass
class CreateCustomerResult:
    """The account that was opened."""

    customer_id: int
    tier: str
    contact: Contact

class CreateCustomer(Method):
    """Open a customer account.

    The first line becomes the operation summary; the whole docstring becomes
    its description.
    """

    def execute(self, params: CreateCustomerParams) -> CreateCustomerResult:
        return CreateCustomerResult(customer_id=1, tier=params.tier, contact=params.contact)

customers = MethodGroup()
customers.register('create', CreateCustomer())

rpc = JSONRPC(version='2.0')
rpc.register('customers', customers)

generator = OpenAPIGenerator(rpc, title='Billing API', version='1.0.0')
generator.add_security_scheme('bearerAuth', 'http', {'scheme': 'bearer', 'bearerFormat': 'JWT'})
generator.add_security_requirement('bearerAuth')
generator.add_header('X-Request-ID', 'Correlation id', required=False)
spec = generator.generate()          # generate_json() and generate_yaml() also exist
```

What each piece of the source turns into:

- **docstrings** — the method's first line is the operation summary and the whole
  docstring its description; a dataclass's docstring is its schema description.
  Without one, the summary falls back to the class name and the schema shows
  Python's generated `Contact(email: str, ...)` repr, which reads like a leak.
- **the params dataclass** — the request schema; fields with defaults are
  optional, fields without are required.
- **`field(metadata={'description': ...})`** — the only way to document an
  individual parameter.
- **nested dataclasses** — emitted once under `components/schemas` and referenced
  by `$ref`, so `list[Contact]` and `dict[str, Contact]` document properly;
  `T | None` becomes a `oneOf` with a null variant.

Two things to watch:

- **Schemas are keyed by class name.** Two different dataclasses both named
  `Item` collide silently: the second reuses the first's schema, and the spec
  describes the wrong shape. Give them distinct names.
- **`simplify_id=True`** (the default) documents `id` as a plain integer instead
  of `string | integer`. Viewers render one example per `oneOf` variant, which
  doubles every example for no gain. Pass `simplify_id=False` for a
  spec-faithful union.

## Common mistakes

| Wrong | Right |
|-------|-------|
| `group.register(MyMethod)` | `group.register('name', MyMethod())` — an instance |
| `MethodGroup(prefix='math')` | `MethodGroup()` — the name comes from `register` |
| `params: dict` or `params: list` | `params: MyParamsDataclass` |
| `-> dict`, `-> int`, `-> datetime` | `-> MyResultDataclass` — anything else documents nothing |
| two dataclasses sharing a class name | distinct names — schemas are keyed by them |
| per-request state on `self` | in `context`; `self` is shared by every request |
| services taken from `context` | injected through `__init__` at assembly time |
| one method instance in two groups | one instance per registration |
| guard in `execute_method()` over a namespace | guard in `around_call()` |
| `def around_call(self, call, context, call_next)` | annotate `context` — it declares `context_type` |
| overriding only `around_call()` with async below | override `around_call_async()` too |
| using `handle()`'s result unconditionally | it is `None` for notifications |
| `def execute(self, data: Params)` | the parameter must be named `params` |
