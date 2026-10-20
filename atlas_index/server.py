from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .indexer import DEFAULT_DB, MAX_FILE_BYTES, safe_source
from .product import ProductStore
from .projects import ProjectRegistry

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_ORIGIN = re.compile(r'^http://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?$')


class GraphStore:
    def __init__(self, db_path=DEFAULT_DB):
        self.db_path = Path(db_path)
        if not self.db_path.is_file():
            raise ValueError('No graph yet. Run: python3 -m atlas_index index /path/to/repository')

    def connect(self):
        connection = sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def node(connection, node_id):
        row = connection.execute('SELECT data FROM nodes WHERE id=?', (node_id,)).fetchone()
        if row is None:
            raise KeyError('This place is not in the current graph.')
        return json.loads(row['data'])

    def breadcrumbs(self, connection, node):
        chain = [node]
        while node['parentId']:
            node = self.node(connection, node['parentId'])
            chain.append(node)
        return list(reversed(chain))

    @staticmethod
    def children(connection, node_id, limit=200):
        rows = connection.execute('SELECT data FROM nodes WHERE parent=? ORDER BY rank DESC, name COLLATE NOCASE, id LIMIT ?',
                                  (node_id, limit)).fetchall()
        return [json.loads(row['data']) for row in rows]

    def status(self):
        with closing(self.connect()) as connection:
            return json.loads(connection.execute("SELECT value FROM metadata WHERE key='status'").fetchone()['value'])

    def graph(self, parent_id='root', limit=200):
        with closing(self.connect()) as connection:
            parent = self.node(connection, parent_id)
            nodes = self.children(connection, parent_id, limit)
            identifiers = {node['id'] for node in nodes}
            edges = [dict(row) for row in connection.execute(
                'SELECT source,target,kind,weight FROM aggregate_edges WHERE parent=? ORDER BY weight DESC,source,target,kind',
                (parent_id,)) if row['source'] in identifiers and row['target'] in identifiers]
            total = parent['metrics']['children']
            return {'parent': parent, 'nodes': nodes, 'edges': edges, 'total': total,
                    'truncated': total > len(nodes), 'breadcrumbs': self.breadcrumbs(connection, parent)}

    def details(self, node_id):
        with closing(self.connect()) as connection:
            node = self.node(connection, node_id)
            relationships = {}
            for direction, column, other in [('incoming', 'target', 'source'), ('outgoing', 'source', 'target')]:
                if node['kind'] in {'directory', 'repository', 'file'}:
                    rows = connection.execute('SELECT kind,node AS other,weight,evidence FROM boundary_edges WHERE parent=? AND direction=? ORDER BY weight DESC,node LIMIT 120',
                                              (node_id, direction)).fetchall()
                else:
                    rows = connection.execute(f'SELECT kind,{other} AS other,weight,evidence FROM edges WHERE {column}=? AND kind!=? ORDER BY weight DESC,{other} LIMIT 120',
                                              (node_id, 'contains')).fetchall()
                relationships[direction] = [{'kind': row['kind'], 'node': self.node(connection, row['other']),
                                               'evidence': json.loads(row['evidence']), 'weight': row['weight']} for row in rows]
            children = self.children(connection, node_id)
            return {'node': node, 'children': children, 'relationships': relationships,
                    'breadcrumbs': self.breadcrumbs(connection, node),
                    'childrenTruncated': node['metrics']['children'] > len(children)}

    def search(self, query, limit=50):
        query = query.strip()[:200]
        if not query:
            return {'results': []}
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        with closing(self.connect()) as connection:
            rows = connection.execute('''SELECT data FROM nodes WHERE name LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\'
                ORDER BY CASE WHEN lower(name)=lower(?) THEN 0 WHEN name LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END,
                         rank DESC, name COLLATE NOCASE,id LIMIT ?''',
                                      (f'%{escaped}%', f'%{escaped}%', query, f'{escaped}%', limit)).fetchall()
            return {'results': [json.loads(row['data']) for row in rows]}

    def source(self, node_id, line=None):
        with closing(self.connect()) as connection:
            node = self.node(connection, node_id)
            if node['kind'] not in {'file', 'class', 'function'}:
                raise ValueError('Choose a file, function, or class to read its source.')
            status = json.loads(connection.execute("SELECT value FROM metadata WHERE key='status'").fetchone()['value'])
        candidate = safe_source(Path(status['root']), node['path'])
        if candidate is None:
            raise FileNotFoundError('The indexed source file is no longer available.')
        if candidate.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('This source file exceeds the 2 MB preview limit.')
        raw = candidate.read_bytes()
        if b'\0' in raw:
            raise ValueError('Binary source cannot be previewed.')
        lines = raw.decode('utf-8').splitlines()
        if line is not None and (not isinstance(line, int) or line < 1 or line > len(lines)):
            raise ValueError('Line must be a positive whole number within this file.')
        highlight = line if line is not None else (node['line'] or 1)
        start = max(1, highlight - 5)
        end = min(len(lines), highlight + 5 if line is not None else max(highlight + 12, node['endLine'] or highlight), start + 159)
        # Keep a malicious single-line source file from flooding the local browser.
        selected, used = [], 0
        for line in lines[start - 1:end]:
            clipped = line[:4000]
            if used + len(clipped) > 64000:
                break
            selected.append(clipped)
            used += len(clipped)
        return {'path': node['path'], 'startLine': start, 'lines': selected, 'highlightLine': highlight,
                'truncated': start + len(selected) - 1 < (node['endLine'] or len(lines))}


