"""Executes the code the documentation tells readers to copy.

These tests compile the fenced blocks out of docs/**/*.md rather than
transcribing them, so a documented example that stops working fails here.

Every block carrying a ``title="..."`` is executed - those are the ones written
as complete files, and the ones a reader will paste. Bare snippets are
illustrative fragments and are only compiled.

Blocks are run per page, in order, into one shared namespace: that is how a
reader meets them, and it lets a later block use what an earlier one defined.

Third-party frameworks are replaced with permissive stubs. The library has no
dependencies and the test suite is not going to grow any, but the point of
executing a Flask or FastAPI example is to check the *library* calls in it -
`OpenAPIGenerator(...)`, `rpc.register(...)`, the handler body. Stubbing the
framework keeps those honest without installing anything.

Two kinds of block cannot simply run, and both are named explicitly rather than
filtered by a pattern, so that the list stays short and reviewable:

- programs that open a socket and serve or connect;
- demonstrations of the library refusing something, which are asserted to raise.
"""

import ast
import asyncio
import contextlib
import importlib.machinery
import io
import json
import logging
import re
import sys
import types
import unittest
from pathlib import Path


def repo_root() -> Path:
    path = Path(__file__).resolve()
    for candidate in path.parents:
        if (candidate / 'pyproject.toml').exists():
            return candidate
    raise RuntimeError('repository root not found')


DOCS = repo_root() / 'docs'
TITLED_BLOCK = re.compile(r'```python title="([^"]+)"\n(.*?)```', re.S)
ANY_PYTHON_BLOCK = re.compile(r'```python[^\n]*\n(.*?)```', re.S)

STUBBED_PACKAGES = (
    'flask',
    'flask_cors',
    'fastapi',
    'starlette',
    'jwt',
    'redis',
    'websockets',
    'paho',
    'requests',
    'aiohttp',
    'sqlalchemy',
)

# Blocks that are whole programs: they bind or dial a socket and then block.
NOT_EXECUTABLE = {
    'ipc_server.py': 'binds a Unix socket and serves forever',
    'ipc_client.py': 'dials a Unix socket that nothing is listening on',
    'websocket_client.py': 'opens a websocket connection',
}

# Blocks whose whole point is that the library refuses them.
EXPECTED_TO_RAISE = {
    'fail_fast.py': TypeError,
    'context_mismatch.py': TypeError,
}

# Deliberately does NOT import any jsonrpc name. It used to, and that hid the
# thing this file exists to catch: a block that uses InvalidParamsError without
# importing it ran fine here and raises NameError for the reader who pastes it.
# The stdlib names stay because a bare snippet may legitimately assume `json` is
# around, and because none of them is what the documentation is teaching.
PREAMBLE = (
    'import asyncio, json, time, logging\n'
    'from dataclasses import dataclass, field\n'
    'from typing import Any, Literal, Optional\n'
)


class _Stub:
    """Stands in for whatever a stubbed package exposes."""

    def __init__(self, name='stub'):
        self._name = name

    def __getattr__(self, item):
        if item.startswith('__') and item.endswith('__'):
            raise AttributeError(item)
        return _Stub(f'{self._name}.{item}')

    def __call__(self, *args, **kwargs):
        # Decorator use, e.g. @app.route(...): hand the function back unchanged.
        if len(args) == 1 and not kwargs and callable(args[0]):
            return args[0]
        return _Stub(f'{self._name}()')

    def __getitem__(self, item):
        return _Stub(f'{self._name}[]')

    def __iter__(self):
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __repr__(self):
        return f'<stub {self._name}>'


class _StubModule(types.ModuleType):
    def __getattr__(self, item):
        if item.startswith('__'):
            raise AttributeError(item)
        return _Stub(f'{self.__name__}.{item}')


class _StubLoader:
    def create_module(self, spec):
        return _StubModule(spec.name)

    def exec_module(self, module):
        pass


class _StubFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] not in STUBBED_PACKAGES:
            return None
        spec = importlib.machinery.ModuleSpec(fullname, _StubLoader())
        spec.submodule_search_locations = []  # so `import fastapi.responses` resolves
        return spec


def titled_blocks(page: str) -> list[tuple[str, str]]:
    return TITLED_BLOCK.findall((DOCS / page).read_text())


class DocumentationExecutionCase(unittest.TestCase):
    """Base class that installs the framework stubs for the duration."""

    @classmethod
    def setUpClass(cls):
        cls._finder = _StubFinder()
        sys.meta_path.insert(0, cls._finder)

    @classmethod
    def tearDownClass(cls):
        sys.meta_path.remove(cls._finder)
        for name in list(sys.modules):
            if name.split('.')[0] in STUBBED_PACKAGES:
                del sys.modules[name]

    def run_block(self, source: str, origin: str, namespace: dict) -> None:
        """Execute one block, awaiting it if it uses top-level await.

        The examples print and log; that output belongs to the example, not to
        the test run, so it goes nowhere.
        """
        code = compile(source, origin, 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink), _quiet_logging():
            result = eval(code, namespace)  # noqa: S307 - the input is this repository's own docs
            if result is not None:
                asyncio.run(_await(result))


