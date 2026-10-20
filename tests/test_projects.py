import copy
import json
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from atlas_index.indexer import index_repository
from atlas_index.projects import ProjectRegistry, project_id, validate_map
from atlas_index.server import create_server


def candidate_map(db, root):
    return {'version': 1, 'features': [{'id': 'service', 'name': 'Service',
            'summary': 'Suggested from the local service package.', 'paths': ['service'],
            'reason': 'A named application package.', 'evidence': [{'path': 'service/app.py', 'line': 1, 'kind': 'source'}]}],
            'foundations': [], 'excludedPaths': [], 'notes': ['Review this suggested grouping.']}


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.data = self.base / 'data'
        self.registry = ProjectRegistry(self.data, legacy_db=None, discoverer=candidate_map, poll_interval=3600)
        self.server = create_server(db_path=self.data / 'missing.db', registry=self.registry, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_agent_candidate_is_separate_and_rejects_stale_index(self):
        project = self.registry.register(str(self.repo('agentrepo')))
        self.registry.queue.join()
        identity = project['id']
        stamp = self.registry.project(identity)['indexedAt']
        original = self.registry.map(identity)['map']
        candidate = candidate_map(None, None)
        from atlas_index.documentation import SECTIONS
        guide = {key: 'This area implements the service behavior. Read the current source and verify its boundaries before depending on this explanation.' for key in SECTIONS}
        candidate['project'] = {'name': 'Service', 'summary': 'This project exposes a small service interface with local implementation and explicit behavior to inspect in the source.', 'documentation': guide}
        from feature_fixture import overview_fixture, reader_guide_fixture
        candidate['project']['readerGuide'] = reader_guide_fixture('service/app.py')
        candidate['features'][0]['readerGuide'] = reader_guide_fixture('service/app.py')
        candidate['project']['overview'] = overview_fixture('service/app.py', candidate['project']['summary'])
        candidate['features'][0].update(summary=candidate['project']['summary'], documentation=guide, entryPoints=[{'path': 'service/app.py', 'line': 1, 'reason': 'Start here to inspect the service implementation.'}])
        route = f'/api/projects/{identity}/candidate'
        _, result, _ = self.request(route, 'PUT', {'map': candidate, 'indexedAt': stamp, 'agent': 'Codex'})
        self.assertEqual(result['map'], original)
        self.assertEqual(result['suggestion']['summarySource'], 'agent-generated')
        self.assertFalse(result['suggestion']['generation']['reviewed'])
        self.assertTrue(result['project']['hasSuggestion'])
        _, preview, _ = self.request('/api/product/graph?project=' + identity + '&map=candidate')
        self.assertEqual(preview['parent']['name'], 'Service')
        self.assertEqual(preview['parent']['overview'], candidate['project']['overview'])
        self.assertEqual(self.registry.map(identity)['map'], original)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(route, 'PUT', {'map': candidate, 'indexedAt': 'old', 'agent': 'Codex'})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.registry.map(identity)['suggestion'], result['suggestion'])

    def repo(self, name):
        path = self.base / name
        path.mkdir()
        subprocess.run(['git', 'init', '-q', str(path)], check=True)
        (path / 'service').mkdir()
        (path / 'service/app.py').write_text(f'def {name}():\n    return "{name}"\n')
        (path / '.gitignore').write_text('ignored.py\n')
        return path

    def request(self, route, method='GET', payload=None, headers=None):
        headers = dict(headers or {})
        data = None
        if method != 'GET' and method != 'OPTIONS':
            data = json.dumps(payload if payload is not None else {}).encode()
            headers.setdefault('Content-Type', 'application/json')
        request = urllib.request.Request(self.url + route, data=data, method=method, headers=headers)
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None, dict(response.headers)

    def wait(self, identity, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            project = self.registry.project(identity)
            if project.get('job') and project['job']['state'] in {'complete', 'error'}:
                return project
            time.sleep(.01)
        self.fail('The local project job did not finish.')

    def add(self, name):
        root = self.repo(name)
        status, body, _ = self.request('/api/projects', 'POST', {'path': str(root), 'name': name.title()})
        self.assertEqual(status, 202)
        identity = body['project']['id']
        self.assertEqual(self.wait(identity)['state'], 'ready')
        return root, identity

    def test_clean_start_and_two_project_isolation(self):
        self.assertEqual(self.request('/api/projects')[1], {'projects': [], 'activeProjectId': None})
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/status')
        self.assertEqual(raised.exception.code, 409)
        raised.exception.close()
        alpha, a = self.add('alpha')
        beta, b = self.add('beta')
        self.assertNotEqual(a, b)
        self.assertNotEqual(self.registry.paths(a)['db'], self.registry.paths(b)['db'])
        for identity, name in [(a, 'alpha'), (b, 'beta')]:
            status = self.request('/api/status?project=' + identity)[1]
            self.assertEqual(status['root'], str(self.base / name))
            self.assertEqual(status['project']['id'], identity)
            source = self.request('/api/source?project=' + identity + '&id=file:service/app.py')[1]
            self.assertIn(f'def {name}():', source['lines'])
            graph = self.request('/api/product/graph?project=' + identity)[1]
            self.assertEqual(graph['nodes'][0]['id'], 'product:service')
        self.request(f'/api/projects/{b}/activate', 'POST')
        self.assertEqual(self.request('/api/status')[1]['root'], str(beta))
        existing = self.registry.register(str(alpha / 'service'))
        self.assertEqual(existing['id'], a)
        self.assertEqual(existing['job']['state'], 'complete')

    def test_async_error_and_serialized_index_jobs(self):
        lock = threading.Lock()
        active = [0, 0]
        def indexer(root, db, progress=None):
            with lock:
                active[0] += 1
                active[1] = max(active)
            try:
                time.sleep(.03)
                if root.name == 'broken':
                    raise RuntimeError('Deliberate fixture failure')
                return index_repository(root, db)
            finally:
                with lock:
                    active[0] -= 1
        self.registry.indexer = indexer
        identities = [self.registry.register(str(self.repo(name)))['id'] for name in ('alpha', 'beta', 'broken')]
        results = [self.wait(identity) for identity in identities]
        self.assertEqual([p['state'] for p in results], ['ready', 'ready', 'error'])
        self.assertEqual(active[1], 1)
        self.assertIn('Deliberate fixture failure', results[-1]['job']['error'])
        self.assertFalse(self.registry.paths(identities[-1])['db'].exists())

    def test_reviewed_map_survives_reindex_and_new_suggestions(self):
        root, identity = self.add('alpha')
        first = self.request(f'/api/projects/{identity}/map')[1]
        self.assertEqual(first['map']['features'][0]['summarySource'], 'suggested')
        edited = first['map']
        edited['features'][0]['name'] = 'My reviewed feature'
        edited['features'][0]['summary'] = 'A deliberately reviewed explanation.'
        self.request(f'/api/projects/{identity}/map', 'PUT', {'map': edited})
        saved = self.registry.map(identity)['map']
        self.assertEqual(saved['features'][0]['summarySource'], 'reviewed')
        self.assertEqual(saved['features'][0]['reason'], 'A named application package.')
        self.registry.enqueue(identity, 'index')
        self.wait(identity)
        self.assertEqual(self.registry.map(identity)['map'], saved)
        self.request(f'/api/projects/{identity}/suggestions', 'POST')
        self.wait(identity)
        maps = self.registry.map(identity)
        self.assertEqual(maps['map'], saved)
        self.assertEqual(maps['suggestion']['features'][0]['name'], 'Service')
        self.assertTrue(maps['project']['hasSuggestion'])
        self.assertEqual(maps['project']['mapStatus'], 'reviewed')

    def test_freshness_added_modified_deleted_and_ignored(self):
        root, identity = self.add('alpha')
        self.assertEqual(self.registry.changes(identity)['changes']['state'], 'current')
        (root / 'service/app.py').write_text('def changed():\n    return 99\n')
        (root / 'service/new.py').write_text('new = 1\n')
        (root / 'ignored.py').write_text('ignored = 1\n')
        changes = self.request(f'/api/projects/{identity}/changes')[1]['changes']
        self.assertEqual((changes['count'], changes['added'], changes['modified']), (2, 1, 1))
        self.registry.enqueue(identity, 'index')
        self.wait(identity)
        (root / 'service/new.py').unlink()
        changes = self.registry.changes(identity)['changes']
        self.assertEqual((changes['count'], changes['deleted']), (1, 1))
        self.assertFalse(self.registry.project(identity)['autoUpdate'])

    def test_opt_in_auto_update_debounces_changes(self):
        root, identity = self.add('alpha')
        (root / 'service/app.py').write_text('changed = 1\n')
        self.registry.update(identity, {'autoUpdate': True})
        self.registry.changes(identity)
        self.assertNotEqual(self.registry.project(identity)['state'], 'indexing')
        with self.registry.lock:
            self.registry.records[identity]['_lastSourceChange'] = time.time() - 10
        self.registry.enqueue(identity, 'check')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            project = self.registry.project(identity)
            if project['changes']['state'] == 'current' and project['job']['state'] == 'complete':
                break
            time.sleep(.01)
        self.assertEqual(self.registry.project(identity)['changes']['state'], 'current')

    def test_legacy_adoption_reuses_db_and_registry_persists(self):
        root = self.repo('legacy')
        db = self.base / 'legacy.db'
        index_repository(root, db)
        original = (db.stat().st_ino, db.stat().st_size, db.stat().st_mtime_ns)
        mapping = self.base / 'legacy-map.json'
        mapping.write_text(json.dumps(candidate_map(db, root)))
        data = self.base / 'adopted'
        adopted = ProjectRegistry(data, legacy_db=db, legacy_map=mapping, start_workers=False)
        identity = adopted.active
        self.assertEqual(adopted.paths(identity)['db'], db)
        self.assertEqual(adopted.paths(identity)['map'], mapping)
        self.assertEqual(adopted.project(identity)['state'], 'ready')
        self.assertEqual(adopted.project(identity)['mapStatus'], 'reviewed')
        self.assertEqual(adopted.changes(identity)['changes']['state'], 'unknown')
        adopted.update(identity, {'name': 'Remember this name'})
        adopted.close()
        reloaded = ProjectRegistry(data, legacy_db=None, start_workers=False)
        self.assertEqual(reloaded.project(identity)['name'], 'Remember this name')
        self.assertEqual(reloaded.active, identity)
        self.assertEqual(original, (db.stat().st_ino, db.stat().st_size, db.stat().st_mtime_ns))
        reloaded.close()

    def test_map_validation_rejects_escape_and_overlapping_capabilities(self):
        raw = candidate_map(None, None)
        raw['features'][0]['paths'] = ['../outside']
        with self.assertRaises(ValueError):
            validate_map(raw)
        raw = candidate_map(None, None)
        raw['features'][0]['capabilities'] = [
            {'id': 'one', 'name': 'One', 'summary': 'One explanation.', 'paths': ['service/a']},
            {'id': 'two', 'name': 'Two', 'summary': 'Two explanation.', 'paths': ['service/a/nested']}]
        with self.assertRaisesRegex(ValueError, 'overlap'):
            validate_map(raw)
        raw['features'][0]['capabilities'][1]['paths'] = ['elsewhere']
        with self.assertRaisesRegex(ValueError, 'within'):
            validate_map(raw)

    def test_adoption_discovers_custom_map_and_detaches_replaced_database(self):
        alpha, beta = self.repo('alpha'), self.repo('beta')
        db = self.base / 'shared-index.db'
        index_repository(alpha, db)
        data = self.base / 'legacy-registry'
        discovery_calls = []
        def discover(db_path, root):
            discovery_calls.append(root.name)
            raw = candidate_map(db_path, root)
            raw['features'][0]['name'] = root.name
            return raw
        def forbidden_index(*args, **kwargs):
            self.fail('Adoption must not reindex the source repository.')
        first = ProjectRegistry(data, legacy_db=db, indexer=forbidden_index, discoverer=discover, monitor=False)
        first.queue.join()
        a = first.active
        self.assertEqual(first.map(a)['map']['features'][0]['name'], 'alpha')
        first.close()
        index_repository(beta, db)
        second = ProjectRegistry(data, legacy_db=db, indexer=forbidden_index, discoverer=discover, monitor=False)
        second.queue.join()
        b = project_id(beta)
        self.assertEqual(second.project(a)['state'], 'error')
        self.assertIsNone(second.project(a)['indexedAt'])
        self.assertNotEqual(second.paths(a)['db'], db)
        self.assertEqual(second.paths(b)['db'], db)
        self.assertEqual(second.map(b)['map']['features'][0]['name'], 'beta')
        self.assertEqual(second.map(a)['map']['features'][0]['name'], 'alpha')
        self.assertEqual(discovery_calls, ['alpha', 'beta'])
        second.close()

    def test_saved_project_recovers_when_its_map_is_missing(self):
        root, identity = self.add('alpha')
        index_path = self.registry.paths(identity)['db']
        original = (index_path.stat().st_size, index_path.stat().st_mtime_ns)
        missing_map = self.base / 'removed-map.json'
        with self.registry.lock:
            self.registry.records[identity]['_map'] = str(missing_map)
            self.registry._save()
        self.server.shutdown()
        self.server.server_close()
        recovered = ProjectRegistry(self.data, legacy_db=None, discoverer=candidate_map, monitor=False)
        try:
            recovered.queue.join()
            self.assertEqual(recovered.project(identity)['state'], 'ready')
            self.assertEqual(recovered.map(identity)['map']['features'][0]['name'], 'Service')
            self.assertEqual(recovered.paths(identity)['map'],
                             self.data / 'projects' / identity / 'product-map.json')
            self.assertFalse(missing_map.exists())
            self.assertEqual(original, (index_path.stat().st_size, index_path.stat().st_mtime_ns))
        finally:
            recovered.close()

    def test_explicit_suggestions_requested_during_index_are_not_dropped(self):
        root = self.repo('alpha')
        gate, entered = threading.Event(), threading.Event()
        def delayed_index(path, db, progress=None):
            entered.set()
            gate.wait(3)
            return index_repository(path, db, progress=progress)
        self.registry.indexer = delayed_index
        identity = self.registry.register(str(root))['id']
        self.assertTrue(entered.wait(2))
        self.registry.enqueue(identity, 'suggestions')
        gate.set()
        self.registry.queue.join()
        self.assertTrue(self.registry.project(identity)['hasSuggestion'])
        self.assertEqual(self.registry.project(identity)['job']['phase'], 'suggestions')

    def test_all_method_guards_content_type_and_bounded_json(self):
        for method in ('GET', 'POST', 'PATCH', 'PUT', 'OPTIONS', 'DELETE'):
            for header in ({'Origin': 'https://untrusted.example'}, {'Host': 'untrusted.example'}):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    self.request('/api/projects', method, {}, header)
                self.assertEqual(raised.exception.code, 403)
                raised.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/projects', 'POST', {}, {'Content-Type': 'text/plain'})
        self.assertEqual(raised.exception.code, 415)
        raised.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/projects', 'POST', {}, {'Content-Length': '1100001'})
        self.assertEqual(raised.exception.code, 413)
        raised.exception.close()
        status, _, headers = self.request('/api/projects', 'OPTIONS', headers={'Origin': 'http://localhost:4317', 'Access-Control-Request-Method': 'PUT'})
        self.assertEqual(status, 204)
        self.assertIn('PUT', headers['Access-Control-Allow-Methods'])
        self.assertEqual(headers['Access-Control-Allow-Origin'], 'http://localhost:4317')


if __name__ == '__main__':
    unittest.main()
