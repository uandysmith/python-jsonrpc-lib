"""OpenAPI documentation generator for JSON-RPC methods."""

import json
import types
from dataclasses import MISSING, fields, is_dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

if TYPE_CHECKING:
    from .jsonrpc import JSONRPC


def _get_docstring(obj: Any) -> str | None:
    doc: str | None = getattr(obj, '__doc__', None)
    if doc:
        return doc.strip()
    return None


def _authored_docstring(dc: type) -> str | None:
    """The dataclass's own docstring, or None if @dataclass generated it.

    @dataclass fills __doc__ with the constructor signature when the author
    wrote none, which then shipped in the public spec as the schema description:
    `Outer(inner: __main__.Inner)`, module paths and all. An absent description
    is better than one that reads like a leak.
    """
    doc = getattr(dc, '__doc__', None)
    if not doc:
        return None
    stripped = doc.strip()
    generated = stripped.startswith(f'{dc.__name__}(') and stripped.endswith(')') and '\n' not in stripped
    return None if generated else stripped


def _schema_name(t: type, registry: dict[type, str]) -> str:
    """Pick the components/schemas key for a dataclass.

    Short name while it is free, because that is what makes a spec readable.
    Two different dataclasses that happen to share a name used to collide
    silently: the second reused the first's schema, so one of the two methods
    was documented with the other's fields. On a collision the loser gets its
    module path prefixed - OpenAPI allows dots in schema names.
    """
    assigned = registry.get(t)
    if assigned is not None:
        return assigned

    name = t.__name__
    if name in registry.values():
        name = f'{t.__module__}.{t.__qualname__}'
    registry[t] = name
    return name


def _type_to_jsonschema(t: type, schemas: dict[str, Any], registry: dict[type, str] | None = None) -> dict[str, Any]:
    """Convert Python type annotation to JSON Schema.

    Args:
        t: Type annotation
        schemas: Dict to add nested schemas to
        registry: Dataclass -> schema name, so two same-named dataclasses do not
            share one entry

    Returns:
        JSON Schema dict
    """
    if registry is None:
        registry = {}
    origin = get_origin(t)

    if t is type(None):
        return {'type': 'null'}

    if origin is Union or isinstance(t, types.UnionType):
        args = get_args(t)
        # Check for Optional (T | None)
        if len(args) == 2 and type(None) in args:
            other = args[0] if args[1] is type(None) else args[1]
            other_schema = _type_to_jsonschema(other, schemas, registry)
            return {'oneOf': [other_schema, {'type': 'null'}]}
        return {'oneOf': [_type_to_jsonschema(a, schemas, registry) for a in args]}

    if origin is Literal:
        args = get_args(t)
        return {'enum': list(args)}

    if origin is list:
        args = get_args(t)
        if args:
            return {
                'type': 'array',
                'items': _type_to_jsonschema(args[0], schemas, registry),
            }
        return {'type': 'array'}

    if origin is dict:
        args = get_args(t)
        if args and len(args) == 2:
            return {
                'type': 'object',
                'additionalProperties': _type_to_jsonschema(args[1], schemas, registry),
            }
        return {'type': 'object'}

    if t is int:
        return {'type': 'integer'}
    if t is float:
        return {'type': 'number'}
    if t is str:
        return {'type': 'string'}
    if t is bool:
        return {'type': 'boolean'}
    if t is Any:
        return {}

    if is_dataclass(t) and isinstance(t, type):
        schema_name = _schema_name(t, registry)
        if schema_name not in schemas:
            # Reserve the key before recursing: a self-referencing dataclass
            # would otherwise recurse forever.
            schemas[schema_name] = {}
            schemas[schema_name] = _dataclass_to_jsonschema(t, schemas, registry)
        return {'$ref': f'#/components/schemas/{schema_name}'}

    return {}


