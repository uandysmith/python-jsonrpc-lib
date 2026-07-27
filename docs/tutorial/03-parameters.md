# 3. Parameters

## What You'll Learn

- Define required and optional parameters
- Use default values
- Handle validation errors

## Required Parameters

```python title="required_params.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class CreateUserParams:
    email: str
    username: str
    age: int

@dataclass
class CreateUserResult:
    user_id: int
    email: str
    username: str

class CreateUser(Method):
    def execute(self, params: CreateUserParams) -> CreateUserResult:
        return CreateUserResult(
            user_id=123,
            email=params.email,
            username=params.username,
        )

rpc = JSONRPC(version='2.0')
rpc.register('create_user', CreateUser())
```

**Request:**

```json title="request.json"
{
  "jsonrpc": "2.0",
  "method": "create_user",
  "params": {
    "email": "user@example.com",
    "username": "john_doe",
    "age": 25
  },
  "id": 1
}
```

**Response:**

```json title="response.json"
{
  "jsonrpc": "2.0",
  "result": {
    "user_id": 123,
    "email": "user@example.com",
    "username": "john_doe"
  },
  "id": 1
}
```

## Optional Parameters with Defaults

```python title="optional_params.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class SearchParams:
    query: str
    limit: int = 10
    offset: int = 0
    sort: str = "relevance"

@dataclass
class SearchResult:
    query: str
    limit: int
    offset: int
    sort: str
    total: int

class Search(Method):
    def execute(self, params: SearchParams) -> SearchResult:
        return SearchResult(
            query=params.query,
            limit=params.limit,
            offset=params.offset,
            sort=params.sort,
            total=0,
        )

rpc = JSONRPC(version='2.0')
rpc.register('search', Search())
```

**Request (minimal — only required field):**

```json title="minimal_request.json"
{
  "jsonrpc": "2.0",
  "method": "search",
  "params": {"query": "python"},
  "id": 1
}
```

**Response (defaults applied):**

```json title="minimal_response.json"
{
  "jsonrpc": "2.0",
  "result": {
    "query": "python",
    "limit": 10,
    "offset": 0,
    "sort": "relevance",
    "total": 0
  },
  "id": 1
}
```

**Request (custom values):**

```json title="custom_request.json"
{
  "jsonrpc": "2.0",
  "method": "search",
  "params": {
    "query": "python",
    "limit": 50,
    "sort": "date"
  },
  "id": 2
}
```

## No Parameters

```python title="no_params.py"
from dataclasses import dataclass
from datetime import datetime
from jsonrpc import JSONRPC, Method

@dataclass
class TimeResult:
    timestamp: str

class GetServerTime(Method):
    def execute(self, params: None) -> TimeResult:
        return TimeResult(timestamp=datetime.now().isoformat())

rpc = JSONRPC(version='2.0')
rpc.register('server_time', GetServerTime())
```

**Request:**

```json title="no_params_request.json"
{
  "jsonrpc": "2.0",
  "method": "server_time",
  "id": 1
}
```

## Validation Errors

**Invalid type:**

```json title="invalid_type_request.json"
{
  "jsonrpc": "2.0",
  "method": "create_user",
  "params": {
    "email": "user@example.com",
    "username": "john_doe",
    "age": "twenty-five"
  },
  "id": 1
}
```

**Error response:**

```json title="error_response.json"
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Parameter 'age' expected type 'int', got 'str'"
  },
  "id": 1
}
```

**Missing required field:**

```json title="missing_field_request.json"
{
  "jsonrpc": "2.0",
  "method": "create_user",
  "params": {
    "email": "user@example.com"
  },
  "id": 2
}
```

**Error response:**

```json title="missing_field_response.json"
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Missing required parameter: 'username'"
  },
  "id": 2
}
```

## Validating values, not just types

The type check answers "is this an int". Whether it is an int you will accept is
your rule, and the place for it is `__post_init__` — ordinary Python, no library
API involved:

```python title="value_validation.py"
from dataclasses import dataclass
from jsonrpc.errors import InvalidParamsError

@dataclass
class CreateUserParams:
    """A new user account."""

    name: str
    age: int

    def __post_init__(self):
        if not self.name.strip():
            raise InvalidParamsError('name must not be empty')
        if not 13 <= self.age <= 120:
            raise InvalidParamsError('age must be between 13 and 120')
```

```json
{"jsonrpc": "2.0", "method": "users.create", "params": {"name": "Ada", "age": -1}, "id": 1}
→ {"jsonrpc": "2.0", "error": {"code": -32602, "message": "age must be between 13 and 120"}, "id": 1}
```

