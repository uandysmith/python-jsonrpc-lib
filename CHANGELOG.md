# Changelog

## 0.3.2

**Bug fixes:**

- `add_security_scheme`: replaced `**kwargs` with `options: dict` parameter, fixing inability to create `apiKey` schemes (conflicting `name` parameter, `in` as Python reserved word)
- `_convert_value`: Union types containing multiple dataclasses now correctly try all variants instead of crashing on the first mismatch
- `simplify_id` flag now consistently applies to JSONRPCError schema in OpenAPI output
- `unregister()` now clears `.rpc` attribute, allowing re-registration of the same Method instance
- `max_concurrent` parameter is now validated (`-1` or `>= 1`); previously `0` caused a silent deadlock
- `version` parameter is now validated at init; invalid values like `'3.0'` raise `ValueError`
- Fixed `bearer_format` typo in tests (should be `bearerFormat` per OpenAPI spec)
- Fixed OpenAPI tutorial example to match actual generated output

## 0.3.1 (First Public Release)

- JSON-RPC 1.0 and 2.0 support
- Dataclass-based parameter validation
- Built-in OpenAPI generation
- Hierarchical context support
- Decorator API for prototyping
- Async/sync methods
- Batch request handling
- Strict mode by default
- Zero external dependencies
