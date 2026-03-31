"""Pure-Python JSON-RPC 1.0/2.0 protocol implementation.

This library provides a transport-agnostic implementation of the JSON-RPC protocol,
supporting both version 1.0 and 2.0 specifications.

Example:
    >>> from jsonrpc import JSONRPC, MethodGroup, Method
    >>> from dataclasses import dataclass
    >>>
    >>> @dataclass
    ... class AddParams:
    ...     a: int
    ...     b: int
    ...
    >>> class Add(Method):
    ...     def execute(self, params: AddParams) -> int:
    ...         return params.a + params.b
    ...
    >>> math = MethodGroup()
    >>> math.register("add", Add())
    >>>
    >>> rpc = JSONRPC()
    >>> rpc.register("math", math)
    >>>
    >>> response = rpc.handle('{"jsonrpc":"2.0","method":"math.add","params":{"a":1,"b":2},"id":1}')
    >>> print(response)
    {"jsonrpc": "2.0", "result": 3, "id": 1}
"""

from .errors import (
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    InvalidResultError,
    JSONRPCError,
    MethodNotFoundError,
    ParseError,
    RPCError,
    ServerError,
)
from .jsonrpc import JSONRPC
from .method import Method, MethodGroup
from .openapi import OpenAPIGenerator
from .request import build_notification, build_request, parse_request
from .response import build_error_response, build_response, parse_response
from .types import ErrorResponse, Request, Response, Version
from .validation import validate_params, validate_result_type

__version__ = '0.3.2'

__all__ = [
    # Main classes
    'JSONRPC',
    'Method',
    'MethodGroup',
    # Types
    'Request',
    'Response',
    'ErrorResponse',
    'RPCError',
    'Version',
    # Errors
    'JSONRPCError',
    'ParseError',
    'InvalidRequestError',
    'MethodNotFoundError',
    'InvalidParamsError',
    'InvalidResultError',
    'InternalError',
    'ServerError',
    # Request/Response builders
    'build_request',
    'build_notification',
    'build_response',
    'build_error_response',
    'parse_request',
    'parse_response',
    # Utilities
    'validate_params',
    'validate_result_type',
    # OpenAPI
    'OpenAPIGenerator',
]
