"""Repository-local storage and a reusable, single-checkout UI/API service."""
import json
import os
import signal
import uuid
from pathlib import Path

from .projects import ProjectRegistry, atomic_json, canonical_repository, project_id, read_index_status
from .server import PROJECT_ROOT, create_server

SERVICE_VERSION = 1


def storage_directory(repository):
    root = canonical_repository(str(repository))
    directory = root / '.atlas'
    if directory.is_symlink():
        raise ValueError('The repository .atlas directory must not be a symlink.')
    directory.mkdir(exist_ok=True)
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise ValueError('Atlas storage must not contain symlinks.')
    return root, directory


class RepositoryRegistry(ProjectRegistry):
    def __init__(self, repository, **kwargs):
        self.repository, directory = storage_directory(repository)
        self.identity = project_id(self.repository)
        # Registry files are repository content, not trusted absolute paths.
        saved_path = directory / 'projects.json'
        if saved_path.exists():
            saved = json.loads(saved_path.read_text())
            for record in saved.get('projects', []):
                if record.get('id') != self.identity or record.get('path') != str(self.repository):
                    raise ValueError('Atlas registry belongs to a different repository.')
                for key, path in self._storage_paths(directory).items():
                    if record.get(key) != str(path):
                        raise ValueError('Atlas registry storage paths must stay in this checkout.')
        database = directory / 'atlas.db'
        if database.is_file() and Path(read_index_status(database)['root']).resolve() != self.repository:
            raise ValueError('Atlas index belongs to a different repository.')
        super().__init__(data_dir=directory, legacy_db=None, monitor=False, **kwargs)

    @staticmethod
    def _storage_paths(directory):
        return {'_db': directory / 'atlas.db', '_map': directory / 'product-map.json',
                '_cache': directory / 'product-projection.json', '_suggestion': directory / 'suggested-map.json',
                '_fingerprint': directory / 'source-fingerprint.json'}

    def _new(self, root, name):
        entry = super()._new(root, name)
        entry.update({key: str(path) for key, path in self._storage_paths(self.data_dir).items()})
        return entry

    def register(self, path, name=None):
        if canonical_repository(path) != self.repository:
            raise ValueError('This Atlas service is bound to a different repository.')
        return super().register(str(self.repository), name)

    def paths(self, identity=None):
        storage_directory(self.repository)
        paths = super().paths(identity)
        if any(not path.resolve().is_relative_to(self.data_dir) for path in paths.values()):
            raise ValueError('Atlas storage must stay within the repository .atlas directory.')
        return paths

    def _save(self):
        storage_directory(self.repository)
        return super()._save()

    def _get(self, identity=None):
        if identity is not None and identity != self.identity:
            raise KeyError('Project is outside this service’s repository scope.')
        return super()._get(identity)

    def list(self):
        return {**super().list(), 'repositoryScope': str(self.repository)}


def serve_repository(repository):
    # flock prevents concurrent harness sessions from creating competing writers.
    import fcntl
    root, directory = storage_directory(repository)
    if not (PROJECT_ROOT / 'dist/index.html').is_file():
        raise ValueError('Build the Atlas UI first: npm run build')
    with (directory / 'service.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # A competing startup won; its descriptor will become ready.
        registry = RepositoryRegistry(root)
        try:
            project = registry.register(str(root))
            server = create_server(db_path=directory / 'atlas.db', registry=registry, port=0)
        except BaseException:
            registry.close()
            raise
        descriptor = {'service': 'atlas-repository', 'version': SERVICE_VERSION,
                      'instance': uuid.uuid4().hex, 'repository': str(root), 'dataDirectory': str(directory),
                      'url': f'http://127.0.0.1:{server.server_port}', 'port': server.server_port,
                      'project': project['id'], 'pid': os.getpid()}
        server.repository_service = descriptor
        atomic_json(directory / 'service.json', descriptor)
        def stop(signum, frame):
            raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM, stop)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            (directory / 'service.json').unlink(missing_ok=True)
