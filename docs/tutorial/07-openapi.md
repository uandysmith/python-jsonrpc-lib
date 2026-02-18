# 7. OpenAPI Generation

!!! success "Unique Feature"
    **Built-in OpenAPI generation is rare in JSON-RPC libraries!** Most require manual schema writing or have no documentation features at all.

## What You'll Learn

- Generate OpenAPI 3.0 schema from your methods
- Integrate with RapiDoc/Swagger UI
- Auto-document complex types and nested structures

## Basic OpenAPI Generation

```python title="basic_openapi.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method
from jsonrpc.openapi import OpenAPIGenerator

# Define methods
@dataclass
class CalculateParams:
    x: float
    y: float
    operation: str

class Calculate(Method):
    def execute(self, params: CalculateParams) -> float:
        """Perform arithmetic operation on two numbers.

        Supported operations: add, subtract, multiply, divide
        """
        ops = {
            'add': params.x + params.y,
            'subtract': params.x - params.y,
            'multiply': params.x * params.y,
            'divide': params.x / params.y if params.y != 0 else 0.0
        }
        return ops.get(params.operation, 0.0)

@dataclass
class GreetParams:
    name: str
    greeting: str = "Hello"

class Greet(Method):
    def execute(self, params: GreetParams) -> str:
        """Greet someone with a customizable greeting message."""
        return f"{params.greeting}, {params.name}!"

# Setup
rpc = JSONRPC(version='2.0')
rpc.register('calculate', Calculate())
rpc.register('greet', Greet())

# Generate OpenAPI schema
generator = OpenAPIGenerator(
    rpc,
    title="Calculator API",
    version="1.0.0",
    description="Simple calculator with JSON-RPC 2.0"
)

spec = generator.generate()

# spec is a dict containing full OpenAPI 3.0 schema
import json
print(json.dumps(spec, indent=2))
```

**Generated OpenAPI Schema:**

```json title="openapi_output.json"
{
  "openapi": "3.0.0",
  "info": {
    "title": "Calculator API",
    "version": "1.0.0",
    "description": "Simple calculator with JSON-RPC 2.0"
  },
  "paths": {
    "/": {
      "post": {
        "summary": "JSON-RPC endpoint",
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "oneOf": [
                  {"$ref": "#/components/schemas/CalculateRequest"},
                  {"$ref": "#/components/schemas/GreetRequest"}
                ]
              }
            }
          }
        }
      }
    }
  },
  "components": {
    "schemas": {
      "CalculateRequest": {
        "type": "object",
        "properties": {
          "jsonrpc": {"type": "string", "enum": ["2.0"]},
          "method": {"type": "string", "enum": ["calculate"]},
          "params": {
            "type": "object",
            "properties": {
              "x": {"type": "number"},
              "y": {"type": "number"},
              "operation": {"type": "string"}
            },
            "required": ["x", "y", "operation"]
          },
          "id": {"type": "integer"}
        },
        "required": ["jsonrpc", "method", "params", "id"]
      }
    }
  }
}
```

## Complex Types and Nested Structures

```python title="complex_openapi.py"
from dataclasses import dataclass
from typing import Literal
from jsonrpc import JSONRPC, Method
from jsonrpc.openapi import OpenAPIGenerator

@dataclass
class Address:
    street: str
    city: str
    country: str

@dataclass
class Contact:
    email: str
    phone: str | None = None

@dataclass
class CreateUserParams:
    username: str
    email: str
    age: int
    address: Address
    contacts: list[Contact]
    role: Literal["user", "admin", "moderator"] = "user"
    tags: list[str] | None = None

@dataclass
class CreateUserResult:
    user_id: int
    username: str
    role: str

class CreateUser(Method):
    def execute(self, params: CreateUserParams) -> CreateUserResult:
        """Create a new user with full profile information.

        Returns the created user ID and profile summary.
        """
        return CreateUserResult(
            user_id=123,
            username=params.username,
            role=params.role,
        )

rpc = JSONRPC(version='2.0')
rpc.register('create_user', CreateUser())

generator = OpenAPIGenerator(rpc, title="User Management API", version="1.0.0")
spec = generator.generate()

# OpenAPI schema includes:
# - Nested Address schema
# - Nested Contact schema
# - Literal enum for role
# - Optional types (phone, tags)
# - Array types (contacts, tags)
```

**Generated Schema (excerpt):**

```json title="nested_schema.json"
{
  "components": {
    "schemas": {
      "Address": {
        "type": "object",
        "properties": {
          "street": {"type": "string"},
          "city": {"type": "string"},
          "country": {"type": "string"}
        },
        "required": ["street", "city", "country"]
      },
      "Contact": {
        "type": "object",
        "properties": {
          "email": {"type": "string"},
          "phone": {"type": "string", "nullable": true}
        },
        "required": ["email"]
      },
      "CreateUserParams": {
        "type": "object",
        "properties": {
          "username": {"type": "string"},
          "email": {"type": "string"},
          "age": {"type": "integer"},
          "address": {"$ref": "#/components/schemas/Address"},
          "contacts": {
            "type": "array",
            "items": {"$ref": "#/components/schemas/Contact"}
          },
          "role": {
            "type": "string",
            "enum": ["user", "admin", "moderator"],
            "default": "user"
          },
          "tags": {
            "type": "array",
            "items": {"type": "string"},
            "nullable": true
          }
        },
        "required": ["username", "email", "age", "address", "contacts"]
      }
    }
  }
}
```

## RapiDoc Integration (Flask)

