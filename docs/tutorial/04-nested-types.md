# 4. Nested Types

## What You'll Learn

- Use nested dataclasses
- Work with Union and Optional types
- Handle lists and dicts with type parameters

## Simple Nested Structure

```python title="nested_simple.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class Address:
    street: str
    city: str
    country: str
    postal_code: str

@dataclass
class CreateAddressParams:
    user_id: int
    address: Address

@dataclass
class AddressSavedResult:
    user_id: int
    street: str
    city: str

class SaveAddress(Method):
    def execute(self, params: CreateAddressParams) -> AddressSavedResult:
        return AddressSavedResult(
            user_id=params.user_id,
            street=params.address.street,
            city=params.address.city,
        )

rpc = JSONRPC(version='2.0')
rpc.register('save_address', SaveAddress())
```

**Request:**

```json title="nested_request.json"
{
  "jsonrpc": "2.0",
  "method": "save_address",
  "params": {
    "user_id": 42,
    "address": {
      "street": "123 Main St",
      "city": "New York",
      "country": "USA",
      "postal_code": "10001"
    }
  },
  "id": 1
}
```

**Response:**

```json title="nested_response.json"
{
  "jsonrpc": "2.0",
  "result": {
    "user_id": 42,
    "street": "123 Main St",
    "city": "New York"
  },
  "id": 1
}
```

## Deep Nesting (3 Levels)

```python title="deep_nesting.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class Contact:
    email: str
    phone: str

@dataclass
class Address:
    street: str
    city: str
    contact: Contact

@dataclass
class CompanyInfo:
    name: str
    address: Address
    employees: int

@dataclass
class RegisterCompanyParams:
    company: CompanyInfo

@dataclass
class RegisterCompanyResult:
    company_id: int
    name: str
    location: str
    contact_email: str

class RegisterCompany(Method):
    def execute(self, params: RegisterCompanyParams) -> RegisterCompanyResult:
        return RegisterCompanyResult(
            company_id=999,
            name=params.company.name,
            location=params.company.address.city,
            contact_email=params.company.address.contact.email,
        )

rpc = JSONRPC(version='2.0')
rpc.register('register_company', RegisterCompany())
```

**Request:**

```json title="deep_nested_request.json"
{
  "jsonrpc": "2.0",
  "method": "register_company",
  "params": {
    "company": {
      "name": "Acme Corp",
      "address": {
        "street": "456 Business Ave",
        "city": "San Francisco",
        "contact": {
          "email": "info@acme.com",
          "phone": "+1-555-0100"
        }
      },
      "employees": 150
    }
  },
  "id": 1
}
```

**Response:**

```json title="deep_nested_response.json"
{
  "jsonrpc": "2.0",
  "result": {
    "company_id": 999,
    "name": "Acme Corp",
    "location": "San Francisco",
    "contact_email": "info@acme.com"
  },
  "id": 1
}
```

## Union and Optional Types

```python title="union_optional.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class ProcessPaymentParams:
    amount: float
    currency: str
    method: str  # "card" | "paypal" | "crypto"
    card_number: str | None = None
    paypal_email: str | None = None
    crypto_address: str | None = None

@dataclass
class PaymentResult:
    transaction_id: str
    amount: float
    currency: str
    status: str

class ProcessPayment(Method):
    def execute(self, params: ProcessPaymentParams) -> PaymentResult:
        return PaymentResult(
            transaction_id='tx_123',
            amount=params.amount,
            currency=params.currency,
            status='completed',
        )

rpc = JSONRPC(version='2.0')
rpc.register('process_payment', ProcessPayment())
```

**Request (card):**

```json title="payment_card_request.json"
{
  "jsonrpc": "2.0",
  "method": "process_payment",
  "params": {
    "amount": 99.99,
    "currency": "USD",
    "method": "card",
    "card_number": "4111111111111111"
  },
  "id": 1
}
```

**Response:**

