import json
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from atlas_index.indexer import index_repository
from atlas_index.review import ReviewStore, parse_hunks, parse_pr
from atlas_index.server import create_server


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name).resolve()
        self.repo = self.directory / 'repo'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.write('service.py', 'def retained():\n    return 1\n\ndef removed():\n    return 2\n')
        self.write('consumer.py', 'from service import retained, removed\nretained()\nremoved()\n')
        self.write('dependency.py', 'def helper():\n    return 3\n')
        self.write('renamed.py', 'def keep():\n    return "kept"\n')
        self.write('decorated.py', '@old\ndef target():\n    return 1\n')
        self.write('secrets.json', '{"token": "old private text"}')
        self.base = self.commit('initial')
        self.git('checkout', '-q', '-b', 'feature')
        self.write('service.py', 'from dependency import helper\ndef retained():\n    return helper()\n')
        self.write('added.py', 'def added():\n    return 5\n')
        self.write('decorated.py', '@new\ndef target():\n    return 1\n')
        self.write('secrets.json', '{"token": "new private text"}')
        self.write('binary.py', b'\x00\x01no source')
        self.write('image.png', b'image bytes')
        self.git('mv', 'renamed.py', 'moved.py')
        self.head = self.commit('feature')
        self.git('checkout', '-q', 'main')
        self.write('unrelated.py', 'def unrelated(): pass\n')
        self.main_tip = self.commit('unrelated base change')
        self.write('service.py', 'def dirty():\n    return "not a commit"\n')
        self.write('untracked.py', 'def untracked(): pass\n')
        self.store = ReviewStore(self.repo, self.directory / 'reviews')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, check=True)
        return result.stdout.decode().strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())

    def commit(self, message):
        self.git('add', '-A')
        self.git('-c', 'user.name=Atlas Tests', '-c', 'user.email=atlas@example.test', 'commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def review(self, **body):
        self.store.start(body or {'base': 'main', 'head': 'feature'})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = self.store.get()
            if result['job']['state'] in {'complete', 'error'}:
                self.assertEqual(result['job']['state'], 'complete', result['job'])
                return result['review']
            time.sleep(.01)
        self.fail('Review did not finish')

    def test_pinned_merge_base_symbols_graph_and_working_tree(self):
        before = self.git('status', '--porcelain=v1')
        review = self.review()
        self.assertEqual(review['baseSha'], self.base)
        self.assertEqual(review['baseTip'], self.main_tip)
        self.assertEqual(review['headSha'], self.head)
        files = {f['path']: f for f in review['files']}
        self.assertNotIn('unrelated.py', files)
        self.assertNotIn('untracked.py', files)
        self.assertEqual(files['moved.py']['status'], 'renamed')
        self.assertEqual(files['moved.py']['oldPath'], 'renamed.py')
        self.assertEqual(files['moved.py']['changedSymbols'], [])
        symbols = {n['name']: n for n in files['service.py']['changedSymbols']}
        self.assertEqual(symbols['removed']['change'], 'deleted')
        self.assertEqual(symbols['removed']['side'], 'base')
        self.assertEqual(symbols['retained']['change'], 'modified')
        self.assertEqual(files['decorated.py']['changedSymbols'][0]['name'], 'target')
        self.assertEqual(files['decorated.py']['changedSymbols'][0]['change'], 'modified')
        self.assertEqual(files['added.py']['changedSymbols'][0]['change'], 'added')
        edges = review['graph']['edges']
        removed = next(e for e in edges if e['source'] == 'file:consumer.py' and e['target'] == 'file:service.py' and e['kind'] == 'calls' and e['change'] == 'removed')
        self.assertEqual(removed['evidence'], [{'path': 'consumer.py', 'line': 3, 'side': 'base'}])
        self.assertTrue(any(e['target'] == 'file:dependency.py' and e['change'] == 'added' for e in edges))
        nodes = {n['path']: n for n in review['graph']['nodes']}
        self.assertEqual(nodes['consumer.py']['role'], 'dependent')
        self.assertEqual(nodes['dependency.py']['role'], 'dependency')
        self.assertEqual(self.git('status', '--porcelain=v1'), before)
        source = self.store.source('service.py', 'head', 3, review['id'])
        self.assertIn('    return helper()', source['lines'])
        self.assertFalse(any('dirty' in line for line in source['lines']))
        self.assertIn('def removed():', self.store.source('service.py', 'base', 4, review['id'])['lines'])
        diff = self.store.diff('service.py', review['id'])
        self.assertIn('-def removed():', diff['diff'])
        self.assertFalse(diff['truncated'])

    def test_excluded_and_binary_previews(self):
        review = self.review()
        files = {f['path']: f for f in review['files']}
        self.assertTrue(files['secrets.json']['excluded'])
        self.assertTrue(files['binary.py']['binary'])
        self.assertTrue(files['image.png']['excluded'])
        for name in ['secrets.json', 'binary.py', 'image.png']:
            diff = self.store.diff(name)
            self.assertEqual(diff['diff'], '')
            self.assertEqual(diff['hunks'], [])
        self.assertNotIn('new private text', json.dumps(review))
        with self.assertRaises(ValueError):
            self.store.source('secrets.json', 'head')
        with self.assertRaises(FileNotFoundError):
            self.store.source('binary.py', 'head')

    def test_persistent_review_identity_and_input_validation(self):
        first = self.review()
        second = self.review(base='feature', head='feature')
        self.assertEqual(second['stats']['changedFiles'], 0)
        self.assertIn('def removed():', self.store.diff('service.py', first['id'])['diff'])
        self.assertEqual(ReviewStore(self.repo, self.directory / 'reviews').get()['review']['id'], second['id'])
        for body in [{'base': '--output=x', 'head': 'feature'}, {'base': 'main\n', 'head': 'feature'}, {'head': 'feature'}, {'pr': 'https://github.com/other/repo/pull/5'}]:
            with self.assertRaises(ValueError):
                self.store.start(body)
        for path in ['../service.py', '/service.py', 'a/../service.py']:
            with self.assertRaises(ValueError):
                self.store.source(path, 'head')
        with self.assertRaises(ValueError):
            self.store.diff('service.py', '../scope')
        with self.assertRaises(KeyError):
            self.store.diff('service.py', '0' * 16)
        self.assertEqual(parse_pr('https://github.com/django/django/pull/22174', 'django/django'), 22174)
        with self.assertRaises(ValueError):
            parse_pr('https://github.com/other/repo/pull/22174', 'django/django')

    def test_bounded_graph_and_large_file(self):
        self.git('checkout', '-q', '-f', 'feature')
        self.write('large.py', 'x' * 2_000_001)
        self.commit('large')
        with patch('atlas_index.review.MAX_NODES', 2), patch('atlas_index.review.MAX_EDGES', 1):
            review = self.review()
        self.assertTrue(review['truncated']['nodes'])
        self.assertTrue(review['truncated']['edges'])
        self.assertEqual(len(review['graph']['nodes']), 2)
        file = next(f for f in review['files'] if f['path'] == 'large.py')
        self.assertIn('2 MB', file['excluded'])
        self.assertEqual(self.store.diff('large.py')['diff'], '')

    def test_hunk_zero_ranges(self):
        hunks = parse_hunks('@@ -1,0 +2,1 @@\n+new\n@@ -5,1 +6,0 @@\n-old\n')
        self.assertEqual(hunks[0]['oldLines'], 0)
        self.assertEqual(hunks[0]['lines'][0]['oldLine'], None)
        self.assertEqual(hunks[1]['newLines'], 0)
        self.assertEqual(hunks[1]['lines'][0]['newLine'], None)

    def test_rename_preserves_edges_and_cannot_include_replacement_directory(self):
        self.git('checkout', '-q', '-f', 'feature')
        self.write('source.py', 'from dependency import helper\ndef run():\n    return helper()\n')
        before = self.commit('rename base')
        self.git('mv', 'source.py', 'destination.py')
        self.write('source.py/unrelated.py', 'def nested():\n    return 42\n')
        self.commit('rename and reuse old path as directory')
        review = self.review(base=before, head='HEAD')
        renamed = next(f for f in review['files'] if f['path'] == 'destination.py')
        self.assertEqual(renamed['status'], 'renamed')
        self.assertEqual(renamed['changedSymbols'], [])
        self.assertEqual(self.store.diff('destination.py')['diff'], '')
        edges = [e for e in review['graph']['edges'] if e['source'] == 'file:destination.py']
        self.assertTrue(edges)
        self.assertTrue(all(e['change'] == 'unchanged' for e in edges), edges)

    def test_deleted_file_keeps_base_symbols_and_evidence(self):
        self.git('checkout', '-q', '-f', 'feature')
        before = self.head
        self.git('rm', '-q', 'dependency.py')
        self.commit('delete dependency')
        review = self.review(base=before, head='HEAD')
        file = next(f for f in review['files'] if f['path'] == 'dependency.py')
        self.assertEqual(file['status'], 'deleted')
        self.assertEqual(file['changedSymbols'][0]['change'], 'deleted')
        incoming = [e for e in review['graph']['edges'] if e['target'] == 'file:dependency.py']
        self.assertTrue(incoming)
        self.assertTrue(all(e['change'] == 'removed' for e in incoming))
        self.assertTrue(all(e['evidence'][0]['side'] == 'base' for e in incoming))
        self.assertIn('-def helper():', self.store.diff('dependency.py')['diff'])

    def test_shallow_repository_pr_head_only_in_custom_ref(self):
        shallow = self.directory / 'shallow'
        subprocess.run(['git', 'clone', '--quiet', '--depth=1', '--single-branch', '--branch', 'main',
                        self.repo.as_uri(), str(shallow)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(shallow), 'fetch', '--quiet', '--depth=20', 'origin',
                        'main:refs/atlas/review/base', 'feature:refs/atlas/review/pr-head'],
                       check=True, capture_output=True)
        self.store.close()
        self.store = ReviewStore(shallow, self.directory / 'shallow-reviews')
        review = self.review(base='refs/atlas/review/base', head='refs/atlas/review/pr-head')
        self.assertEqual(review['headSha'], self.head)
        self.assertEqual(review['baseSha'], self.base)
        self.assertIn('    return helper()', self.store.source('service.py', 'head', 3)['lines'])
        self.assertIn('def removed():', self.store.source('service.py', 'base', 4)['lines'])

    def test_http_contract_and_scope_source(self):
        db = self.directory / 'project.db'
        index_repository(self.repo, db)
        server = create_server(db, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            def request(route, body=None):
                req = urllib.request.Request(url + route, data=json.dumps(body).encode() if body else None,
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req) as response:
                    return response.status, json.load(response)
            options = request('/api/review/options')[1]
            self.assertTrue(options['available'])
            self.assertIn('feature', options['branches'])
            status, result = request('/api/review', {'base': 'main', 'head': 'feature'})
            self.assertEqual(status, 202)
            deadline = time.monotonic() + 15
            while result['job']['state'] not in {'complete', 'error'} and time.monotonic() < deadline:
                time.sleep(.02)
                result = request('/api/review')[1]
            self.assertEqual(result['job']['state'], 'complete', result)
            review = result['review']
            source = request('/api/review/source?path=service.py&side=base&line=4&review=' + review['id'])[1]
            self.assertEqual(source['sha'], self.base)
            self.assertIn('def removed():', source['lines'])
            diff = request('/api/review/diff?path=service.py&review=' + review['id'])[1]
            self.assertTrue(diff['hunks'])
            with self.assertRaises(urllib.error.HTTPError) as raised:
                request('/api/review', {'base': '-bad', 'head': 'HEAD'})
            self.assertEqual(raised.exception.code, 400)
            raised.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
