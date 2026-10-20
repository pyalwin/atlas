"""A curated product map whose connections come only from indexed source edges."""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from collections import defaultdict
from contextlib import closing
from pathlib import Path

PROJECTION_VERSION = 3
DEFAULT_EXCLUSIONS = ('tests', 'test', 'deploy', 'devex', 'buildkite', '.github')
SAMPLE_LIMIT = 5


def matches(path, prefix):
    return path == prefix or path.startswith(prefix + '/')


def clean_prefix(value):
    value = value.replace('\\', '/')
    if value.startswith('/'):
        raise ValueError(f'Product paths must be repository-relative paths: {value}')
    value = value.rstrip('/')
    if not value or any(part in {'.', '..', ''} for part in value.split('/')):
        raise ValueError(f'Product paths must be repository-relative paths: {value}')
    return value


def curated_node(raw, node_id, kind, parent, category):
    return {'id': node_id, 'name': raw['name'], 'kind': kind, 'parentId': parent,
            'path': '', 'line': None, 'endLine': None, 'summary': raw['summary'],
            'summarySource': raw.get('summarySource', 'curated'), 'language': None, 'category': category,
            'evidence': raw.get('evidence', []),
            'reason': raw.get('reason', ''), 'confidence': raw.get('confidence'),
            'metrics': dict(files=0, symbols=0, lines=0, children=0, imports=0, importedBy=0)}


class Taxonomy:
    def __init__(self, raw):
        self.raw = raw
        self.nodes = {'products': curated_node({'name': 'The product universe',
                                               'summary': 'Explore what the product does, then follow its features into the code.'},
                                              'products', 'product-universe', None, 'product')}
        self.paths, self.parents, self.children = {}, {}, defaultdict(list)
        self.prefixes = {}
        for feature in raw.get('features', []):
            feature_id = 'product:' + feature['id']
            self.add(feature, feature_id, 'feature', 'products', 'product', feature_id, None)
            for capability in feature.get('capabilities', []):
                cap_id = f"capability:{feature['id']}:{capability['id']}"
                self.add(capability, cap_id, 'capability', feature_id, 'product', feature_id, cap_id)
        for foundation in raw.get('foundations', []):
            foundation_id = 'foundation:' + foundation['id']
            self.add(foundation, foundation_id, 'foundation', 'products', 'foundation', foundation_id, None)
        self.excluded = [*DEFAULT_EXCLUSIONS, *(clean_prefix(p) for p in raw.get('excludedPaths', []))]
        for parent, children in self.children.items():
            self.nodes[parent]['metrics']['children'] = len(children)
        # Foundations are supporting detail, rather than product-universe planets.
        self.nodes['products']['metrics']['children'] = len(raw.get('features', []))

    def add(self, raw, node_id, kind, parent, category, owner, capability):
        if node_id in self.nodes:
            raise ValueError(f'Duplicate product-map ID: {node_id}')
        self.nodes[node_id] = curated_node(raw, node_id, kind, parent, category)
        self.parents[node_id] = parent
        self.children[parent].append(node_id)
        paths = list(dict.fromkeys(clean_prefix(p) for p in raw.get('paths', [])))
        self.paths[node_id] = paths
        for path in paths:
            existing = self.prefixes.get(path)
            if existing and existing[0] != owner:
                raise ValueError(f'Two product areas claim the same path: {path}')
            if existing and existing[1] and capability and existing[1] != capability:
                raise ValueError(f'Two capabilities claim the same path: {path}')
            if not existing or capability:
                self.prefixes[path] = (owner, capability)

    def classify(self, path):
        name = path.rsplit('/', 1)[-1]
        if any(matches(path, prefix) for prefix in self.excluded) or name == 'conftest.py' or name.startswith('test_') or name.endswith('_test.py'):
            return 'excluded', None, None
        current = path
        while current:
            if current in self.prefixes:
                owner, capability = self.prefixes[current]
                return 'mapped', owner, capability
            current = current.rpartition('/')[0]
        return 'unmapped', None, None


