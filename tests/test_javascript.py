import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from atlas_index.indexer import index_repository
from atlas_index.javascript import resolve_import, scan_batch
from atlas_index.server import GraphStore


class JavaScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name) / 'shop'
        cls.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(cls.root)], check=True)
        files = {
            'src/catalog/index.ts': '/** Products available to buy. */\nexport function products(): string[] { return []; }\n',
            'src/checkout/cart.tsx': '''import { products } from '../catalog';
import type { Session } from '../shared/session.js';
import ignored from '@alias/not-resolved';
// import fake from '../fake';
/** Add products to the basket. */
export const Cart = () => <div>{products().length}</div>;
export class Basket {
    count(): number { return products().length; }
}
export function outer() {
    function inner() { return 1; }
    return inner();
}
export default () => null;
''',
            'src/shared/session.ts': 'export type Session = { id: string };\n',
            'src/checkout/exports.mjs': "export * from '../catalog/index.ts';\nconst load = () => import('../shared/session.js');\n",
            'src/checkout/broken.ts': 'export function broken( {\n',
            'src/backend.py': 'def alive(): return True\n',
            'README.md': '# Shop\n',
        }
        for name, contents in files.items():
            path = cls.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        cls.db = Path(cls.temp.name) / 'atlas.db'
        cls.progress = []
        cls.status = index_repository(cls.root, cls.db, progress=cls.progress.append)
        cls.store = GraphStore(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_named_definitions_and_documentation(self):
        cart = self.store.details('function:src/checkout/cart.tsx:Cart')['node']
        self.assertEqual(cart['summary'], 'Add products to the basket.')
        self.assertEqual(cart['summarySource'], 'documentation')
        self.assertEqual(cart['line'], 6)
        method = self.store.details('function:src/checkout/cart.tsx:Basket.count')['node']
        self.assertEqual(method['parentId'], 'class:src/checkout/cart.tsx:Basket')
        inner = self.store.details('function:src/checkout/cart.tsx:outer.inner')['node']
        self.assertEqual(inner['parentId'], 'function:src/checkout/cart.tsx:outer')
        self.assertEqual(self.store.details('function:src/checkout/cart.tsx:default')['node']['kind'], 'function')

    def test_imports_have_evidence_without_invented_call_edges(self):
        with closing(sqlite3.connect(self.db)) as db:
            rows = db.execute("SELECT source,target,kind,evidence FROM edges").fetchall()
        imports = {(source, target): json.loads(evidence) for source, target, kind, evidence in rows if kind == 'imports'}
        self.assertEqual(imports[('file:src/checkout/cart.tsx', 'file:src/catalog/index.ts')], [{'path': 'src/checkout/cart.tsx', 'line': 1}])
        self.assertIn(('file:src/checkout/cart.tsx', 'file:src/shared/session.ts'), imports)
        self.assertIn(('file:src/checkout/exports.mjs', 'file:src/shared/session.ts'), imports)
        self.assertFalse(any(kind in {'calls', 'inherits'} and source.endswith(':Cart') for source, _, kind, _ in rows))
        self.assertFalse(any('fake' in target for _, target, _, _ in rows))
        self.assertEqual(self.status['coverage']['unresolvedOrExternalImports'], 1)

    def test_coverage_and_progress_are_explicit(self):
        analysis = {item['name']: item for item in self.status['languageAnalysis']}
        self.assertEqual(analysis['TypeScript']['mode'], 'imports-and-symbols')
        self.assertEqual(analysis['TypeScript']['parsedFiles'], 3)
        self.assertEqual(analysis['Python']['mode'], 'semantic')
        self.assertEqual(analysis['Markdown']['mode'], 'file-only')
        self.assertEqual(self.status['parseWarnings'], 1)
        self.assertEqual(self.store.details('file:src/checkout/broken.ts')['node']['kind'], 'file')
        self.assertTrue(any('broken.ts' in warning for warning in self.status['warnings']))
        self.assertEqual(self.progress[0]['phase'], 'scanning')
        self.assertEqual(self.progress[-1]['phase'], 'saving')

    def test_resolver_stays_inside_local_index(self):
        paths = {'src/foo.ts', 'src/bar/index.tsx', 'outside.py'}
        self.assertEqual(resolve_import('src/main.ts', './foo.js', paths), 'src/foo.ts')
        self.assertEqual(resolve_import('src/main.ts', './bar', paths), 'src/bar/index.tsx')
        self.assertIsNone(resolve_import('src/main.ts', '@alias/foo', paths))
        self.assertIsNone(resolve_import('src/main.ts', '../../outside.py', paths))
        self.assertIsNone(resolve_import('src/main.ts', '/outside.py', paths))
        variants = {'src/config.ts', 'src/config.mts', 'src/config.cts'}
        self.assertEqual(resolve_import('src/main.mts', './config.mjs', variants), 'src/config.mts')
        self.assertEqual(resolve_import('src/main.cts', './config.cjs', variants), 'src/config.cts')
        self.assertIsNone(resolve_import('src/main.ts', './config.js', {'src/config.mts'}))

    def test_named_property_functions(self):
        records, warnings = scan_batch([{'path': 'properties.ts', 'source': '''
export class Service { run = () => true; #secret = () => false; method() {} }
const handlers = { save: () => true, "finish": function() {}, clear() {} };
'''}])
        self.assertFalse(warnings)
        symbols = {symbol['id']: symbol for symbol in records[0]['symbols']}
        self.assertEqual(symbols['function:properties.ts:Service.run']['parentId'], 'class:properties.ts:Service')
        self.assertIn('function:properties.ts:Service.#secret', symbols)
        self.assertIn('function:properties.ts:save', symbols)
        self.assertIn('function:properties.ts:finish', symbols)

    def test_unavailable_parser_is_disclosed(self):
        with patch('atlas_index.javascript.shutil.which', return_value=None), patch.dict('os.environ', {}, clear=True):
            records, warnings = scan_batch([{'path': 'x.ts', 'source': 'const x = 1;'}])
        self.assertEqual(records, [])
        self.assertIn('without symbols', warnings[0])


if __name__ == '__main__':
    unittest.main()
