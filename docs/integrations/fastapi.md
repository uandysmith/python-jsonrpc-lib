# FastAPI Integration

FastAPI is an async ASGI framework with built-in type validation, dependency injection, and its own OpenAPI generator. The integration pattern mirrors Flask but uses `rpc.handle_async()` and FastAPI's `Header` dependency to inject typed headers into context.

One practical detail: FastAPI already serves its own `/docs` for REST routes. For the JSON-RPC API, add a separate `/docs` route that serves RapiDoc pointed at the JSON-RPC OpenAPI spec — both documentation UIs can coexist.

## Complete FastAPI Application

```python title="fastapi_app.py"
from fastapi import FastAPI, Request, Header
from fastapi.responses import HTMLResponse
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method, MethodGroup
from jsonrpc.openapi import OpenAPIGenerator

app = FastAPI(title="JSON-RPC API")

# Define context
@dataclass
class RequestContext:
    user_id: int | None
    username: str | None

# Define methods
@dataclass
class SearchParams:
    query: str
    limit: int = 10

@dataclass
class SearchItem:
    id: int
    title: str

@dataclass
class WhoAmIResult:
    user_id: int
    username: str | None

class Search(Method):
    async def execute(self, params: SearchParams) -> list[SearchItem]:
        """Search for items by query."""
        # Simulate async database call
        import asyncio
        await asyncio.sleep(0.1)

        results = [
            SearchItem(id=1, title=f"Result for: {params.query}"),
            SearchItem(id=2, title=f"Another result for: {params.query}"),
        ]
        return results[:params.limit]

class WhoAmI(Method):
    def execute(self, params: None, context: RequestContext) -> WhoAmIResult:
        """Get current user information."""
        from jsonrpc.errors import InvalidParamsError

        if not context.user_id:
            raise InvalidParamsError("Not authenticated")

        return WhoAmIResult(user_id=context.user_id, username=context.username)

# Setup RPC
rpc = JSONRPC(version='2.0', context_type=RequestContext)
rpc.register('search', Search())

auth_group = MethodGroup()
auth_group.register('whoami', WhoAmI())
rpc.register('auth', auth_group)

# Generate OpenAPI
generator = OpenAPIGenerator(
    rpc,
    title="FastAPI JSON-RPC API",
    version="2.0.0",
    description="Async JSON-RPC server with FastAPI",
    servers=[{"url": "http://localhost:8000/rpc"}],
    headers={
        "X-User-ID": {
            "description": "User ID",
            "schema": {"type": "integer"}
        },
        "X-Username": {
            "description": "Username",
            "schema": {"type": "string"}
        }
    }
)
openapi_spec = generator.generate()

# Routes
@app.post('/rpc')
async def handle_rpc(
    request: Request,
    x_user_id: int | None = Header(None),
    x_username: str | None = Header(None)
):
    # Build context from headers
    ctx = RequestContext(user_id=x_user_id, username=x_username)

    # Handle async
    body = await request.body()
    response = await rpc.handle_async(body, context=ctx)
    return response

@app.get('/openapi.json')
async def get_openapi():
    return openapi_spec

@app.get('/docs', response_class=HTMLResponse)
async def docs():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>API Documentation</title>
        <script type="module" src="https://unpkg.com/rapidoc/dist/rapidoc-min.js"></script>
    </head>
    <body>
        <rapi-doc
            spec-url="/openapi.json"
            render-style="focused"
            theme="light"
            primary-color="#7C4DFF"
            allow-try="true"
            show-header="true"
            header-color="#7C4DFF"
        > </rapi-doc>
    </body>
    </html>
    '''

@app.get('/')
async def root():
    return {"message": "JSON-RPC API - visit /docs for documentation"}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
```

## Run the Application

```bash
pip install fastapi uvicorn python-jsonrpc-lib
python fastapi_app.py
```

Visit:
- **Root**: `http://localhost:8000/`
- **RPC endpoint**: `http://localhost:8000/rpc`
- **API docs**: `http://localhost:8000/docs`
- **OpenAPI spec**: `http://localhost:8000/openapi.json`

## Test with curl

**Search request:**

```bash
curl -X POST http://localhost:8000/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "search",
    "params": {"query": "python", "limit": 5},
    "id": 1
  }'
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "result": [
    {"id": 1, "title": "Result for: python"},
    {"id": 2, "title": "Another result for: python"}
  ],
  "id": 1
}
```

> Search returns up to `limit` items (default 10). Results are plain dataclasses — serialized automatically.

**Authenticated request:**

```bash
curl -X POST http://localhost:8000/rpc \
  -H "Content-Type: application/json" \
  -H "X-User-ID: 42" \
  -H "X-Username: john_doe" \
  -d '{
    "jsonrpc": "2.0",
    "method": "auth.whoami",
    "id": 2
  }'
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "user_id": 42,
    "username": "john_doe"
  },
  "id": 2
}
```