def build_projection(store, raw):
    taxonomy = Taxonomy(raw)
    nodes = taxonomy.nodes
    status = store.status()
    coverage = {'mappedFiles': 0, 'excludedFiles': 0, 'unmappedFiles': 0,
                'productFiles': 0, 'foundationFiles': 0, 'indexedAt': status['indexedAt'],
                'indexedFiles': status['stats']['files'], 'notes': raw.get('notes', [])}
    files, node_files, code_roots, root_paths = {}, {}, {}, {}
    relations, components = {}, {}

    with closing(store.connect()) as db:
        # Only file records need JSON parsing. Symbols inherit their file's ownership.
        for row in db.execute("SELECT id,path,data FROM nodes WHERE kind='file'"):
            path = row['path']
            state, owner, capability = taxonomy.classify(path)
            coverage[state + 'Files'] += 1
            files[path] = (owner, capability)
            if not owner:
                continue
            file = json.loads(row['data'])
            coverage['productFiles' if nodes[owner]['category'] == 'product' else 'foundationFiles'] += 1
            for group_id in [owner, *([capability] if capability else [])]:
                for key in ('files', 'symbols', 'lines'):
                    nodes[group_id]['metrics'][key] += file['metrics'][key]
                if nodes[group_id]['kind'] == 'feature':
                    for key in ('files', 'symbols', 'lines'):
                        nodes['products']['metrics'][key] += file['metrics'][key]
        for row in db.execute("SELECT id,path FROM nodes WHERE kind IN ('file','class','function')"):
            assignment = files.get(row['path'])
            if assignment and assignment[0]:
                node_files[row['id']] = (row['path'], *assignment)

        for group_id, paths in taxonomy.paths.items():
            # A broad root already exposes its descendants; avoid duplicate roots.
            roots = [p for p in paths if not any(p != other and matches(p, other) for other in paths)]
            code_roots[group_id], root_paths[group_id] = [], []
            for path in roots:
                for node_id in ('dir:' + path, 'file:' + path):
                    row = db.execute('SELECT data FROM nodes WHERE id=?', (node_id,)).fetchone()
                    if row:
                        node = json.loads(row['data'])
                        code_roots[group_id].append(node)
                        root_paths[group_id].append((path, node_id))
                        break
            if not taxonomy.children[group_id]:
                nodes[group_id]['metrics']['children'] = len(code_roots[group_id])
            nodes[group_id]['metrics']['codeAreas'] = len(code_roots[group_id])
            if nodes[group_id]['kind'] == 'feature':
                nodes[group_id]['metrics']['capabilities'] = len(taxonomy.children[group_id])

        def root_for(group_id, path):
            return next((node_id for prefix, node_id in root_paths[group_id] if matches(path, prefix)), None)

        def add(mapping, key, row, source_path, target_path):
            record = mapping.get(key)
            if record is None:
                record = mapping[key] = {'weight': 0, 'referenceSites': 0,
                                         'breakdown': {'imports': 0, 'calls': 0, 'inherits': 0}, 'evidence': []}
            record['weight'] += 1  # One distinct persisted (source, target, kind) edge.
            record['referenceSites'] += row['weight']
            record['breakdown'][row['kind']] += 1
            if len(record['evidence']) < SAMPLE_LIMIT:
                proof = json.loads(row['evidence'])
                if proof:
                    record['evidence'].append({'path': source_path, 'line': proof[0]['line'],
                                               'targetPath': target_path, 'targetLine': None,
                                               'sourceId': row['source'], 'targetId': row['target']})

        for row in db.execute("SELECT source,target,kind,weight,evidence FROM edges WHERE kind IN ('imports','calls','inherits')"):
            source, target = node_files.get(row['source']), node_files.get(row['target'])
            if not source or not target:
                continue
            source_path, source_owner, source_cap = source
            target_path, target_owner, target_cap = target
            if source_owner != target_owner:
                add(relations, (source_owner, target_owner), row, source_path, target_path)
            if source_cap:
                target_group = target_cap or target_owner
                if target_group not in {source_cap, source_owner}:
                    add(relations, (source_cap, target_group), row, source_path, target_path)
            elif target_cap and source_owner != target_owner:
                add(relations, (source_owner, target_cap), row, source_path, target_path)
            if source_owner == target_owner:
                same_groups = [source_owner]
                if source_cap and source_cap == target_cap:
                    same_groups.append(source_cap)
                for group_id in same_groups:
                    source_root, target_root = root_for(group_id, source_path), root_for(group_id, target_path)
                    if source_root and target_root and source_root != target_root:
                        add(components, (group_id, source_root, target_root), row, source_path, target_path)

        # Resolve line numbers only for the small proof sample, never for every symbol.
        target_lines = {}
        for record in [*relations.values(), *components.values()]:
            for proof in record['evidence']:
                target_id = proof['targetId']
                if target_id not in target_lines:
                    row = db.execute('SELECT data FROM nodes WHERE id=?', (target_id,)).fetchone()
                    target_lines[target_id] = json.loads(row['data'])['line'] if row else 1
                proof['targetLine'] = target_lines[target_id] or 1

    def decorate(source, target, record):
        breakdown = record['breakdown']
        labels = ', '.join(f'{count:,} {kind}' for kind, count in breakdown.items() if count)
        return {'source': source, 'target': target, 'kind': 'references', **record,
                'sampleCount': len(record['evidence']),
                'description': f'{record["weight"]:,} static code references ({labels}). This describes code dependencies, not a user journey.'}

    links = [decorate(source, target, record) for (source, target), record in sorted(relations.items())]
    component_links = defaultdict(list)
    for (parent, source, target), record in sorted(components.items()):
        component_links[parent].append(decorate(source, target, record))
    for edge in links:
        # Feature counters use the feature-level projection only, avoiding cap duplication.
        if nodes[edge['source']]['kind'] == 'capability' or nodes[edge['target']]['kind'] == 'capability':
            if nodes[edge['source']]['kind'] == 'capability':
                nodes[edge['source']]['metrics']['imports'] += edge['breakdown']['imports']
            if nodes[edge['target']]['kind'] == 'capability':
                nodes[edge['target']]['metrics']['importedBy'] += edge['breakdown']['imports']
            continue
        nodes[edge['source']]['metrics']['imports'] += edge['breakdown']['imports']
        nodes[edge['target']]['metrics']['importedBy'] += edge['breakdown']['imports']
    return {'nodes': nodes, 'children': dict(taxonomy.children), 'links': links,
            'componentLinks': dict(component_links), 'codeRoots': code_roots, 'coverage': coverage,
            'supportingGroups': [{'foundation': node['id'], 'features': sorted({edge['source'] for edge in links
                                  if edge['target'] == node['id'] and nodes[edge['source']]['kind'] == 'feature'})}
                                 for node in nodes.values() if node['kind'] == 'foundation']}


