import argparse
import json
import sys
from pathlib import Path

from .indexer import DEFAULT_DB, index_repository
from .projects import ProjectRegistry, read_index_status
from .server import serve

parser = argparse.ArgumentParser(description='Build and explore a local source-code graph.')
sub = parser.add_subparsers(dest='command', required=True)
index = sub.add_parser('index', help='Read a Git repository and rebuild its local graph.')
index.add_argument('repository', type=Path)
index.add_argument('--db', type=Path, help='Write a standalone index to this explicit path.')
index.add_argument('--data-dir', type=Path, help='Store the managed project index and registry here.')
server = sub.add_parser('serve', help='Serve the graph API and built UI on localhost.')
server.add_argument('--port', type=int, default=4318)
server.add_argument('--db', type=Path, default=DEFAULT_DB)
server.add_argument('--data-dir', type=Path, help='Store the local project registry and per-project indexes here.')
repository_server = sub.add_parser('repository-serve', help='Serve one checkout with persistent data in its .atlas directory.')
repository_server.add_argument('repository', type=Path)
args = parser.parse_args()
if sys.version_info < (3, 12):
    parser.exit(1, 'Atlas needs Python 3.12 or newer. Select a supported Python interpreter, or set ATLAS_PYTHON for the npm launcher.\n')
try:
    if args.command == 'index':
        if args.db:
            status = index_repository(args.repository, args.db)
        else:
            data_dir = args.data_dir or DEFAULT_DB.parent
            registry = ProjectRegistry(data_dir=data_dir, legacy_db=Path(data_dir) / 'atlas.db', monitor=False)
            try:
                project = registry.register(str(args.repository))
                registry.enqueue(project['id'], 'index')
                registry.queue.join()
                project = registry.project(project['id'])
                if project['state'] != 'ready':
                    raise ValueError((project.get('job') or {}).get('error', 'The project index could not finish.'))
                status = read_index_status(registry.paths(project['id'])['db'])
            finally:
                registry.close()
        print(json.dumps(status, indent=2))
    elif args.command == 'repository-serve':
        from .repository import serve_repository
        serve_repository(args.repository)
    else:
        serve(args.db, args.port, data_dir=args.data_dir)
except ValueError as exc:
    parser.exit(1, f'{exc}\n')
