# 6. Groups

## What You'll Learn

- Organize methods with prefixes
- Create hierarchical method namespaces
- Implement middleware with custom MethodGroups

## Basic Group Registration

```python title="basic_groups.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, MethodGroup, Method

# Math methods
@dataclass
class BinaryOpParams:
    a: float
    b: float

class Add(Method):
    def execute(self, params: BinaryOpParams) -> float:
        return params.a + params.b

class Multiply(Method):
    def execute(self, params: BinaryOpParams) -> float:
        return params.a * params.b

# String methods
@dataclass
class StringParams:
    text: str

class Uppercase(Method):
    def execute(self, params: StringParams) -> str:
        return params.text.upper()

class Reverse(Method):
    def execute(self, params: StringParams) -> str:
        return params.text[::-1]

# Setup
rpc = JSONRPC(version='2.0')

math_group = MethodGroup()
math_group.register('add', Add())
math_group.register('multiply', Multiply())
rpc.register('math', math_group)

string_group = MethodGroup()
string_group.register('upper', Uppercase())
string_group.register('reverse', Reverse())
rpc.register('string', string_group)
```

**Request:**

```json title="math_request.json"
{
  "jsonrpc": "2.0",
  "method": "math.add",
  "params": {"a": 10, "b": 5},
  "id": 1
}
```

**Response:**

```json title="math_response.json"
{
  "jsonrpc": "2.0",
  "result": 15.0,
  "id": 1
}
```

```json title="string_request.json"
{
  "jsonrpc": "2.0",
  "method": "string.reverse",
  "params": {"text": "hello"},
  "id": 2
}
```

**Response:**

```json title="string_response.json"
{
  "jsonrpc": "2.0",
  "result": "olleh",
  "id": 2
}
```

## Hierarchical Namespaces

```python title="hierarchical.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, MethodGroup, Method

@dataclass
class UserIdParams:
    user_id: int

@dataclass
class DeletedResult:
    deleted: int

@dataclass
class UserSummary:
    id: int

@dataclass
class ProfileResult:
    user_id: int
    name: str

# Admin group
class DeleteUser(Method):
    def execute(self, params: UserIdParams) -> DeletedResult:
        return DeletedResult(deleted=params.user_id)

class ListUsers(Method):
    def execute(self, params: None) -> list[UserSummary]:
        return [UserSummary(id=1), UserSummary(id=2)]

# Public group
class GetProfile(Method):
    def execute(self, params: UserIdParams) -> ProfileResult:
        return ProfileResult(user_id=params.user_id, name="John")

# Setup hierarchical structure
rpc = JSONRPC(version='2.0')

# admin.user.*
admin_user_group = MethodGroup()
admin_user_group.register('delete', DeleteUser())
admin_user_group.register('list', ListUsers())

admin_group = MethodGroup()
admin_group.register('user', admin_user_group)

# public.user.*
public_user_group = MethodGroup()
public_user_group.register('profile', GetProfile())

public_group = MethodGroup()
public_group.register('user', public_user_group)

rpc.register('admin', admin_group)
rpc.register('public', public_group)
```

**Methods available:**
- `admin.user.delete`
- `admin.user.list`
- `public.user.profile`

**Request:**

```json title="hierarchical_request.json"
{
  "jsonrpc": "2.0",
  "method": "admin.user.delete",
  "params": {"user_id": 42},
  "id": 1
}
```

## Middleware

`MethodGroup` is the extension point for cross-cutting concerns. Override `execute_method()` to inject behavior before and after every method call in the group — logging, caching, rate limiting, authentication guards, and more.

Groups compose cleanly: wrap one group inside another to layer behaviors without touching method logic.

→ [Middleware](../advanced/middleware.md) - Reference implementations: logging, caching, rate limiting, auth guards

## Key Points

- **MethodGroup**: Organizes methods with prefix
- **Hierarchical**: Groups can contain groups (unlimited depth)
- **Middleware**: Override `execute_method()` for custom behavior
- **Composition**: Combine multiple groups for layered functionality
- **Separation**: Business logic (Method) vs routing (MethodGroup)

!!! tip "Real-World Usage"
    Use groups for:
    - API versioning: `v1.*`, `v2.*`
    - Access control: `public.*`, `admin.*`
    - Feature separation: `users.*`, `orders.*`, `payments.*`

## What's Next?

→ [OpenAPI](07-openapi.md) — Auto-generate API documentation
