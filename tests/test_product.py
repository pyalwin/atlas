import json
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from atlas_index.indexer import index_repository
from atlas_index.product import ProductStore, Taxonomy, build_projection
from atlas_index.server import GraphStore, create_server


class ProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.repo = cls.base / 'repo'
        cls.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(cls.repo)], check=True)
        files = {
            'alpha/main.py': 'from beta.main import work\nfrom shared.util import helper\nfrom alpha.sub.logic import detail\ndef run():\n    work()\n    work()\n    helper()\n    detail()\n',
            'alpha/sub/logic.py': 'def detail():\n    return 1\n',
            'alpha/platform/tool.py': 'def platform():\n    return 1\n',
            'beta/main.py': 'from shared.util import helper\ndef work():\n    return helper()\n',
            'gamma/main.py': 'from shared.util import helper\ndef act():\n    return helper()\n',
            'shared/util.py': 'def helper():\n    return 1\n',
            'tests/noise.py': 'from gamma.main import act\nfrom alpha.main import run\nact()\nrun()\n',
            'deploy/noise.py': 'from alpha.main import run\nrun()\n',
            'alpha/test_noise.py': 'from gamma.main import act\nact()\n',
            'unmapped.py': 'from gamma.main import act\nact()\n',
        }
        for name, content in files.items():
            path = cls.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        cls.db = cls.base / 'atlas.db'
        index_repository(cls.repo, cls.db)
        cls.store = GraphStore(cls.db)
        cls.raw = {
            'version': 1,
            'features': [
                {'id': 'alpha', 'name': 'Alpha', 'summary': 'The first product.', 'paths': ['alpha'], 'evidence': ['alpha/main.py'],
                 'capabilities': [
                     {'id': 'core', 'name': 'Core', 'summary': 'Runs the product.', 'paths': ['alpha/main.py'], 'evidence': ['alpha/main.py']},
                     {'id': 'detail', 'name': 'Detail', 'summary': 'Adds detail.', 'paths': ['alpha/sub'], 'evidence': ['alpha/sub/logic.py']}]},
                {'id': 'beta', 'name': 'Beta', 'summary': 'The second product.', 'paths': ['beta']},
                {'id': 'gamma', 'name': 'Gamma', 'summary': 'The third product.', 'paths': ['gamma']}],
            'foundations': [{'id': 'shared', 'name': 'Shared', 'summary': 'Common helpers.', 'paths': ['shared', 'alpha/platform']}],
            'excludedPaths': [], 'notes': ['A curated view of static source references.']}
        cls.map = cls.base / 'map.json'
        cls.map.write_text(json.dumps(cls.raw))
        cls.products = ProductStore(cls.store, cls.map)
        cls.graph = cls.products.graph()
        cls.server = create_server(cls.db, port=0, taxonomy_path=cls.map)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temporary.cleanup()

    def test_longest_prefix_and_exclusions(self):
        taxonomy = Taxonomy(self.raw)
        self.assertEqual(taxonomy.classify('alpha/platform/tool.py'), ('mapped', 'foundation:shared', None))
        self.assertEqual(taxonomy.classify('alpha/sub/logic.py'), ('mapped', 'product:alpha', 'capability:alpha:detail'))
        self.assertEqual(taxonomy.classify('alphabet/file.py')[0], 'unmapped')
        self.assertEqual(taxonomy.classify('alpha/test_noise.py')[0], 'excluded')
        coverage = self.graph['coverage']
        self.assertEqual(coverage['mappedFiles'], 6)
        self.assertEqual(coverage['excludedFiles'], 3)
        self.assertEqual(coverage['unmappedFiles'], 1)
        self.assertEqual(sum(coverage[key] for key in ('mappedFiles', 'excludedFiles', 'unmappedFiles')), coverage['indexedFiles'])
        alpha = self.products.node('product:alpha')
        self.assertEqual(alpha['metrics']['files'], 2)
        self.assertEqual(self.products.node('foundation:shared')['metrics']['files'], 2)

    def test_only_real_direct_product_edges_and_no_double_count(self):
        self.assertEqual({(edge['source'], edge['target']) for edge in self.graph['edges']}, {('product:alpha', 'product:beta')})
        edge = self.graph['edges'][0]
        self.assertEqual(edge['weight'], 2)
        self.assertEqual(edge['referenceSites'], 3)
        self.assertEqual(edge['breakdown'], {'imports': 1, 'calls': 1, 'inherits': 0})
        self.assertEqual(edge['sampleCount'], len(edge['evidence']))
        self.assertEqual(len(self.graph['foundations']), 1)
        support = self.graph['supportingGroups'][0]
        self.assertEqual(support['features'], ['product:alpha', 'product:beta', 'product:gamma'])
        self.assertEqual(self.products.node('products')['metrics']['files'], 4)

    def test_source_and_target_proof(self):
        edge = self.graph['edges'][0]
        for proof in edge['evidence']:
            source = self.store.details(proof['sourceId'])['node']
            target = self.store.details(proof['targetId'])['node']
            self.assertEqual(proof['path'], source['path'])
            self.assertEqual(proof['targetPath'], target['path'])
            self.assertEqual(proof['targetLine'], target['line'])
            self.assertGreater(proof['line'], 0)
            self.assertIn('beta', proof['targetPath'])
        self.assertIn('static code references', edge['description'])

    def test_capability_graph_and_real_code_roots(self):
        self.assertEqual(self.products.node('product:alpha')['metrics']['capabilities'], 2)
        self.assertEqual(self.products.node('product:beta')['metrics']['capabilities'], 0)
        self.assertEqual(self.products.node('product:beta')['metrics']['codeAreas'], 1)
        graph = self.products.graph('product:alpha')
        self.assertEqual({n['id'] for n in graph['nodes']}, {'capability:alpha:core', 'capability:alpha:detail'})
        self.assertEqual([(edge['source'], edge['target'], edge['weight']) for edge in graph['edges']], [('capability:alpha:core', 'capability:alpha:detail', 2)])
        cap = self.products.details('capability:alpha:core')
        self.assertEqual([n['id'] for n in cap['codeRoots']], ['file:alpha/main.py'])
        self.assertEqual([n['id'] for n in cap['breadcrumbs']], ['products', 'product:alpha', 'capability:alpha:core'])
        self.assertEqual(self.products.graph('product:beta')['nodes'][0]['id'], 'dir:beta')

    def test_foundation_users_are_separate_from_product_connections(self):
        alpha = self.products.details('product:alpha')
        self.assertEqual([r['node']['id'] for r in alpha['relationships']['outgoing']], ['product:beta'])
        self.assertEqual([r['node']['id'] for r in alpha['foundations']], ['foundation:shared'])
        foundation = self.products.details('foundation:shared')
        self.assertEqual({r['node']['id'] for r in foundation['relationships']['incoming']}, {'product:alpha', 'product:beta', 'product:gamma'})
        self.assertEqual(len(foundation['codeRoots']), 2)

    def test_cache_reuse_and_taxonomy_digest_invalidation(self):
        cache = self.base / 'cache-test.json'
        mapping = self.base / 'cache-map.json'
        mapping.write_text(json.dumps(self.raw))
        with patch('atlas_index.product.build_projection', wraps=build_projection) as build:
            first = ProductStore(self.store, mapping, cache)
            first.ensure()
            ProductStore(self.store, mapping, cache).ensure()
            self.assertEqual(build.call_count, 1)
            raw = json.loads(mapping.read_text())
            raw['features'][0]['summary'] = 'An updated explanation.'
            mapping.write_text(json.dumps(raw))
            self.assertEqual(first.node('product:alpha')['summary'], 'An updated explanation.')
            self.assertEqual(build.call_count, 2)

    def test_product_http_contract(self):
        for route in ('/api/product/graph', '/api/product/graph?parent=product:alpha', '/api/product/node?id=foundation:shared', '/api/product/status'):
            with urllib.request.urlopen(self.url + route) as response:
                self.assertEqual(response.status, 200)
                self.assertIsInstance(json.load(response), dict)

    def test_default_map_and_cache_are_local_to_each_index(self):
        for name in ('first', 'second'):
            with self.subTest(name=name):
                directory = self.base / name
                directory.mkdir()
                db = directory / 'atlas.db'
                index_repository(self.repo, db)
                raw = json.loads(json.dumps(self.raw))
                raw['features'][0]['name'] = name
                (directory / 'product-map.json').write_text(json.dumps(raw))
                products = ProductStore(GraphStore(db))
                self.assertEqual(products.node('product:alpha')['name'], name)
                self.assertTrue((directory / 'product-projection.json').is_file())


if __name__ == '__main__':
    unittest.main()