class ProjectNotReady(ValueError):
    pass


def create_server(db_path=DEFAULT_DB, port=4318, host='127.0.0.1', dist=None, taxonomy_path=None,
                  data_dir=None, registry=None):
    if host not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Atlas serves local source code and only binds to localhost.')
    if data_dir is not None and Path(db_path) == DEFAULT_DB:
        db_path = Path(data_dir) / 'atlas.db'
    data_dir = data_dir or Path(db_path).parent
    registry = registry or ProjectRegistry(data_dir=data_dir, legacy_db=db_path,
                                           **({'legacy_map': taxonomy_path} if taxonomy_path else {}))
    stores, product_stores = {}, {}
    dist = (Path(dist) if dist else PROJECT_ROOT / 'dist').resolve()

    def selected(identity):
        if identity:
            project = registry.project(identity)
        elif registry.active:
            project = registry.project()
        else:
            raise ProjectNotReady('Add a local repository to begin.')
        paths = registry.paths(project['id'])
        if not project.get('indexedAt') or not paths['db'].is_file():
            raise ProjectNotReady('This project has not finished its first index yet.')
        key = project['id']
        if key not in stores:
            stores[key] = GraphStore(paths['db'])
            product_stores[key] = ProductStore(stores[key], taxonomy_path=paths['map'], cache_path=paths['cache'])
        return project, stores[key], product_stores[key]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def send(self, status, payload, content_type='application/json; charset=utf-8', preflight=False):
            data = json.dumps(payload).encode() if isinstance(payload, (dict, list)) else payload
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            origin = self.headers.get('Origin', '')
            if LOCAL_ORIGIN.fullmatch(origin):
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            if preflight:
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, PUT, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.send_header('Access-Control-Max-Age', '600')
            self.end_headers()
            if data:
                self.wfile.write(data)

        def guard(self):
            try:
                hostname = urlsplit('http://' + self.headers.get('Host', '')).hostname
            except ValueError:
                hostname = None
            origin = self.headers.get('Origin')
            if hostname not in {'localhost', '127.0.0.1', '::1'} or (origin and not LOCAL_ORIGIN.fullmatch(origin)):
                self.send(403, {'error': 'Atlas accepts requests from this computer only.'})
                return False
            return True

        def body(self):
            if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                self.send(415, {'error': 'Use Content-Type: application/json.'})
                return None
            if self.headers.get('Transfer-Encoding'):
                self.send(400, {'error': 'Send a bounded JSON request body.'})
                return None
            try:
                length = int(self.headers.get('Content-Length', '-1'))
            except ValueError:
                length = -1
            if length < 0:
                self.send(411, {'error': 'A JSON Content-Length is required.'})
                return None
            if length > 1_100_000:
                self.send(413, {'error': 'The JSON request body exceeds 1.1 MB.'})
                return None
            try:
                raw = self.rfile.read(length)
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError()
            except (ValueError, UnicodeError, RecursionError):
                self.send(400, {'error': 'Send a valid JSON object.'})
                return None
            return body

        def errors(self, operation):
            try:
                operation()
            except ProjectNotReady as exc:
                self.send(409, {'error': str(exc)})
            except KeyError as exc:
                self.send(404, {'error': str(exc).strip("'")})
            except FileNotFoundError as exc:
                self.send(404, {'error': str(exc)})
            except (ValueError, UnicodeError) as exc:
                self.send(400, {'error': str(exc)})
            except (sqlite3.Error, OSError):
                self.send(500, {'error': 'Atlas could not read or save local project data.'})

        def do_OPTIONS(self):
            if not self.guard():
                return
            if self.headers.get('Access-Control-Request-Method', 'GET') not in {'GET', 'POST', 'PATCH', 'PUT', 'OPTIONS'}:
                self.send(405, {'error': 'That method is not supported.'})
                return
            self.send(204, b'', preflight=True)

        def unsupported(self):
            if self.guard():
                self.send(405, {'error': 'That method is not supported.'})

        do_DELETE = unsupported
        do_HEAD = unsupported
        do_TRACE = unsupported
        do_CONNECT = unsupported

        def do_GET(self):
            if self.guard():
                self.errors(self.get)

        def get(self):
            request = urlsplit(self.path)
            query = parse_qs(request.query, keep_blank_values=True)
            value = lambda name, default='': query.get(name, [default])[0]
            route = request.path
            if route == '/api/projects':
                self.send(200, registry.list())
                return
            match = re.fullmatch(r'/api/projects/([^/]+)/(map|changes)', route)
            if match:
                identity, action = match.groups()
                self.send(200, registry.map(identity) if action == 'map' else registry.changes(identity))
                return
            known = {'/api/status', '/api/product/graph', '/api/product/node', '/api/product/status',
                     '/api/graph', '/api/node', '/api/search', '/api/source'}
            if route in known:
                project, store, products = selected(value('project'))
                if route == '/api/status':
                    self.send(200, {**store.status(), 'project': project})
                elif route == '/api/product/graph':
                    self.send(200, products.graph(value('parent', 'products')))
                elif route == '/api/product/node':
                    self.send(200, products.details(value('id', 'products')))
                elif route == '/api/product/status':
                    self.send(200, products.status())
                elif route == '/api/graph':
                    try:
                        limit = max(1, min(500, int(value('limit', '200'))))
                    except ValueError:
                        raise ValueError('Limit must be a whole number.')
                    self.send(200, store.graph(value('parent', 'root'), limit))
                elif route == '/api/node':
                    self.send(200, store.details(value('id', 'root')))
                elif route == '/api/search':
                    self.send(200, store.search(value('q')))
                elif route == '/api/source':
                    line = None
                    if 'line' in query:
                        raw_line = value('line')
                        if not re.fullmatch(r'[1-9]\d*', raw_line):
                            raise ValueError('Line must be a positive whole number within this file.')
                        line = int(raw_line)
                    self.send(200, store.source(value('id'), line))
            elif route.startswith('/api/'):
                self.send(404, {'error': 'Unknown API route.'})
            else:
                path = unquote(route).lstrip('/') or 'index.html'
                target = (dist / path).resolve()
                if not target.is_relative_to(dist):
                    self.send(404, {'error': 'File not found.'})
                    return
                if not target.is_file():
                    target = dist / 'index.html'
                if not target.is_file():
                    self.send(404, {'error': 'The UI is not built yet. Start the development server or run npm run build.'})
                    return
                self.send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or 'application/octet-stream')

        def mutate(self):
            if not self.guard():
                return
            body = self.body()
            if body is None:
                return
            self.errors(lambda: self.write(body))

        do_POST = mutate
        do_PATCH = mutate
        do_PUT = mutate

        def write(self, body):
            route = urlsplit(self.path).path
            if route == '/api/projects' and self.command == 'POST':
                if any(key not in {'path', 'name'} for key in body):
                    raise ValueError('Register a project with path and optional name.')
                self.send(202, {'project': registry.register(body.get('path'), body.get('name'))})
                return
            match = re.fullmatch(r'/api/projects/([^/]+)(?:/(index|activate|map|suggestions))?', route)
            if not match:
                self.send(404, {'error': 'Unknown project route.'})
                return
            identity, action = match.groups()
            registry.project(identity)
            if self.command == 'PATCH' and action is None:
                self.send(200, {'project': registry.update(identity, body)})
            elif self.command == 'PUT' and action == 'map':
                self.send(200, registry.save_map(identity, body.get('map')))
            elif self.command == 'POST' and action == 'activate':
                self.send(200, {'project': registry.activate(identity)})
            elif self.command == 'POST' and action in {'index', 'suggestions'}:
                self.send(202, {'project': registry.enqueue(identity, action)})
            else:
                self.send(405, {'error': 'That method is not supported for this project route.'})

    class LocalServer(ThreadingHTTPServer):
        def server_close(self):
            registry.close()
            super().server_close()

    server = LocalServer((host, port), Handler)
    server.registry = registry
    return server


def serve(db_path=DEFAULT_DB, port=4318, data_dir=None):
    server = create_server(db_path, port, data_dir=data_dir)
    print(f'Atlas API is ready at http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