```python title="flask_rapidoc.py"
from flask import Flask
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method
from jsonrpc.openapi import OpenAPIGenerator
import json

app = Flask(__name__)

# Define API
@dataclass
class SearchParams:
    query: str
    limit: int = 10

class Search(Method):
    def execute(self, params: SearchParams) -> list[dict]:
        """Search for items by query string."""
        return [{"id": 1, "title": "Result 1"}]

rpc = JSONRPC(version='2.0')
rpc.register('search', Search())

# Generate OpenAPI spec
generator = OpenAPIGenerator(
    rpc,
    base_url="/rpc",
    title="Search API",
    version="1.0.0",
)
openapi_spec = generator.generate()

# RPC endpoint
@app.route('/rpc', methods=['POST'])
def handle_rpc():
    from flask import request
    response = rpc.handle(request.data)
    return response, 200, {'Content-Type': 'application/json'}

# OpenAPI spec endpoint
@app.route('/openapi.json')
def openapi():
    return openapi_spec

# Interactive documentation with RapiDoc
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
    app.run(port=5000)
    # Visit http://localhost:5000/docs for interactive API docs!
```

## FastAPI Integration with RapiDoc

```python title="fastapi_rapidoc.py"
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method
from jsonrpc.openapi import OpenAPIGenerator

app = FastAPI()

# Define methods
@dataclass
class Item:
    name: str
    price: float
    quantity: int

@dataclass
class CreateOrderParams:
    items: list[Item]
    customer_id: int

@dataclass
class CreateOrderResult:
    order_id: str
    customer_id: int
    total: float
    items_count: int

class CreateOrder(Method):
    def execute(self, params: CreateOrderParams) -> CreateOrderResult:
        """Create a new order with items.

        Returns order ID and total price.
        """
        total = sum(item.price * item.quantity for item in params.items)
        return CreateOrderResult(
            order_id='ORD-123',
            customer_id=params.customer_id,
            total=total,
            items_count=len(params.items),
        )

rpc = JSONRPC(version='2.0')
rpc.register('create_order', CreateOrder())

# Generate OpenAPI
generator = OpenAPIGenerator(
    rpc,
    base_url="/rpc",
    title="Order Management API",
    version="2.0.0",
)
openapi_spec = generator.generate()

@app.post('/rpc')
async def handle_rpc(request: Request):
    body = await request.body()
    response = await rpc.handle_async(body)
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
        <title>Order API Documentation</title>
        <script type="module" src="https://unpkg.com/rapidoc/dist/rapidoc-min.js"></script>
    </head>
    <body>
        <rapi-doc
            spec-url="/openapi.json"
            render-style="focused"
            theme="light"
            primary-color="#7C4DFF"
            allow-try="true"
            allow-server-selection="false"
        > </rapi-doc>
    </body>
    </html>
    '''
```

**Visit `http://localhost:8000/docs`** for interactive API documentation where you can:
- Browse all available methods
- See parameter schemas
- Try requests directly in browser
- View response examples

## Security Schemes

```python title="security_schemes.py"
from jsonrpc.openapi import OpenAPIGenerator

# Bearer token authentication
generator = OpenAPIGenerator(rpc, title="Secure API", version="1.0.0")
generator.add_security_scheme(
    "BearerAuth",
    scheme_type="http",
    scheme="bearer",
    bearerFormat="JWT",
)
generator.add_security_requirement("BearerAuth")

# OAuth2
generator = OpenAPIGenerator(rpc, title="OAuth API", version="1.0.0")
generator.add_security_scheme(
    "OAuth2",
    scheme_type="oauth2",
    flows={
        "authorizationCode": {
            "authorizationUrl": "https://example.com/oauth/authorize",
            "tokenUrl": "https://example.com/oauth/token",
            "scopes": {
                "read": "Read access",
                "write": "Write access",
            },
        }
    },
)
generator.add_security_requirement("OAuth2", scopes=["read", "write"])
```

## Custom Headers

```python title="custom_headers.py"
generator = OpenAPIGenerator(rpc, title="API with Headers", version="1.0.0")
generator.add_header(
    name="X-User-ID",
    description="User ID from session",
    required=True,
    schema={"type": "integer"},
)
generator.add_header(
    name="X-Request-ID",
    description="Unique request identifier",
    required=False,
    schema={"type": "string", "format": "uuid"},
)
```

## Why This is Powerful

**Traditional approach (manual schema):**

```python
# Define method
def calculate(x, y, op):
    return x + y

# Separately maintain OpenAPI schema
openapi = {
    "paths": {
        "/": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                # Manually write schema...
                                # Must keep in sync with code!
                            }
                        }
                    }
                }
            }
        }
    }
}
```

**python-jsonrpc-lib approach:**

```python
# Define method with types
@dataclass
class CalculateParams:
    x: float
    y: float
    operation: str

class Calculate(Method):
    def execute(self, params: CalculateParams) -> float:
        """Perform arithmetic operation."""
        return params.x + params.y

# Schema auto-generated from types!
spec = generator.generate()
```

**Benefits:**

| Manual Schema | python-jsonrpc-lib |
|---------------|----------------|
| Write schema separately | Auto-generated |
| Keep docs in sync manually | Always in sync |
| No validation | Automatic validation |
| Prone to errors | Type-safe |
| Verbose | Concise |

## Key Points

- **Automatic**: Schema generated from dataclass type hints
- **Nested types**: Full support for complex structures
- **Docstrings**: Method descriptions from docstrings
- **Interactive docs**: RapiDoc/Swagger UI integration
- **Always in sync**: Schema updates when code changes
- **Zero config**: Just define types, get documentation

!!! success "Rare Feature"
    Most JSON-RPC libraries don't have OpenAPI generation. This is a **unique advantage** of python-jsonrpc-lib!

## What's Next?

→ [Integrations](../integrations/flask.md) - Use with Flask and FastAPI

→ [Advanced Topics](../advanced/async.md) - Async, batch, protocols
