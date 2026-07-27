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

## Sharing Code Between Methods

Methods are ordinary classes, so ordinary inheritance works. A base class that
does **not** define `execute()` is an intermediate base: it carries shared domain
logic and cannot be registered itself.

```python title="domain_base.py"
@dataclass
class AppContext:
    """What the transport established about the caller."""

    user_id: int

@dataclass
class RefundParams:
    """Which charge to refund."""

    charge_id: str

@dataclass
class RefundResult:
    """The refund that was started."""

    refund_id: str
    status: str

class BillingMethod(Method):
    """Shared helpers for every billing method. No execute() - not mountable."""

    def load_account(self, context: AppContext):
        return accounts.get(context.user_id)

    def assert_not_frozen(self, account):
        if account.frozen:
            raise InvalidParamsError('Account is frozen')

class Refund(BillingMethod):
    def execute(self, params: RefundParams, context: AppContext) -> RefundResult:
        account = self.load_account(context)
        self.assert_not_frozen(account)
        return refund(account, params.amount)
```

Type extraction runs for the class that defines `execute()`. `Refund` gets
`params_type` and `result_type` from its own signature; `BillingMethod` has no
signature to extract and is skipped.

The template-method shape works too — a base defines `execute()` and the params
contract, subclasses override only the hooks and share the extracted types:

```python title="template_method.py"
@dataclass
class PageParams:
    """Offset pagination."""

    offset: int = 0
    limit: int = 50

@dataclass
class Row:
    """One row of a listing."""

    id: int
    label: str

class ListMethod(Method):
    def execute(self, params: PageParams) -> list[Row]:
        rows = self.query(params.offset, params.limit)
        return [self.to_row(r) for r in rows]

    def query(self, offset, limit):
        raise NotImplementedError

    def to_row(self, record):
        return Row(id=record.id, label=str(record))

class ListOrders(ListMethod):
    def query(self, offset, limit):
        return orders.page(offset, limit)
```

Registering a class that never implemented `execute()` raises `TypeError` — an
abstract base is for inheriting, not for mounting.

## Key Points

- **Decorator**: Fast prototyping, simple APIs
- **Method Class**: Production, internal calls, context, groups
- **Internal calls**: Use `self.rpc.call_method()` for method composition
- **Dataclasses**: Define parameter structure explicitly
- **Inheritance**: a base without `execute()` is an abstract domain base; type extraction runs for the class that defines `execute()`

!!! warning "Decorator Limitation"
    `@rpc.method` only works with JSON-RPC 2.0. Use Method classes for v1.0.

## What's Next?

→ [Parameters](03-parameters.md) - Deep dive into dataclass validation