def _dataclass_to_jsonschema(
    dc: type, schemas: dict[str, Any], registry: dict[type, str] | None = None
) -> dict[str, Any]:
    """Convert dataclass to JSON Schema.

    Args:
        dc: Dataclass type
        schemas: Dict to add nested schemas to
        registry: Dataclass -> schema name mapping (see _schema_name)

    Returns:
        JSON Schema dict
    """
    if registry is None:
        registry = {}
    if not is_dataclass(dc):
        raise ValueError(f'Expected dataclass, got {type(dc).__name__}')

    type_hints = get_type_hints(dc)
    field_list = fields(dc)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for f in field_list:
        field_type = type_hints.get(f.name, Any)
        field_schema = _type_to_jsonschema(field_type, schemas, registry)

        if f.metadata and 'description' in f.metadata:
            field_schema['description'] = f.metadata['description']

        properties[f.name] = field_schema

        if f.default is MISSING and f.default_factory is MISSING:
            required.append(f.name)

    schema: dict[str, Any] = {
        'type': 'object',
        'properties': properties,
    }

    if required:
        schema['required'] = required

    doc = _authored_docstring(dc)
    if doc:
        schema['description'] = doc

    return schema


class OpenAPIGenerator:
    """Generates OpenAPI 3.0 specification from registered JSON-RPC methods."""

    def __init__(
        self,
        rpc: 'JSONRPC',
        base_url: str = '/jsonrpc',
        title: str = 'JSON-RPC API',
        version: str = '1.0.0',
        description: str | None = None,
        simplify_id: bool = True,
    ) -> None:
        """Initialize OpenAPI generator.

        Args:
            rpc: JSONRPC instance with registered methods
            base_url: Base URL path for the JSON-RPC endpoint
            title: API title
            version: API version
            description: API description
            simplify_id: If True (default), represent id as {"type": "integer"} in schemas.
                If False, use {"oneOf": [{"type": "string"}, {"type": "integer"}]} per spec.
                Simplification reduces duplicate examples in documentation viewers (e.g. RapiDoc),
                where each oneOf variant generates a separate example differing only by id type.
        """
        self.rpc = rpc
        self.base_url = base_url.rstrip('/')
        self.title = title
        self.version = version
        self.description = description
        self.simplify_id = simplify_id
        self.security_schemes: dict[str, dict[str, Any]] = {}
        self.global_headers: list[dict[str, Any]] = []
        self.global_security: list[dict[str, list[str]]] = []

    def add_security_scheme(
        self,
        name: str,
        scheme_type: Literal['apiKey', 'http', 'oauth2', 'openIdConnect'],
        options: dict[str, Any] | None = None,
    ) -> None:
        """Add authentication/security scheme.

        Args:
            name: Scheme name (used in security requirements)
            scheme_type: Type of security scheme
            options: Additional scheme properties as a dict.
                Keys are passed directly into the OpenAPI security scheme object.

        Example:
            >>> openapi.add_security_scheme(
            ...     "bearerAuth",
            ...     scheme_type="http",
            ...     options={"scheme": "bearer", "bearerFormat": "JWT"},
            ... )
            >>> openapi.add_security_scheme(
            ...     "apiKeyAuth",
            ...     scheme_type="apiKey",
            ...     options={"name": "X-API-Key", "in": "header"},
            ... )
        """
        scheme: dict[str, Any] = {'type': scheme_type}
        if options:
            scheme.update(options)
        self.security_schemes[name] = scheme

    def add_header(
        self,
        name: str,
        description: str,
        required: bool = False,
        schema: dict[str, Any] | None = None,
    ) -> None:
        """Add global header parameter.

        Args:
            name: Header name (e.g., "Authorization")
            description: Header description
            required: Whether header is required
            schema: JSON Schema for header value

        Example:
            >>> openapi.add_header(
            ...     name="Authorization",
            ...     description="Bearer token for authentication",
            ...     required=True,
            ...     schema={"type": "string", "pattern": "^Bearer .+$"}
            ... )
        """
        header: dict[str, Any] = {
            'name': name,
            'in': 'header',
            'description': description,
            'required': required,
            'schema': schema or {'type': 'string'},
        }
        self.global_headers.append(header)

    def add_security_requirement(
        self,
        scheme_name: str,
        scopes: list[str] | None = None,
    ) -> None:
        """Add global security requirement.

        Args:
            scheme_name: Name of security scheme to require
            scopes: OAuth2 scopes (if applicable)
        """
        self.global_security.append({scheme_name: scopes or []})

    def _construct_method_path(self, prefix: str, method_name: str) -> str:
        """Construct path for method.

        Args:
            prefix: Method group prefix (e.g., "math")
            method_name: Method name (e.g., "add")

        Returns:
            Path string (e.g., "/jsonrpc#math.add")
        """
        full_name = f'{prefix}.{method_name}' if prefix else method_name
        return f'{self.base_url}#{full_name}'

    def _get_method_summary(self, method: Any) -> str:
        """Extract first line of docstring as summary.

        Args:
            method: Method instance

        Returns:
            Summary string
        """
        doc = _get_docstring(method.__class__)
        if doc:
            return doc.split('\n')[0].strip()
        # Fallback to class name if no docstring
        name: str = method.__class__.__name__
        return name

    def _generate_from_group(
        self,
        group: Any,
        prefix: str,
        schemas: dict[str, Any],
        paths: dict[str, Any],
        tags: list[dict[str, str]],
        seen_tags: set[str],
        registry: dict[type, str],
    ) -> None:
        """Recursively generate OpenAPI entries from group tree.

        This method traverses the MethodGroup hierarchy and generates
        OpenAPI paths for all methods in the tree.

        Args:
            group: Current MethodGroup
            prefix: Accumulated path prefix (e.g., "math", "sudo.user")
            schemas: Schemas dict to populate
            paths: Paths dict to populate
            tags: Tags list to populate
            seen_tags: Set of seen tag names
            registry: Dataclass -> schema name mapping, shared across the tree
        """
        tag_name = prefix if prefix else 'default'
        if tag_name not in seen_tags:
            tag_desc = f"Methods in '{prefix}' group" if prefix else 'Root-level methods'
            tags.append({'name': tag_name, 'description': tag_desc})
            seen_tags.add(tag_name)

        # Generate for local methods (not recursive)
        for method_name in group.list_methods(recursive=False):
            method = group.get_method(method_name)
            full_name = f'{prefix}.{method_name}' if prefix else method_name
            method_path = self._construct_method_path(prefix, method_name)

            # Generate request schema
            request_schema = self._generate_method_request_schema(full_name, method, schemas, registry)
            schemas[f'{full_name}_request'] = request_schema

            # Generate response schema
            response_schema = self._generate_method_response_schema(full_name, method, schemas, registry)
            schemas[f'{full_name}_response'] = response_schema

            operation: dict[str, Any] = {
                'operationId': full_name.replace('.', '_'),
                'summary': self._get_method_summary(method),
                'tags': [tag_name],
                'requestBody': {
                    'required': True,
                    'content': {'application/json': {'schema': {'$ref': f'#/components/schemas/{full_name}_request'}}},
                },
                'responses': {
                    '200': {
                        'description': 'Successful response',
                        'content': {
                            'application/json': {'schema': {'$ref': f'#/components/schemas/{full_name}_response'}}
                        },
                    },
                    'default': {
                        'description': 'JSON-RPC Error',
                        'content': {'application/json': {'schema': {'$ref': '#/components/schemas/JSONRPCError'}}},
                    },
                },
            }

            doc = _get_docstring(method.__class__)
            if doc:
                operation['description'] = doc

            if self.global_headers:
                operation['parameters'] = self.global_headers.copy()

            paths[method_path] = {'post': operation}

        # Recurse into subgroups
        for subgroup_name, subgroup in group.get_all_groups().items():
            new_prefix = f'{prefix}.{subgroup_name}' if prefix else subgroup_name
            self._generate_from_group(subgroup, new_prefix, schemas, paths, tags, seen_tags, registry)

    def generate(self) -> dict[str, Any]:
        """Generate OpenAPI 3.0 specification with per-method paths.

        Each JSON-RPC method gets its own path entry for better
        documentation viewer experience.

        Returns:
            OpenAPI spec as dict
        """
        schemas: dict[str, Any] = {}
        paths: dict[str, Any] = {}
        tags: list[dict[str, str]] = []
        seen_tags: set[str] = set()
        registry: dict[type, str] = {}

        # Generate paths and schemas by traversing group tree
        root_group = self.rpc.get_root_group()
        self._generate_from_group(root_group, '', schemas, paths, tags, seen_tags, registry)

        # Build OpenAPI spec
        spec: dict[str, Any] = {
            'openapi': '3.0.3',
            'info': {
                'title': self.title,
                'version': self.version,
            },
            'tags': tags,
            'paths': paths,
            'components': {
                'schemas': {
                    **schemas,
                    'JSONRPCError': {
                        'type': 'object',
                        'properties': {
                            'jsonrpc': {'const': '2.0'},
                            'error': {
                                'type': 'object',
                                'properties': {
                                    'code': {'type': 'integer'},
                                    'message': {'type': 'string'},
                                    'data': {},
                                },
                                'required': ['code', 'message'],
                            },
                            'id': self._error_id_schema(),
                        },
                        'required': ['jsonrpc', 'error', 'id'],
                    },
                }
            },
        }

        if self.description:
            spec['info']['description'] = self.description

        if self.security_schemes:
            spec['components']['securitySchemes'] = self.security_schemes

        if self.global_security:
            spec['security'] = self.global_security

        return spec

    def _id_schema(self) -> dict[str, Any]:
        """Return JSON Schema for the request/response id field."""
        if self.simplify_id:
            return {'type': 'integer'}
        return {'oneOf': [{'type': 'string'}, {'type': 'integer'}]}

    def _error_id_schema(self) -> dict[str, Any]:
        """Return JSON Schema for the error response id field (includes null)."""
        if self.simplify_id:
            return {'oneOf': [{'type': 'integer'}, {'type': 'null'}]}
        return {'oneOf': [{'type': 'string'}, {'type': 'integer'}, {'type': 'null'}]}

    def _generate_method_request_schema(
        self,
        full_name: str,
        method: Any,
        schemas: dict[str, Any],
        registry: dict[type, str],
    ) -> dict[str, Any]:
        """Generate JSON Schema for method request."""
        schema: dict[str, Any] = {
            'type': 'object',
            'properties': {
                'jsonrpc': {'const': '2.0'},
                'method': {'const': full_name},
                'id': self._id_schema(),
            },
            'required': ['jsonrpc', 'method', 'id'],
        }

        doc = _get_docstring(method.__class__)
        if doc:
            schema['description'] = doc

        if method.params_type is not None and method.params_type is not type(None):
            params_schema = _dataclass_to_jsonschema(method.params_type, schemas, registry)
            schema['properties']['params'] = params_schema
            schema['required'].append('params')

        return schema

    def _generate_method_response_schema(
        self,
        full_name: str,
        method: Any,
        schemas: dict[str, Any],
        registry: dict[type, str],
    ) -> dict[str, Any]:
        """Generate JSON Schema for method response."""
        schema: dict[str, Any] = {
            'type': 'object',
            'properties': {
                'jsonrpc': {'const': '2.0'},
                'id': self._id_schema(),
            },
            'required': ['jsonrpc', 'result', 'id'],
            'description': f'Response for {full_name}',
        }

        if method.result_type is not None:
            result_schema = _type_to_jsonschema(method.result_type, schemas, registry)
            schema['properties']['result'] = result_schema
        else:
            schema['properties']['result'] = {}  # Any type

        return schema

    def generate_json(self, indent: int = 2) -> str:
        """Generate OpenAPI spec as JSON string.

        Deliberately json.dumps and not `self.rpc.serialize()`. That hook exists
        to encode a *response* - it is documented as overridable with a narrower
        signature, `def serialize(self, data)`, and with libraries such as orjson
        that have no `indent` keyword at all. Passing indent into it made every
        such override raise TypeError here, on a call the author never made.

        A spec is not a response either: it is this generator's own output, plain
        dicts and strings by construction, with nothing for a custom encoder to
        do.

        Args:
            indent: JSON indentation level

        Returns:
            JSON string
        """
        return json.dumps(self.generate(), indent=indent)

    def generate_yaml(self) -> str:
        """Generate OpenAPI spec as YAML string.

        Requires PyYAML to be installed.

        Returns:
            YAML string

        Raises:
            ImportError: If PyYAML is not installed
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            raise ImportError('PyYAML is required for YAML output. Install with: pip install pyyaml')

        result: str = yaml.dump(self.generate(), default_flow_style=False, sort_keys=False)
        return result
