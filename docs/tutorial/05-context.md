# 5. Context

## What You'll Learn

- Pass authentication context from transport layer
- Use hierarchical context validation
- Implement role-based access control

## Basic Context

```python title="basic_context.py"
from dataclasses import dataclass
import uuid
from jsonrpc import JSONRPC, Method
from jsonrpc.errors import InvalidParamsError

@dataclass
class RequestContext:
    request_id: str
    user_id: int | None = None

@dataclass
class GetProfileParams:
    include_email: bool = False

@dataclass
class ProfileResult:
    user_id: int
    username: str
    email: str | None

class GetProfile(Method):
    def execute(self, params: GetProfileParams, context: RequestContext) -> ProfileResult:
        if not context.user_id:
            raise InvalidParamsError("Authentication required")

        return ProfileResult(
            user_id=context.user_id,
            username=f"user_{context.user_id}",
            email="user@example.com" if params.include_email else None,
        )

rpc = JSONRPC(version='2.0', context_type=RequestContext)
rpc.register('get_profile', GetProfile())

ctx = RequestContext(request_id=str(uuid.uuid4()), user_id=42)
request = '{"jsonrpc": "2.0", "method": "get_profile", "params": {"include_email": true}, "id": 1}'
response = rpc.handle(request, context=ctx)
```

**Response:**

```json title="response.json"
{
  "jsonrpc": "2.0",
  "result": {
    "user_id": 42,
    "username": "user_42",
    "email": "user@example.com"
  },
  "id": 1
}
```

## Role-Based Access Control

```python title="rbac.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, MethodGroup, Method
from jsonrpc.errors import InvalidParamsError

@dataclass
class AuthContext:
    user_id: int
    is_admin: bool = False
    is_moderator: bool = False

@dataclass
class DeleteUserParams:
    target_user_id: int

@dataclass
class BanUserParams:
    user_id: int
    reason: str
    duration_days: int

@dataclass
class DeleteUserResult:
    deleted_user_id: int
    deleted_by: int

@dataclass
class BanUserResult:
    banned_user_id: int
    reason: str
    banned_by: int

class DeleteUser(Method):
    def execute(self, params: DeleteUserParams, context: AuthContext) -> DeleteUserResult:
        if not context.is_admin:
            raise InvalidParamsError("Admin privileges required")
        return DeleteUserResult(
            deleted_user_id=params.target_user_id,
            deleted_by=context.user_id,
        )

class BanUser(Method):
    def execute(self, params: BanUserParams, context: AuthContext) -> BanUserResult:
        if not (context.is_admin or context.is_moderator):
            raise InvalidParamsError("Moderator or admin privileges required")
        return BanUserResult(
            banned_user_id=params.user_id,
            reason=params.reason,
            banned_by=context.user_id,
        )

rpc = JSONRPC(version='2.0', context_type=AuthContext)

admin = MethodGroup()
admin.register('delete_user', DeleteUser())
rpc.register('admin', admin)

moderation = MethodGroup()
moderation.register('ban_user', BanUser())
rpc.register('moderation', moderation)
```

**Admin request (allowed):**

```json title="admin_request.json"
{
  "jsonrpc": "2.0",
  "method": "admin.delete_user",
  "params": {"target_user_id": 999},
  "id": 1
}
```

```python
ctx = AuthContext(user_id=1, is_admin=True)
response = rpc.handle(request, context=ctx)
```

**Non-admin request (denied):**

```python
ctx = AuthContext(user_id=42, is_admin=False)
response = rpc.handle(request, context=ctx)
```

**Error response:**

```json title="access_denied.json"
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Admin privileges required"
  },
  "id": 1
}
```

## Flask Integration

