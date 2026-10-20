import json
from contextlib import closing
import subprocess
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from atlas_index.repository import RepositoryRegistry
from atlas_index.server import create_server, GraphStore
from atlas_index.indexer import source_files


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'checkout'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'app.py').write_text('def hello():\n    return 1\n')
        self.registry = None

    def tearDown(self):
        if self.registry:
            if not self.registry.stop.is_set():
                self.registry.queue.join()
                self.registry.close()
            for worker in self.registry.workers:
                worker.join(timeout=5)
        self.temp.cleanup()

    def test_storage_persistence_and_generated_files_excluded(self):
        self.registry = RepositoryRegistry(self.root)
        project = self.registry.register(str(self.root))
        self.registry.queue.join()
        for path in self.registry.paths(project['id']).values():
            self.assertEqual(path.parent, self.root / '.atlas')
        self.assertTrue((self.root / '.atlas/atlas.db').is_file())
        self.assertFalse(any('.atlas' in Path(path).parts for path, _ in source_files(self.root)))
        self.registry.close()
        self.registry = RepositoryRegistry(self.root)
        self.assertEqual(self.registry.project()['state'], 'ready')
        self.assertEqual(self.registry.list()['repositoryScope'], str(self.root))

    def test_http_registration_and_reads_cannot_switch_checkout(self):
        other = self.base / 'other'
        other.mkdir()
        subprocess.run(['git', 'init', '-q', str(other)], check=True)
        self.registry = RepositoryRegistry(self.root)
        self.registry.register(str(self.root))
        self.registry.queue.join()
        server = create_server(registry=self.registry, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}'
            request = urllib.request.Request(url + '/api/projects', data=json.dumps({'path': str(other)}).encode(),
                                             headers={'Content-Type': 'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url + '/api/status?project=other')
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
            with urllib.request.urlopen(url + '/api/projects') as response:
                self.assertEqual(len(json.load(response)['projects']), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_source_rejects_index_metadata_pointing_outside_checkout(self):
        self.registry = RepositoryRegistry(self.root)
        project = self.registry.register(str(self.root))
        self.registry.queue.join()
        database = self.registry.paths(project['id'])['db']
        with closing(sqlite3.connect(database)) as connection, connection:
            status = json.loads(connection.execute("SELECT value FROM metadata WHERE key='status'").fetchone()[0])
            status['root'] = str(self.base)
            connection.execute("UPDATE metadata SET value=? WHERE key='status'", (json.dumps(status),))
        with self.assertRaisesRegex(ValueError, 'different repository'):
            GraphStore(database, repository=self.root).source('file:app.py')

    def test_reject_symlink_and_tampered_registry(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (self.root / '.atlas').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            RepositoryRegistry(self.root)
        (self.root / '.atlas').unlink()
        self.registry = RepositoryRegistry(self.root)
        self.registry.register(str(self.root))
        self.registry.queue.join()
        self.registry.close()
        saved = self.root / '.atlas/projects.json'
        raw = json.loads(saved.read_text())
        raw['projects'][0]['_db'] = str(outside / 'atlas.db')
        saved.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            RepositoryRegistry(self.root)
