# Flask Integration

Flask is a synchronous WSGI framework — simple, mature, and widely used. The integration pattern is straightforward: extract context from the incoming request (headers, session, JWT), call `rpc.handle()`, return JSON. No async, no setup beyond a single route.

This page shows a complete application with context, OpenAPI docs, blueprints, and CORS — the pieces you'll assemble in a real project.

## Complete Flask Application

!!! warning "The identity in this example is whatever the caller typed"
    `X-User-ID` is read straight off the request with nothing verifying it, to
    keep the integration example about integration. A real endpoint must derive
    `user_id` from a verified credential — see
    [Middleware → Transport layer](../advanced/middleware.md#authentication-middleware).

```python title="flask_app.py"
from flask import Flask, request
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method, MethodGroup
from jsonrpc.openapi import OpenAPIGenerator

app = Flask(__name__)

# Define context
@dataclass
class RequestContext:
    user_id: int | None
    ip_address: str

# Define methods
@dataclass
class AddParams:
    a: int
    b: int

class Add(Method):
    def execute(self, params: AddParams) -> int:
        """Add two numbers."""
        return params.a + params.b

@dataclass
class GetProfileParams:
    include_email: bool = False

@dataclass
class ProfileResult:
    user_id: int
    username: str
    email: str | None
    ip: str

class GetProfile(Method):
    def execute(self, params: GetProfileParams, context: RequestContext) -> ProfileResult:
        """Get user profile information."""
        if not context.user_id:
            from jsonrpc.errors import JSONRPCError

            class Unauthenticated(JSONRPCError):
                code = -32010
                message = 'Authentication required'

            raise Unauthenticated()

        return ProfileResult(
            user_id=context.user_id,
            username=f"user_{context.user_id}",
            email="user@example.com" if params.include_email else None,
            ip=context.ip_address,
        )

# Setup RPC
rpc = JSONRPC(version='2.0', context_type=RequestContext)

math_group = MethodGroup()
math_group.register('add', Add())
rpc.register('math', math_group)

user_group = MethodGroup()
user_group.register('profile', GetProfile())
rpc.register('user', user_group)

# Generate OpenAPI
generator = OpenAPIGenerator(
    rpc,
    title="Flask JSON-RPC API",
    version="1.0.0",
)
generator.add_header("X-User-ID", "User ID for authentication", schema={"type": "integer"})
openapi_spec = generator.generate()

# Routes
@app.route('/rpc', methods=['POST'])
def handle_rpc():
    # Extract context from headers.
    # NOTE: a header is not a credential - see the warning below this block.
    user_id = request.headers.get('X-User-ID')
    ctx = RequestContext(
        user_id=int(user_id) if user_id else None,
        ip_address=request.remote_addr
    )

    # Handle request
    response = rpc.handle(request.data, context=ctx)

    # A notification has no response; returning None here raises inside Flask
    if response is None:
        return '', 204
    return response, 200, {'Content-Type': 'application/json'}

@app.route('/openapi.json')
def openapi():
    return openapi_spec

@app.route('/docs')
def docs():
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>API Documentation</title>
        <script type="module" src="https://unpkg.com/rapidoc/dist/rapidoc-min.js"></script>
    </head>
    <body>
        <rapi-doc
            spec-url="/openapi.json"
            render-style="read"
            theme="dark"
            show-header="false"
            allow-try="true"
        > </rapi-doc>
    </body>
    </html>
    '''

if __name__ == '__main__':
    app.run(debug=True, port=5000)
```

## Run the Application

```bash
pip install flask python-jsonrpc-lib
python flask_app.py
```

Visit:
- **RPC endpoint**: `http://localhost:5000/rpc`
- **API docs**: `http://localhost:5000/docs`
- **OpenAPI spec**: `http://localhost:5000/openapi.json`

## Test with curl

**Math request:**

```bash
curl -X POST http://localhost:5000/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "math.add",
    "params": {"a": 10, "b": 5},
    "id": 1
  }'
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "result": 15,
  "id": 1
}
```

**Authenticated request:**

```bash
curl -X POST http://localhost:5000/rpc \
  -H "Content-Type: application/json" \
  -H "X-User-ID: 123" \
  -d '{
    "jsonrpc": "2.0",
    "method": "user.profile",
    "params": {"include_email": true},
    "id": 2
  }'
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "user_id": 123,
    "username": "user_123",
    "email": "user@example.com",
    "ip": "127.0.0.1"
  },
  "id": 2
}
```

## Error Handling

```bash
curl -X POST http://localhost:5000/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "user.profile",
    "params": {},
    "id": 3
  }'
```

**Error response (not authenticated):**

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Authentication required"
  },
  "id": 3
}
```

## Flask Blueprints

```python title="blueprints.py"
from flask import Blueprint, request

rpc_blueprint = Blueprint('rpc', __name__)

@rpc_blueprint.route('/rpc', methods=['POST'])
def handle_rpc():
    ctx = RequestContext(
        user_id=request.headers.get('X-User-ID'),
        ip_address=request.remote_addr
    )
    response = rpc.handle(request.data, context=ctx)
    if response is None:
        return '', 204
    return response, 200, {'Content-Type': 'application/json'}

# Register blueprint
app.register_blueprint(rpc_blueprint, url_prefix='/api/v1')
# Endpoint: http://localhost:5000/api/v1/rpc
```

## CORS Support

!!! warning "Do not ship a wildcard origin"
    `CORS(app)` reflects any origin, including `null`, and grants your custom
    headers on preflight. Combined with header-asserted identity it lets any page
    on the internet call your endpoint as any user. Pass an explicit allow-list.

    Note also that a cross-origin `fetch()` with no headers object sends
    `Content-Type: text/plain` and skips the preflight entirely, so CORS never
    gets a say. Check the media type in the route if you care about that:

    ```python
    if not request.mimetype.startswith('application/json'):
        return {'error': 'Content-Type must be application/json'}, 415
    ```

```python title="flask_cors.py"
from flask import Flask, request
from flask_cors import CORS, cross_origin

app = Flask(__name__)
CORS(app, origins=['https://app.example.com'])  # never CORS(app) in production

# Or specific route:
@app.route('/rpc', methods=['POST'])
@cross_origin(origins=['https://app.example.com'])
def handle_rpc():
    response = rpc.handle(request.data)
    if response is None:
        return '', 204
    return response, 200, {'Content-Type': 'application/json'}
```

## Batch Requests

```bash
curl -X POST http://localhost:5000/rpc \
  -H "Content-Type: application/json" \
  -d '[
    {
      "jsonrpc": "2.0",
      "method": "math.add",
      "params": {"a": 1, "b": 2},
      "id": 1
    },
    {
      "jsonrpc": "2.0",
      "method": "math.add",
      "params": {"a": 5, "b": 3},
      "id": 2
    }
  ]'
```

**Response:**

```json
[
  {"jsonrpc": "2.0", "result": 3, "id": 1},
  {"jsonrpc": "2.0", "result": 8, "id": 2}
]
```

## Key Points

- Extract context from HTTP headers/cookies/session — and verify a credential before you trust it
- Check `handle()` for `None` before returning: a notification has no response
- Return JSON response with `Content-Type: application/json`
- Use Flask blueprints for API versioning
- Enable CORS for browser clients with an explicit origin allow-list, never a wildcard
- Batch requests work automatically

## What's Next?

→ [FastAPI Integration](fastapi.md) - Async server with type hints