```python title="flask_context.py"
from flask import Flask, request
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method
from jsonrpc.errors import InvalidParamsError

app = Flask(__name__)

@dataclass
class FlaskContext:
    user_id: int | None
    ip_address: str

@dataclass
class SecureOperationParams:
    action: str

@dataclass
class SecureOperationResult:
    action: str
    executed_by: int
    from_ip: str

class SecureOperation(Method):
    def execute(self, params: SecureOperationParams, context: FlaskContext) -> SecureOperationResult:
        if not context.user_id:
            raise InvalidParamsError("Authentication required")
        return SecureOperationResult(
            action=params.action,
            executed_by=context.user_id,
            from_ip=context.ip_address,
        )

rpc = JSONRPC(version='2.0', context_type=FlaskContext)
rpc.register('secure_op', SecureOperation())

@app.route('/rpc', methods=['POST'])
def handle_rpc():
    user_id = request.headers.get('X-User-ID')
    ctx = FlaskContext(
        user_id=int(user_id) if user_id else None,
        ip_address=request.remote_addr,
    )
    response = rpc.handle(request.data, context=ctx)
    return response, 200, {'Content-Type': 'application/json'}
```

**HTTP Request:**

```bash
curl -X POST http://localhost:5000/rpc \
  -H "Content-Type: application/json" \
  -H "X-User-ID: 123" \
  -d '{
    "jsonrpc": "2.0",
    "method": "secure_op",
    "params": {"action": "transfer_funds"},
    "id": 1
  }'
```

## FastAPI Integration

```python title="fastapi_context.py"
from fastapi import FastAPI, Request, Header
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method
from jsonrpc.errors import InvalidParamsError

app = FastAPI()

@dataclass
class FastAPIContext:
    user_id: int | None
    username: str | None

@dataclass
class WhoAmIResult:
    user_id: int
    username: str | None

class WhoAmI(Method):
    def execute(self, params: None, context: FastAPIContext) -> WhoAmIResult:
        if not context.user_id:
            raise InvalidParamsError("Not authenticated")
        return WhoAmIResult(user_id=context.user_id, username=context.username)

rpc = JSONRPC(version='2.0', context_type=FastAPIContext)
rpc.register('whoami', WhoAmI())

@app.post('/rpc')
async def handle_rpc(
    request: Request,
    x_user_id: int | None = Header(None),
    x_username: str | None = Header(None),
):
    ctx = FastAPIContext(user_id=x_user_id, username=x_username)
    body = await request.body()
    return await rpc.handle_async(body, context=ctx)
```

## Hierarchical Context

Context types can form an inheritance hierarchy. A method that requires a more specific context type can only be registered with an RPC that provides exactly that type (or a subclass of it).

```python title="hierarchical_context.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, MethodGroup, Method

@dataclass
class BaseContext:
    request_id: str

@dataclass
class AuthContext(BaseContext):
    user_id: int

@dataclass
class AdminContext(AuthContext):
    is_admin: bool

@dataclass
class AdminResult:
    admin: bool

class AdminAction(Method):
    def execute(self, params: None, context: AdminContext) -> AdminResult:
        return AdminResult(admin=context.is_admin)

# Works: AdminContext is a subclass of AuthContext
rpc = JSONRPC(version='2.0', context_type=AdminContext)
group = MethodGroup()
group.register('action', AdminAction())
rpc.register('admin', group)

# Pass a full AdminContext at call time
ctx = AdminContext(request_id='req-1', user_id=7, is_admin=True)
resp = rpc.handle('{"jsonrpc":"2.0","method":"admin.action","params":null,"id":1}', context=ctx)
```

## Key Points

- **Context from transport**: Extract from HTTP headers, sessions, JWT tokens
- **Type validation**: Context types validated at registration time
- **Hierarchical**: Method context type must be in the same inheritance chain as the RPC context type
- **Security**: Implement authentication and authorization in `execute()`
- **Framework integration**: Works with Flask, FastAPI, Django, etc.

!!! tip "Fail-Fast"
    Registering a method whose context type has no relationship to the RPC's context type raises `TypeError` immediately — before any requests are served.

## What's Next?

→ [Groups](06-groups.md) - Organize methods hierarchically
