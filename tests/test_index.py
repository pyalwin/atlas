import json
import sqlite3
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from contextlib import closing
from atlas_index.indexer import index_repository, safe_source
from atlas_index.server import GraphStore, create_server


class IndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.repo = cls.base / 'example'
        cls.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(cls.repo)], check=True)
        files = {
            '.gitignore': 'ignored.py\n', 'ignored.py': 'def ignored(): pass\n',
            '.env': 'PRIVATE=hidden\n', 'secrets.json': '{"token":"hidden"}',
            'pkg/__init__.py': 'from .a import work\n',
            'pkg/a.py': '''"""Small jobs that keep the service running."""
from .b import Base, helper
from external_package import unavailable
class Worker(Base):
    def same(self):
        return helper()
    def run(self):
        return self.same()
def same():
    return helper()
def work(value):
    def inside():
        return helper()
    return inside()
def shadow(helper):
    return helper()
def reassigned():
    helper = object()
    return helper()
''',
            'pkg/b.py': 'class Base:\n    """Shared behavior for workers."""\n    pass\ndef helper():\n    """Finish one small job."""\n    return 1\n',
            'consumer/main.py': 'from pkg.a import work\nimport pkg.b as shared\nfrom domain.task import task\nwork(1)\nshared.helper()\ntask()\n',
            'consumer/other.py': 'from pkg import work\nwork(2)\n',
            'src/domain/task.py': 'from .utils import value\ndef task():\n    return value()\n',
            'src/domain/utils.py': 'def value():\n    return 42\n',
            'bad.py': 'def broken(:\n', 'view.tsx': 'export const View = () => null;\n',
            'duplicate.py': 'def duplicate(): pass\ndef duplicate(): pass\nduplicate()\n',
            'match.py': 'def helper(): pass\ndef run(value):\n    match value:\n        case {"a": helper}:\n            helper()\n',
            'attributes.py': 'def outer():\n    def inner(): pass\nouter.inner()\nclass Example:\n    def method(self): pass\n    @staticmethod\n    def static(self):\n        self.method()\n',
            'scope.py': 'def target(): pass\ndef outer():\n    def target(): pass\n    def inner():\n        global target\n        target()\n    inner()\ndef other():\n    def target(): pass\n    def inner():\n        nonlocal target\n        target()\n',
            'generic.py': 'from pkg.b import Base\nclass Specialized(Base[int]): pass\n',
        }
        for name, content in files.items():
            path = cls.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        cls.outside = cls.base / 'outside.py'
        cls.outside.write_text('do_not_expose = True\n')
        (cls.repo / 'escape.py').symlink_to(cls.outside)
        cls.db = cls.base / 'graph.db'
        cls.status = index_repository(cls.repo, cls.db)
        cls.store = GraphStore(cls.db)
        cls.server = create_server(cls.db, port=0, dist=cls.base / 'dist')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temporary.cleanup()

    def edges(self, kind):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute('SELECT source,target,evidence FROM edges WHERE kind=?', (kind,)).fetchall()

    def test_relative_imports_and_src_import_root(self):
        imports = {(a, b) for a, b, _ in self.edges('imports')}
        self.assertIn(('file:pkg/a.py', 'file:pkg/b.py'), imports)
        self.assertIn(('file:consumer/main.py', 'file:src/domain/task.py'), imports)
        self.assertIn(('file:src/domain/task.py', 'file:src/domain/utils.py'), imports)
        calls = {(a, b) for a, b, _ in self.edges('calls')}
        self.assertIn(('file:consumer/other.py', 'function:pkg/a.py:work'), calls)
        self.assertIn(('file:consumer/main.py', 'function:pkg/b.py:helper'), calls)
        self.assertIn(('function:src/domain/task.py:task', 'function:src/domain/utils.py:value'), calls)

    def test_symbol_scope_and_shadowing(self):
        self.assertEqual(self.store.details('function:pkg/a.py:work.inside')['node']['parentId'], 'function:pkg/a.py:work')
        self.assertEqual(self.store.details('function:pkg/a.py:Worker.same')['node']['parentId'], 'class:pkg/a.py:Worker')
        calls = {(a, b) for a, b, _ in self.edges('calls')}
        self.assertIn(('function:pkg/a.py:Worker.run', 'function:pkg/a.py:Worker.same'), calls)
        self.assertIn(('function:pkg/a.py:work', 'function:pkg/a.py:work.inside'), calls)
        self.assertFalse(any(a in {'function:pkg/a.py:shadow', 'function:pkg/a.py:reassigned', 'file:duplicate.py', 'function:match.py:run', 'file:attributes.py', 'function:attributes.py:Example.static'} for a, _ in calls))
        self.assertEqual(len(self.store.search('duplicate')['results']), 3)
        self.assertIn(('function:scope.py:outer.inner', 'function:scope.py:target'), calls)
        self.assertNotIn(('function:scope.py:outer.inner', 'function:scope.py:outer.target'), calls)
        self.assertFalse(any(a == 'function:scope.py:other.inner' for a, _ in calls))

    def test_inheritance_has_evidence(self):
        inheritance = self.edges('inherits')
        self.assertEqual({(a, b) for a, b, _ in inheritance}, {('class:pkg/a.py:Worker', 'class:pkg/b.py:Base'), ('class:generic.py:Specialized', 'class:pkg/b.py:Base')})
        worker = next(row for row in inheritance if row[0] == 'class:pkg/a.py:Worker')
        self.assertEqual(json.loads(worker[2]), [{'path': 'pkg/a.py', 'line': 4}])

    def test_syntax_errors_and_coverage(self):
        self.assertEqual(self.status['parseWarnings'], 1)
        self.assertEqual(self.store.details('file:bad.py')['node']['kind'], 'file')
        self.assertTrue(any('bad.py' in warning for warning in self.status['warnings']))
        self.assertGreater(self.status['coverage']['excludedFiles'], 0)
        for name in ['file:ignored.py', 'file:secrets.json', 'file:escape.py']:
            with self.assertRaises(KeyError):
                self.store.details(name)

    def test_graph_aggregation_and_directory_connections(self):
        graph = self.store.graph('root')
        dependencies = {(e['source'], e['target'], e['kind']): e['weight'] for e in graph['edges']}
        self.assertGreaterEqual(dependencies[('dir:consumer', 'dir:pkg', 'calls')], 3)
        self.assertTrue(all(e['kind'] != 'contains' for e in graph['edges']))
        outgoing = self.store.details('dir:consumer')['relationships']['outgoing']
        self.assertTrue(any(item['node']['id'] == 'dir:pkg' for item in outgoing))
        self.assertTrue(all(item['evidence'] for item in outgoing))
        file_outgoing = self.store.details('file:pkg/a.py')['relationships']['outgoing']
        self.assertTrue(any(item['kind'] == 'calls' and item['node']['id'] == 'function:pkg/b.py:helper' for item in file_outgoing))
        file_incoming = self.store.details('file:pkg/b.py')['relationships']['incoming']
        self.assertTrue(any(item['kind'] == 'calls' and item['node']['id'] == 'function:pkg/a.py:work.inside' for item in file_incoming))
        limited = self.store.graph('root', 1)
        self.assertEqual(len(limited['nodes']), 1)
        self.assertTrue(limited['truncated'])
        self.assertEqual(limited['edges'], [])
        self.assertEqual([n['id'] for n in self.store.graph('dir:pkg')['breadcrumbs']], ['root', 'dir:pkg'])

    def test_metrics_and_source(self):
        root = self.store.details('root')['node']
        self.assertEqual(root['metrics']['files'], self.status['stats']['files'])
        self.assertEqual(root['metrics']['symbols'], self.status['stats']['symbols'])
        source = self.store.source('function:pkg/a.py:work')
        self.assertEqual(source['path'], 'pkg/a.py')
        self.assertEqual(source['highlightLine'], 11)
        self.assertIn('def work(value):', source['lines'])
        self.assertEqual(self.store.details('file:pkg/a.py')['node']['summarySource'], 'docstring')
        reference = self.store.source('file:pkg/a.py', 14)
        self.assertEqual(reference['highlightLine'], 14)
        self.assertEqual(reference['startLine'], 9)
        self.assertLessEqual(len(reference['lines']), 11)
        for line in (0, -1, 100000):
            with self.assertRaises(ValueError):
                self.store.source('file:pkg/a.py', line)

    def request(self, path, **headers):
        return urllib.request.urlopen(urllib.request.Request(self.url + path, headers=headers))

    def test_http_routes_and_security(self):
        with self.request('/api/status', Origin='http://localhost:5173') as response:
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'http://localhost:5173')
            self.assertEqual(json.load(response)['name'], 'example')
        for path in ['/api/source?id=../../outside.py', '/api/node?id=missing', '/api/unknown', '/../../outside.py']:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request(path)
            self.assertEqual(raised.exception.code, 404)
            raised.exception.close()
        for headers in [{'Origin': 'https://untrusted.example'}, {'Host': 'untrusted.example'}]:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request('/api/status', **headers)
            self.assertEqual(raised.exception.code, 403)
            raised.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/graph?limit=bad')
        self.assertEqual(raised.exception.code, 400)
        raised.exception.close()
        for line in ('', '0', '-1', 'nan', '1.2', '100000'):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request('/api/source?id=file:pkg/a.py&line=' + line)
            self.assertEqual(raised.exception.code, 400)
            raised.exception.close()
        self.assertIsNone(safe_source(self.repo, '../outside.py'))
        self.assertIsNone(safe_source(self.repo, 'escape.py'))

    def test_changed_file_cannot_escape_or_overflow_preview(self):
        path = self.repo / 'view.tsx'
        original = path.read_text()
        try:
            path.unlink()
            path.symlink_to(self.outside)
            with self.assertRaises(FileNotFoundError):
                self.store.source('file:view.tsx')
            path.unlink()
            path.write_text('x' * 100000)
            self.assertEqual(len(self.store.source('file:view.tsx')['lines'][0]), 4000)
        finally:
            if path.is_symlink():
                path.unlink()
            path.write_text(original)


if __name__ == '__main__':
    unittest.main()