class ProductStore:
    def __init__(self, store, taxonomy_path=None, cache_path=None):
        self.store = store
        self.taxonomy_path = Path(taxonomy_path) if taxonomy_path else store.db_path.with_name('product-map.json')
        self.cache_path = Path(cache_path) if cache_path else store.db_path.with_name('product-projection.json')
        self.lock = threading.Lock()
        self.signature = None
        self.projection = None

    def ensure(self):
        if not self.taxonomy_path.is_file():
            raise ValueError('The product map has not been created yet.')
        signature = (self.store.db_path.stat().st_mtime_ns, self.taxonomy_path.stat().st_mtime_ns)
        if self.projection is not None and signature == self.signature:
            return self.projection
        with self.lock:
            if self.projection is not None and signature == self.signature:
                return self.projection
            raw_bytes = self.taxonomy_path.read_bytes()
            digest = hashlib.sha256(raw_bytes).hexdigest()
            key = {'version': PROJECTION_VERSION, 'taxonomy': digest, 'indexedAt': self.store.status()['indexedAt']}
            try:
                cached = json.loads(self.cache_path.read_text())
                if cached['key'] == key:
                    self.projection, self.signature = cached['projection'], signature
                    return self.projection
            except (OSError, ValueError, KeyError):
                pass
            projection = build_projection(self.store, json.loads(raw_bytes))
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.cache_path.parent,
                                             prefix='product-projection-', suffix='.building.json', delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({'key': key, 'projection': projection}, handle)
            temporary.replace(self.cache_path)
            self.projection, self.signature = projection, signature
            return projection

    def node(self, node_id):
        data = self.ensure()
        try:
            return data['nodes'][node_id]
        except KeyError:
            raise KeyError('This product area is not in the current map.')

    def breadcrumbs(self, node_id):
        node, result = self.node(node_id), []
        while node:
            result.append(node)
            node = self.node(node['parentId']) if node['parentId'] else None
        return list(reversed(result))

    def children(self, node_id):
        data = self.ensure()
        children = [data['nodes'][child] for child in data['children'].get(node_id, [])]
        if node_id == 'products':
            return [node for node in children if node['kind'] == 'feature']
        return children or data['codeRoots'].get(node_id, [])

    def graph(self, parent='products'):
        data, node = self.ensure(), self.node(parent)
        children = self.children(parent)
        ids = {child['id'] for child in children}
        links = data['links'] if any(child['kind'] in {'feature', 'capability'} for child in children) else data['componentLinks'].get(parent, [])
        result = {'parent': node, 'nodes': children,
                  'edges': [edge for edge in links if edge['source'] in ids and edge['target'] in ids],
                  'total': len(children), 'truncated': False, 'breadcrumbs': self.breadcrumbs(parent),
                  'layer': 'product', 'coverage': data['coverage']}
        if parent == 'products':
            result['foundations'] = [n for n in data['nodes'].values() if n['kind'] == 'foundation']
            result['supportingGroups'] = data['supportingGroups']
        return result

    def details(self, node_id):
        data, node = self.ensure(), self.node(node_id)
        incoming, outgoing, foundations = [], [], []
        for edge in data['links']:
            if edge['source'] != node_id and edge['target'] != node_id:
                continue
            is_outgoing = edge['source'] == node_id
            other = data['nodes'][edge['target'] if is_outgoing else edge['source']]
            item = {'kind': 'references', 'node': other, **{key: value for key, value in edge.items() if key not in {'source', 'target', 'kind'}}}
            if is_outgoing and other['kind'] == 'foundation' and node['kind'] != 'foundation':
                foundations.append(item)
            elif node['kind'] in {'feature', 'foundation'} and other['kind'] == 'feature':
                (outgoing if is_outgoing else incoming).append(item)
            elif node['kind'] == 'capability' and other['kind'] in {'capability', 'feature'}:
                (outgoing if is_outgoing else incoming).append(item)
        for items in (incoming, outgoing, foundations):
            items.sort(key=lambda item: (-item['weight'], item['node']['name']))
        return {'node': node, 'children': self.children(node_id),
                'relationships': {'incoming': incoming, 'outgoing': outgoing},
                'foundations': foundations, 'codeRoots': data['codeRoots'].get(node_id, []),
                'breadcrumbs': self.breadcrumbs(node_id), 'layer': 'product'}

    def status(self):
        return self.ensure()['coverage']
