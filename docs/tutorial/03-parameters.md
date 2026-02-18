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