@contextlib.contextmanager
def _quiet_logging():
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


async def _await(coro):
    return await coro


class TestEveryDocumentedFileRuns(DocumentationExecutionCase):
    """Every `title=`d block executes, page by page, in reading order.

    Two examples once passed `servers=` and `headers=` to OpenAPIGenerator -
    arguments it has never had - in the "Complete Application" block of both
    integration guides, the one block a reader is most likely to copy whole.
    Nothing caught it, because this file only ever executed one page.
    """

    def _pages(self):
        return sorted(page for page in DOCS.rglob('*.md') if TITLED_BLOCK.search(page.read_text()))

    def test_every_block_executes(self):
        failures = []
        executed = 0

        for page in self._pages():
            namespace: dict = {}
            exec(compile(PREAMBLE, 'preamble', 'exec'), namespace)  # noqa: S102

            for name, body in TITLED_BLOCK.findall(page.read_text()):
                if name in NOT_EXECUTABLE:
                    continue
                origin = f'{page.relative_to(DOCS)}::{name}'
                expected = EXPECTED_TO_RAISE.get(name)
                try:
                    self.run_block(body, origin, namespace)
                    if expected is not None:
                        failures.append(f'{origin}: expected {expected.__name__}, nothing raised')
                    executed += 1
                except Exception as e:  # noqa: BLE001
                    if expected is not None and isinstance(e, expected):
                        executed += 1
                        continue
                    failures.append(f'{origin}: {type(e).__name__}: {e}')

        self.assertEqual(failures, [], f'{len(failures)} documented example(s) do not run:\n' + '\n'.join(failures))
        self.assertGreater(executed, 60, 'the harness stopped finding blocks - check the fence pattern')

    def test_the_unexecutable_list_stays_short_and_true(self):
        """Anything on that list must still exist, or the list is lying."""
        names = {name for page in self._pages() for name, _ in TITLED_BLOCK.findall(page.read_text())}
        for skipped in NOT_EXECUTABLE:
            self.assertIn(skipped, names, f'{skipped} is listed as unexecutable but no longer exists')
        for expected in EXPECTED_TO_RAISE:
            self.assertIn(expected, names)


class TestMiddlewarePage(DocumentationExecutionCase):
    """The full middleware assembly on docs/advanced/middleware.md."""

    REQUEST = json.dumps(
        {
            'jsonrpc': '2.0',
            'method': 'public.v1.api.protected.search.items',
            'params': {'query': 'python'},
            'id': 1,
        }
    )

    def setUp(self):
        quiet = _quiet_logging()  # the documented wrappers log every call
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

        blocks = titled_blocks('advanced/middleware.md')
        source = '\n'.join(body for name, body in blocks if not name.startswith('flask'))
        namespace: dict = {}
        self.run_block(PREAMBLE + source, 'middleware.md', namespace)
        self.ns = namespace
        self.rpc = namespace['rpc']
        self.AppContext = namespace['AppContext']

    def _call(self, user_id):
        context = self.AppContext(user_id=user_id, ip_address='10.0.0.1')
        return json.loads(self.rpc.handle(self.REQUEST, context=context))

    def test_an_anonymous_caller_is_refused(self):
        data = self._call(None)
        self.assertEqual(data['error']['code'], -32010)
        self.assertEqual(data['error']['message'], 'Authentication required')

    def test_the_refusal_is_not_an_internal_error(self):
        """A guard that raises the wrong exception class refuses nobody visibly.

        Only a JSONRPCError subclass keeps its code and text; everything else is
        answered with a bare -32603 plus a traceback at ERROR, once per
        unauthorized attempt. The docstrings on MethodGroup used to raise
        PermissionError, so a reader who copied them got exactly that.
        """
        error = self._call(None)['error']
        self.assertNotEqual(error['code'], -32603)
        self.assertNotEqual(error['message'], 'Internal error')

    def test_an_authenticated_caller_is_served(self):
        data = self._call(42)
        self.assertEqual(data['result'][0]['user_id'], 42)

    def test_a_primed_cache_does_not_serve_an_anonymous_caller(self):
        """The cache is mounted innermost precisely so this cannot happen.

        A cache that returns before delegating skips everything below it. With
        the cache above the guard, priming it with an authorized call would let
        the next anonymous caller read the same bytes.
        """
        self.assertEqual(self._call(42)['result'][0]['user_id'], 42)
        self.assertEqual(self._call(None)['error']['message'], 'Authentication required')

    def test_the_cache_key_separates_sibling_registrations(self):
        """Keyed on the class name, two registrations of one class collide."""
        caching_group = self.ns['CachingGroup']()

        class Call:
            def __init__(self, path):
                self.path = path
                self.params = None

        self.assertNotEqual(
            caching_group._cache_key(Call('report.mine')),
            caching_group._cache_key(Call('report.everyone')),
        )

    def test_the_documented_wrappers_override_both_hooks(self):
        """Every documented wrapper must cover async methods too."""
        from jsonrpc import MethodGroup

        for name in ('LoggingGroup', 'RateLimitGroup', 'RequireAuthGroup'):
            group = self.ns[name]
            with self.subTest(group=name):
                self.assertIsNot(group.around_call, MethodGroup.around_call)
                self.assertIsNot(group.around_call_async, MethodGroup.around_call_async)


