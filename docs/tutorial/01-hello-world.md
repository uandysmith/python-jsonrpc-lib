# 1. Hello World

## What You'll Learn

- Create your first JSON-RPC server with decorators
- Handle requests and generate responses
- Understand JSON-RPC protocol structure

## Complete Example

```python title="hello_world.py"
from jsonrpc import JSONRPC

# Create RPC instance
rpc = JSONRPC(version='2.0')

# Register method using decorator
@rpc.method
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@rpc.method
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

# Handle JSON-RPC request
request_json = '''
{
  "jsonrpc": "2.0",
  "method": "add",
  "params": {"a": 10, "b": 32},
  "id": 1
}
'''

response = rpc.handle(request_json)
print(response)
# Output: {"jsonrpc": "2.0", "result": 42, "id": 1}
```

## JSON-RPC Protocol

**Request:**

```json title="request.json"
{
  "jsonrpc": "2.0",
  "method": "add",
  "params": {"a": 10, "b": 32},
  "id": 1
}
```

**Response:**

```json title="response.json"
{
  "jsonrpc": "2.0",
  "result": 42,
  "id": 1
}
```

## Try Another Method

```python title="greet_example.py"
request_json = '''
{
  "jsonrpc": "2.0",
  "method": "greet",
  "params": {"name": "World"},
  "id": 2
}
'''

response = rpc.handle(request_json)
print(response)
# Output: {"jsonrpc": "2.0", "result": "Hello, World!", "id": 2}
```

## Key Points

- `@rpc.method` decorator automatically creates method registration
- Type hints (`int`, `str`) enable automatic validation
- `rpc.handle()` converts JSON request → JSON response
- Request must include: `jsonrpc`, `method`, `params`, `id`
- Response contains: `jsonrpc`, `result`, `id`

!!! tip "Automatic Validation"
    Try sending `{"a": "not_a_number", "b": 32}` - you'll get a validation error!

## Run It

```bash
python hello_world.py
```

## What's Next?

→ [Method Classes](02-method-classes.md) - Production-ready code structure