Worth knowing about that message: **it reaches the caller verbatim.** That is
the point — the reason to write it is that someone reads it — but it means the
text is public. Do not put internal detail in it.

Three notes on the mechanics:

- **Raise `InvalidParamsError`, not `ValueError`,** when you want the caller to
  read your message. Both become `-32602`; only the first carries its text
  outward. A plain `ValueError` gets the fixed message
  `Invalid params: rejected by the parameter type` and its own text goes to the
  log — because the `ValueError` you did not write is the far more common one:
  `datetime.fromisoformat`, `int`, `Decimal`, `UUID` and `json.loads` all put
  the input they were handed into the message they raise, and that input came
  from the caller. `InvalidParamsError` also takes `data=` for a machine-readable
  payload.
- Do **not** use `assert`: it disappears under `python -O`, and the response
  would be `-32603`, which is deliberate — a check that vanishes in production
  should not look like a working one.
- `__post_init__` validates; it does not change the declared type. The
  annotation is what mypy reads and what OpenAPI publishes, so keep the field's
  type honest. On a `frozen=True` dataclass, assigning at all needs
  `object.__setattr__`.

Nested dataclasses work the same way — an `InvalidParamsError` raised inside one
reports as `-32602` for the whole request.

## Types JSON cannot express

Parameters are limited to what JSON has: strings, numbers, booleans, arrays,
objects, null — plus dataclasses built from those. `datetime`, `date`, `Enum`,
`Decimal`, `UUID`, `tuple`, `set` and `bytes` are **not** accepted as parameter
annotations: the value arrives as a `str`, and a `str` is never an instance of
`datetime`, so no value the caller could send would ever be accepted. Declaring
one raises `TypeError` where the method class is defined, rather than answering
`-32602` to every call for the life of the process.

Take the wire type and convert where you validate:

```python title="wire_types.py"
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from jsonrpc.errors import InvalidParamsError

@dataclass
class ReportParams:
    """A report over a date range."""

    since: str                      # ISO-8601, e.g. "2024-01-01T00:00:00"
    budget: str                     # decimal string, e.g. "10.50"
    parsed_since: datetime = field(init=False)
    parsed_budget: Decimal = field(init=False)

    def __post_init__(self):
        try:
            self.parsed_since = datetime.fromisoformat(self.since)
        except ValueError:
            raise InvalidParamsError('since must be ISO-8601') from None
        try:
            self.parsed_budget = Decimal(self.budget)
        except InvalidOperation:
            raise InvalidParamsError('budget must be a decimal string') from None
```

`init=False` fields are not parameters — the caller cannot set them and the spec
does not list them — so they are the right place to keep the converted value.
Malformed input produces `-32602` with your message, not a `-32603`.

Note what those two `except` clauses are for. `InvalidOperation` and the
`ValueError` from `fromisoformat` both already become `-32602` on their own — but
with a fixed message, because their own text embeds the string the caller sent
(`Invalid isoformat string: '<whatever they typed>'`). Catching them and raising
your own sentence is what puts something useful on the wire.

The annotation stays `str`, which is honest: `str` is what the caller sends and
what the published schema describes.

**Going the other way** — returning a `datetime` or a `Decimal` — is a
serialization problem, and there the library does have a hook. See
[serialize()](../api-reference.md#serialization-hooks).

## Complex Example

```python title="complex_params.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class FilterParams:
    keyword: str
    min_price: float | None = None
    max_price: float | None = None
    categories: list[str] | None = None
    in_stock: bool = True
    sort_by: str = "price"
    page: int = 1
    per_page: int = 20

@dataclass
class FilterResult:
    keyword: str
    page: int
    per_page: int
    total: int

class FilterProducts(Method):
    def execute(self, params: FilterParams) -> FilterResult:
        return FilterResult(
            keyword=params.keyword,
            page=params.page,
            per_page=params.per_page,
            total=0,
        )

rpc = JSONRPC(version='2.0')
rpc.register('filter_products', FilterProducts())
```

**Request:**

```json title="complex_request.json"
{
  "jsonrpc": "2.0",
  "method": "filter_products",
  "params": {
    "keyword": "laptop",
    "min_price": 500.0,
    "max_price": 1500.0,
    "categories": ["electronics", "computers"],
    "page": 2,
    "per_page": 50
  },
  "id": 1
}
```

## Key Points

- **Required fields**: No default value
- **Optional fields**: Provide a default value
- **No params**: Use `params: None` type hint
- **Type validation**: Automatic based on type hints
- **Error code**: `-32602` for Invalid params

!!! tip "IDE Support"
    Dataclasses give you autocomplete in IDEs like VS Code and PyCharm!

## What's Next?

→ [Nested Types](04-nested-types.md) - Complex data structures