class TestTransportExamplesCheckForNone(unittest.TestCase):
    """handle() returns None for every notification, which is most of these bugs.

    An unknown or empty method name still takes the notification branch, so
    `{"jsonrpc":"2.0","method":""}` - 29 bytes, no credentials, no knowledge of
    the method table - reaches the return value. Dereferencing it kills a socket
    server outright, and a restart-always supervisor does not heal it because the
    same packet re-kills each restart.
    """

    def test_no_example_dereferences_the_result_without_a_check(self):
        offenders = []

        for path in sorted(DOCS.rglob('*.md')):
            for block in ANY_PYTHON_BLOCK.findall(path.read_text()):
                lines = block.splitlines()
                for index, line in enumerate(lines):
                    if not re.search(r'=\s*(await\s+)?rpc\.handle(_async)?\(', line):
                        continue
                    variable = line.split('=')[0].strip()
                    if not variable.isidentifier():
                        continue
                    following = '\n'.join(lines[index + 1 : index + 6])
                    guarded = (
                        f'if {variable} is not None' in following
                        or f'if {variable}:' in following
                        or f'{variable} is None' in following
                    )
                    # Attribute access crashes; handing the value to a transport
                    # call sends a phantom message. print() is neither - a
                    # tutorial printing the answer to a request that carries an
                    # id is not a defect.
                    dereferenced = re.search(rf'\b{re.escape(variable)}\b\s*\.', following)
                    passed_on = re.search(rf'(?<!print)\(\s*[^)]*\b{re.escape(variable)}\b', following)
                    if (dereferenced or passed_on) and not guarded:
                        offenders.append(f'{path.relative_to(DOCS)}: {line.strip()}')

        self.assertEqual(offenders, [], f'unguarded handle() results: {offenders}')

    def test_no_example_returns_the_result_directly(self):
        """`return rpc.handle(...)` skips the None check by construction."""
        offenders = []
        for path in sorted(DOCS.rglob('*.md')):
            for block in ANY_PYTHON_BLOCK.findall(path.read_text()):
                for line in block.splitlines():
                    if re.search(r'return\s+(await\s+)?rpc\.handle(_async)?\(', line):
                        offenders.append(f'{path.relative_to(DOCS)}: {line.strip()}')
        self.assertEqual(offenders, [], f'results returned without a None check: {offenders}')


class TestTheDocumentedDefaultsAreTheRealOnes(unittest.TestCase):
    """api-reference.md lists every constructor parameter and its default.

    Two pages went on describing `max_concurrent=os.cpu_count()` after the
    default became 64 — and one of them contradicted itself 57 lines apart. A
    table of defaults is exactly the kind of prose that rots silently, so it is
    checked against the signature rather than read.
    """

    PARAMETER_ROW = re.compile(r'^\| `(\w+)` \| .*? \| `?([^|`]*)`? \|', re.M)

    def _documented(self):
        text = (DOCS / 'api-reference.md').read_text()
        start = text.index('| Parameter | Type | Default | Description |')
        end = text.index('**Default params mode', start)
        return {name: default.strip() for name, default in self.PARAMETER_ROW.findall(text[start:end])}

    def test_every_parameter_appears_with_its_real_default(self):
        import inspect

        from jsonrpc import JSONRPC

        documented = self._documented()
        actual = {
            name: parameter.default
            for name, parameter in inspect.signature(JSONRPC.__init__).parameters.items()
            if name != 'self'
        }

        self.assertEqual(set(documented), set(actual), 'the table and the signature list different parameters')

        for name, default in actual.items():
            with self.subTest(parameter=name):
                # A string default is written `'2.0'` in the table and `2.0` by
                # str(); either spelling is the same fact.
                self.assertIn(
                    documented[name],
                    {str(default), repr(default)},
                    f'api-reference.md says {name} defaults to {documented[name]!r}, it is {default!r}',
                )

    def test_no_page_still_quotes_the_old_concurrency_default(self):
        offenders = [f'{path.relative_to(DOCS)}' for path in DOCS.rglob('*.md') if 'cpu_count' in path.read_text()]
        self.assertEqual(offenders, [], f'pages describing a default that no longer exists: {offenders}')


class TestProseMatchesTheContract(unittest.TestCase):
    def test_no_page_promises_that_handle_always_returns_a_string(self):
        """Eight statements across five pages promised an unqualified string."""
        offenders = []
        for path in DOCS.rglob('*.md'):
            for line in path.read_text().splitlines():
                if line.strip().startswith(('#', '|')) or '```' in line:
                    continue
                lowered = line.lower()
                if 'handle(' not in lowered and 'handle()' not in lowered:
                    continue
                if re.search(r'returns? (a )?json string', lowered) and 'none' not in lowered:
                    offenders.append(f'{path.relative_to(DOCS)}: {line.strip()}')
        self.assertEqual(offenders, [], f'prose contradicting the str | None contract: {offenders}')


if __name__ == '__main__':
    unittest.main()