## Async Methods with Database

```python title="async_database.py"
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from dataclasses import dataclass
from jsonrpc import Method

# Async database setup
engine = create_async_engine('postgresql+asyncpg://user:pass@localhost/db')
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession)

@dataclass
class GetUserParams:
    user_id: int

class GetUser(Method):
    async def execute(self, params: GetUserParams) -> dict:
        """Fetch user from database asynchronously."""
        async with AsyncSessionLocal() as session:
            # Async query
            result = await session.execute(
                select(User).where(User.id == params.user_id)
            )
            user = result.scalar_one_or_none()

            if not user:
                from jsonrpc.errors import InvalidParamsError
                raise InvalidParamsError(f"User {params.user_id} not found")

            return {
                "id": user.id,
                "username": user.username,
                "email": user.email
            }
```

## Dependency Injection

```python title="dependencies.py"
from fastapi import Depends
from typing import Annotated

# Dependency
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# FastAPI endpoint with dependency
@app.post('/rpc')
async def handle_rpc(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    # Inject database into context
    ctx = RequestContext(db=db, user_id=request.headers.get('X-User-ID'))
    body = await request.body()
    return await rpc.handle_async(body, context=ctx)
```

## WebSocket Support

```python title="websocket.py"
from fastapi import WebSocket

@app.websocket('/ws/rpc')
async def websocket_rpc(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            # Receive JSON-RPC request
            data = await websocket.receive_text()

            # Handle request
            ctx = RequestContext(user_id=None, username=None)
            response = await rpc.handle_async(data, context=ctx)

            # Send response
            await websocket.send_text(response)
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await websocket.close()
```

**Client example:**

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/rpc');

ws.onopen = () => {
  ws.send(JSON.stringify({
    jsonrpc: '2.0',
    method: 'search',
    params: {query: 'test'},
    id: 1
  }));
};

ws.onmessage = (event) => {
  const response = JSON.parse(event.data);
  console.log(response.result);
};
```

## Middleware

```python title="middleware.py"
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from time import time

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["*"],
)

# Logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time()
    response = await call_next(request)
    duration = time() - start_time
    print(f"{request.method} {request.url.path} - {duration:.3f}s")
    return response
```

## Background Tasks

```python title="background_tasks.py"
from fastapi import BackgroundTasks
from dataclasses import dataclass
from jsonrpc import Method

@dataclass
class SendEmailParams:
    to: str
    subject: str
    body: str

async def send_email_background(to: str, subject: str, body: str):
    # Simulate sending email
    import asyncio
    await asyncio.sleep(2)
    print(f"Email sent to {to}")

class SendEmail(Method):
    async def execute(self, params: SendEmailParams, context: RequestContext) -> dict:
        """Send email in background."""
        # Access background_tasks from context
        if hasattr(context, 'background_tasks'):
            context.background_tasks.add_task(
                send_email_background,
                params.to,
                params.subject,
                params.body
            )

        return {"status": "queued", "recipient": params.to}

# Modified endpoint
@app.post('/rpc')
async def handle_rpc(
    request: Request,
    background_tasks: BackgroundTasks
):
    ctx = RequestContext(background_tasks=background_tasks)
    body = await request.body()
    return await rpc.handle_async(body, context=ctx)
```

## API Versioning

```python title="versioning.py"
from fastapi import APIRouter

# v1 router
router_v1 = APIRouter(prefix='/api/v1')

rpc_v1 = JSONRPC(version='2.0')
rpc_v1.register('search', SearchV1())

@router_v1.post('/rpc')
async def handle_rpc_v1(request: Request):
    body = await request.body()
    return await rpc_v1.handle_async(body)

# v2 router
router_v2 = APIRouter(prefix='/api/v2')

rpc_v2 = JSONRPC(version='2.0')
rpc_v2.register('search', SearchV2())

@router_v2.post('/rpc')
async def handle_rpc_v2(request: Request):
    body = await request.body()
    return await rpc_v2.handle_async(body)

# Register routers
app.include_router(router_v1)
app.include_router(router_v2)

# Endpoints:
# /api/v1/rpc
# /api/v2/rpc
```

## Production Deployment

```bash
# Install production server
pip install uvicorn[standard] gunicorn

# Run with Uvicorn
uvicorn fastapi_app:app --host 0.0.0.0 --port 8000 --workers 4

# Run with Gunicorn + Uvicorn workers
gunicorn fastapi_app:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

## Key Points

- Use `async def execute()` for async methods
- Call `rpc.handle_async()` for async processing
- Extract context from FastAPI Headers
- Support WebSocket for real-time RPC
- Use Background Tasks for async operations
- Deploy with Uvicorn + Gunicorn for production

## What's Next?

→ [Custom Transports](custom.md) - TCP, WebSocket, custom protocols
