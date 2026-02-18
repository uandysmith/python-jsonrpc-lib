"""Shared test fixtures for JSON-RPC tests.

This module contains all dataclasses and Method classes used across
test_jsonrpc_v1.py, test_jsonrpc_v2.py, and test_openapi.py.

All fixtures are extracted from test_jsonrpc_v2.py to provide
a single source of truth for test methods.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from jsonrpc import InvalidParamsError, Method


@dataclass
class AddParams:
    """Parameters for add/subtract operations."""

    a: int
    b: int


@dataclass
class OptionalParams:
    """Parameters with optional field."""

    required: str
    optional: str = 'default'


@dataclass
class MultiParams:
    """Parameters for multiply operation."""

    x: int
    y: int
    z: int


@dataclass
class EchoParams:
    """Parameters for echo."""

    message: str


@dataclass
class MathResult:
    """Result as dataclass."""

    operation: str
    result: int


# 3-level nested dataclass for testing
@dataclass
class Contact:
    """Level 1: Basic contact info."""

    email: str
    phone: str


@dataclass
class Address:
    """Level 2: Address with nested contact."""

    street: str
    city: str
    contact: Contact


@dataclass
class CompanyInfo:
    """Level 3: Company with nested address."""

    name: str
    founded: int
    address: Address


@dataclass
class UserAddress:
    """Address for user."""

    city: str
    country: str


@dataclass
class UserInfo:
    """User with nested address."""

    name: str
    age: int
    address: UserAddress


class AddMethod(Method):
    """Add two numbers together."""

    def execute(self, params: AddParams) -> int:
        return params.a + params.b


class SubtractMethod(Method):
    """Subtract two numbers."""

    def execute(self, params: AddParams) -> int:
        return params.a - params.b


class MultiplyMethod(Method):
    """Multiply three numbers."""

    def execute(self, params: MultiParams) -> int:
        return params.x * params.y * params.z


class OptionalMethod(Method):
    """Method with optional parameters."""

    def execute(self, params: OptionalParams) -> str:
        return f'{params.required}:{params.optional}'


class NoParamsMethod(Method):
    """Ping without parameters."""

    def execute(self, params: None) -> str:
        return 'pong'


class EchoMethod(Method):
    """Echo back a message."""

    def execute(self, params: EchoParams) -> str:
        return params.message


class DataclassResultMethod(Method):
    """Return dataclass result."""

    def execute(self, params: AddParams) -> MathResult:
        return MathResult(operation='add', result=params.a + params.b)


class NestedCompanyMethod(Method):
    """Process 3-level nested dataclass."""

    def execute(self, params: CompanyInfo) -> str:
        return (
            f'{params.name} founded in {params.founded}, '
            f'located at {params.address.street}, {params.address.city}, '
            f'contact: {params.address.contact.email}'
        )


class InternalCallMethod(Method):
    """Call another method internally and double the result."""

    def execute(self, params: AddParams) -> int:
        result = self.rpc.call_method('math.add', {'a': params.a, 'b': params.b})
        return result * 2


class TypedAddMethod(Method):
    """Add with explicit types."""

    def execute(self, params: AddParams) -> int:
        return params.a + params.b


class WrongTypeMethod(Method):
    """Return wrong type for error testing."""

    def execute(self, params: None) -> int:
        return 'not an int'  # Wrong type!


class AsyncDataclassResultMethod(Method):
    """Async method with dataclass result."""

    async def execute(self, params: AddParams) -> MathResult:
        return MathResult(operation='add', result=params.a + params.b)


class NestedDataclassResultMethod(Method):
    """Return nested dataclass."""

    def execute(self, params: None) -> UserInfo:
        address = UserAddress(city='Krakow', country='Poland')
        return UserInfo(name='Jakub', age=25, address=address)


class ListDataclassResultMethod(Method):
    """Return list of dataclasses."""

    def execute(self, params: None) -> list[MathResult]:
        return [
            MathResult(operation='add', result=self.rpc.call_method('math.add', {'a': 2, 'b': 3})),
            MathResult(operation='sub', result=self.rpc.call_method('math.subtract', {'a': 5, 'b': 2})),
        ]


class DictDataclassResultMethod(Method):
    """Return dict of dataclasses."""

    def execute(self, params: None) -> dict[str, MathResult]:
        return {
            'first': MathResult(operation='add', result=10),
            'second': MathResult(operation='mul', result=20),
        }


class AsyncMethod(Method):
    """Async method."""

    async def execute(self, params: None) -> str:
        await asyncio.sleep(0.001)
        return 'async_result'


class ErrorMethod(Method):
    """Method that raises error."""

    def execute(self, params: None) -> str:
        raise InvalidParamsError('Intentional error')


@dataclass
class ComplexTypesParams:
    """Parameters with edge case type annotations for OpenAPI testing."""

    none_type: None  # type(None)
    optional_int: int | None  # Optional
    union_types: int | str | float  # General Union
    literal_val: Literal['a', 'b', 'c']  # Literal
    plain_list: list  # list without args
    plain_dict: dict  # dict without args
    any_value: Any  # Any type
    float_val: float  # float
    bool_val: bool  # bool


class ComplexTypesMethod(Method):
    """Method with complex type annotations for OpenAPI testing."""

    def execute(self, params: ComplexTypesParams) -> dict:
        return {'status': 'ok'}


class NoDocstringMethod(Method):
    """Method without docstring in execute (for testing)."""

    def execute(self, params: None) -> str:
        return 'ok'


@dataclass
class MetadataParams:
    """Parameters with field metadata for OpenAPI description testing."""

    name: str = field(metadata={'description': 'User name'})
    age: int = field(metadata={'description': 'User age'})


@dataclass
class UnionTestParams:
    """Parameters with complex Union types for type system edge case testing."""

    none_first: None | int  # Test None | T ordering
    none_second: int | None  # Test T | None ordering
    plain_list: list  # Plain list type without args
    plain_dict: dict  # Plain dict type without args


class MultiLineDocstringMethod(Method):
    """First line summary.

    Additional details on second line.
    More information here.
    """

    def execute(self, params: None) -> str:
        return 'ok'


class NoResultTypeMethod(Method):
    """Method with result_type explicitly set to None for testing edge case."""

    def execute(self, params: None) -> Any:
        return {'anything': True}


# Manually override result_type to None for testing
# This is defensive code testing - normally result_type is always set
NoResultTypeMethod.result_type = None


class MetadataMethod(Method):
    """Method with metadata params for OpenAPI testing."""

    def execute(self, params: MetadataParams) -> str:
        return f'{params.name} is {params.age} years old'
