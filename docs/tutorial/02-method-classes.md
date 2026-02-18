# 2. Method Classes

## What You'll Learn

- Transition from decorators to Method classes
- When to use each approach
- Call methods internally

## Decorator vs Method Class

**Decorator (Prototyping):**

```python title="decorator_approach.py"
from jsonrpc import JSONRPC

rpc = JSONRPC()

@rpc.method
def add(a: int, b: int) -> int:
    return a + b
```

**Method Class (Production):**

```python title="method_class_approach.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class AddParams:
    a: int
    b: int

class AddMethod(Method):
    def execute(self, params: AddParams) -> int:
        return params.a + params.b

rpc = JSONRPC()
rpc.register('add', AddMethod())
```

## Complete Example

```python title="production_methods.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

# Define parameter types
@dataclass
class CalculateParams:
    x: float
    y: float
    operation: str

@dataclass
class SquareParams:
    number: float

# Define methods
class Calculate(Method):
    def execute(self, params: CalculateParams) -> float:
        ops = {
            'add': params.x + params.y,
            'subtract': params.x - params.y,
            'multiply': params.x * params.y,
            'divide': params.x / params.y if params.y != 0 else 0.0
        }
        return ops.get(params.operation, 0.0)

class Square(Method):
    def execute(self, params: SquareParams) -> float:
        # Call another method internally
        result = self.rpc.call_method(
            'calculate',
            {'x': params.number, 'y': params.number, 'operation': 'multiply'}
        )
        return result

# Setup
rpc = JSONRPC(version='2.0')
rpc.register('calculate', Calculate())
rpc.register('square', Square())
```

## JSON-RPC Usage

**Request:**

```json title="calculate_request.json"
{
  "jsonrpc": "2.0",
  "method": "calculate",
  "params": {
    "x": 10.5,
    "y": 2.5,
    "operation": "multiply"
  },
  "id": 1
}
```

**Response:**

```json title="calculate_response.json"
{
  "jsonrpc": "2.0",
  "result": 26.25,
  "id": 1
}
```

## Internal Method Calls

```json title="square_request.json"
{
  "jsonrpc": "2.0",
  "method": "square",
  "params": {"number": 5.0},
  "id": 2
}
```

The `Square` method internally calls `Calculate`:

```python
# Internal call (no JSON serialization)
result = self.rpc.call_method('calculate', {
    'x': params.number,
    'y': params.number,
    'operation': 'multiply'
})
```

**Response:**

```json title="square_response.json"
{
  "jsonrpc": "2.0",
  "result": 25.0,
  "id": 2
}
```

## When to Use Each

| Feature | Decorator | Method Class |
|---------|-----------|--------------|
| **Quick prototyping** | Best | Verbose |
| **Production code** | Limited | Best |
| **Internal calls** | No `self.rpc` | Full access |
| **Context support** | Not available | Supported |
| **Method groups** | Root only | Hierarchical |
| **JSON-RPC version** | v2.0 only | Both v1.0 & v2.0 |

## Key Points

- **Decorator**: Fast prototyping, simple APIs
- **Method Class**: Production, internal calls, context, groups
- **Internal calls**: Use `self.rpc.call_method()` for method composition
- **Dataclasses**: Define parameter structure explicitly

!!! warning "Decorator Limitation"
    `@rpc.method` only works with JSON-RPC 2.0. Use Method classes for v1.0.

## What's Next?

→ [Parameters](03-parameters.md) - Deep dive into dataclass validation