```json title="payment_response.json"
{
  "jsonrpc": "2.0",
  "result": {
    "transaction_id": "tx_123",
    "amount": 99.99,
    "currency": "USD",
    "status": "completed"
  },
  "id": 1
}
```

## Lists and Dicts

```python title="collections.py"
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

@dataclass
class Item:
    id: int
    name: str
    price: float

@dataclass
class CreateOrderParams:
    items: list[Item]
    metadata: dict[str, str | int]
    discounts: list[float]

@dataclass
class OrderResult:
    order_id: str
    items_count: int
    total: float
    metadata: dict[str, str | int]

class CreateOrder(Method):
    def execute(self, params: CreateOrderParams) -> OrderResult:
        total = sum(item.price for item in params.items)
        discount_total = sum(params.discounts)
        return OrderResult(
            order_id='ORD-001',
            items_count=len(params.items),
            total=round(total - discount_total, 2),
            metadata=params.metadata,
        )

rpc = JSONRPC(version='2.0')
rpc.register('create_order', CreateOrder())
```

**Request:**

```json title="order_request.json"
{
  "jsonrpc": "2.0",
  "method": "create_order",
  "params": {
    "items": [
      {"id": 1, "name": "Laptop", "price": 999.99},
      {"id": 2, "name": "Mouse", "price": 29.99}
    ],
    "metadata": {
      "customer_id": "12345",
      "source": "web",
      "priority": 1
    },
    "discounts": [50.0, 10.0]
  },
  "id": 1
}
```

**Response:**

```json title="order_response.json"
{
  "jsonrpc": "2.0",
  "result": {
    "order_id": "ORD-001",
    "items_count": 2,
    "total": 969.98,
    "metadata": {
      "customer_id": "12345",
      "source": "web",
      "priority": 1
    }
  },
  "id": 1
}
```

## Complex Type Combinations

```python title="complex_types.py"
from dataclasses import dataclass
from typing import Literal
from jsonrpc import JSONRPC, Method

@dataclass
class ConfigParams:
    mode: Literal["development", "production", "testing"]
    features: dict[str, bool]
    allowed_ips: list[str] | None = None
    max_retries: int = 3

@dataclass
class SecurityConfig:
    ip_whitelist: list[str]
    max_retries: int

@dataclass
class ConfigResult:
    mode: str
    features_enabled: list[str]
    security: SecurityConfig

class UpdateConfig(Method):
    def execute(self, params: ConfigParams) -> ConfigResult:
        return ConfigResult(
            mode=params.mode,
            features_enabled=[k for k, v in params.features.items() if v],
            security=SecurityConfig(
                ip_whitelist=params.allowed_ips or [],
                max_retries=params.max_retries,
            ),
        )

rpc = JSONRPC(version='2.0')
rpc.register('update_config', UpdateConfig())
```

**Request:**

```json title="config_request.json"
{
  "jsonrpc": "2.0",
  "method": "update_config",
  "params": {
    "mode": "production",
    "features": {
      "analytics": true,
      "beta_features": false,
      "logging": true
    },
    "allowed_ips": ["192.168.1.1", "10.0.0.1"]
  },
  "id": 1
}
```

**Response:**

```json title="config_response.json"
{
  "jsonrpc": "2.0",
  "result": {
    "mode": "production",
    "features_enabled": ["analytics", "logging"],
    "security": {
      "ip_whitelist": ["192.168.1.1", "10.0.0.1"],
      "max_retries": 3
    }
  },
  "id": 1
}
```

## Key Points

- **Nested dataclasses**: Automatic recursive validation
- **Union types**: `str | int` for multiple allowed types
- **Optional**: `str | None` for nullable fields
- **Lists**: `list[Item]` validates each element
- **Dicts**: `dict[str, int]` validates keys and values
- **Literal**: `Literal["a", "b"]` restricts to specific values

!!! tip "Nested results too"
    Result dataclasses can be nested just like params — the library serializes them recursively.

## What's Next?

→ [Context](05-context.md) - Authentication and request context
