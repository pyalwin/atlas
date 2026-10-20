"""Suggest a reviewable product map from an existing local index.

Folder names are grouping hints, not proof of product behavior. This module never
imports repository code, runs commands, follows source symlinks, or creates edges.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import sqlite3
import tokenize
import unicodedata
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path, PurePosixPath

from .indexer import EXTENSIONS, safe_source

MAX_FEATURES = 30
MAX_CAPABILITIES = 8
MAX_EVIDENCE = 5
MAX_PATHS = 200
MAX_EXCLUSIONS = 1000
MAX_SOURCE_BYTES = 64 * 1024
SOURCE_BUDGET = 8 * 1024 * 1024

# These names describe layout or implementation layers, not user outcomes.
WRAPPERS = {
    'src', 'source', 'sources', 'app', 'apps', 'application', 'applications',
    'package', 'packages', 'pkg', 'module', 'modules', 'domain', 'domains',
    'feature', 'features', 'service', 'services', 'route', 'routes', 'controller',
    'controllers', 'handler', 'handlers', 'endpoint', 'endpoints', 'api', 'apis',
    'backend', 'frontend', 'server', 'client', 'web', 'internal', 'implementation',
    'page', 'pages', 'v1', 'v2', 'v3',
}
ENTRY_FILES = {
    'init', 'index', 'main', 'app', 'application', 'server', 'client', 'cli',
    'manage', 'setup', 'router', 'routes', 'handler', 'handlers', 'service',
    'controller', 'module', 'package', 'layout', 'page', 'loading', 'error',
}
EXCLUDED_DIRS = {
    '.git', '.github', '.gitlab', '.buildkite', 'buildkite', '.circleci', '.vscode',
    '.idea', '.venv', 'venv', 'node_modules', '__pycache__', 'vendor', 'generated',
    '_generated', 'dist', 'build', '.next', '.cache', 'coverage', 'htmlcov',
    'test', 'tests', '__tests__', '__mocks__', 'fixtures', 'testdata', 'e2e',
    'integration_tests', 'unit_tests', 'spec', 'specs', 'cypress', 'playwright',
    'deploy', 'deployment', 'deployments', 'devex', 'ci', 'scripts', 'tools',
    'tooling', 'benchmarks', 'benchmark', 'examples', '.agents', '.codex',
    '.claude', '.aws', '.ssh', '.secrets',
}
FOUNDATIONS = {
    'identity': ('Identity & access', {'auth', 'authentication', 'authorization',
                                     'permissions', 'security', 'identity', 'rbac'}),
    'storage': ('Storage & data', {'db', 'database', 'databases', 'storage',
                                  'persistence', 'orm', 'migrations', 'migration',
                                  'cache', 'caches', 'models', 'model', 'schemas',
                                  'schema', 'dto', 'types'}),
    'configuration': ('Configuration & logging', {'config', 'configs', 'configuration',
                                                  'settings', 'logging', 'logger',
                                                  'loggers', 'telemetry', 'observability'}),
    'shared': ('Shared helpers', {'common', 'shared', 'utils', 'util', 'utilities',
                                'helpers', 'helper', 'lib', 'core', 'base',
                                'framework', 'platform', 'constants', 'exceptions'}),
    'presentation': ('Shared interface code', {'components', 'ui', 'styles', 'assets',
                                              'public', 'fonts', 'icons', 'themes'}),
    'transport': ('Network helpers', {'http', 'network', 'rpc', 'middleware', 'clients'}),
    'documentation': ('Project documentation', {'docs', 'doc', 'documentation'}),
}
FOUNDATION_NAMES = {name: key for key, (_, names) in FOUNDATIONS.items() for name in names}
MANIFESTS = {
    'package.json', 'pyproject.toml', 'setup.cfg', 'setup.py', 'tsconfig.json',
    'jsconfig.json', 'cargo.toml', 'go.mod', 'requirements.txt', 'pom.xml',
}
DOCUMENTS = {'readme', 'license', 'licence', 'changelog', 'contributing', 'authors',
             'code-of-conduct', 'security', 'agents'}
NON_CODE_LANGUAGES = {'Markdown', 'JSON', 'YAML', 'TOML'}


def _slug(name):
    name = re.sub(r'([a-z0-9])([A-Z])', r'\1-\2', name)
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def _title(name):
    words = _slug(name).split('-')
    return ' '.join(word.upper() if word in {'ai', 'api', 'crm', 'pdf', 'sql'} else word.capitalize()
                    for word in words)[:160] or name[:160] or 'Application code'


def _key(name):
    key = _slug(name)
    digest = hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]
    return key if key and len(key) <= 90 else (key[:75] + '-' if key else 'area-') + digest


def _valid_path(path):
    return (isinstance(path, str) and 0 < len(path) <= 1000 and not path.startswith('/')
            and not re.match(r'^[a-zA-Z]:', path) and '\\' not in path
            and not any(ord(character) < 32 for character in path)
            and all(part not in {'', '.', '..'} for part in path.split('/')))


def _within(path, prefix):
    return path == prefix or path.startswith(prefix + '/')


def _minimal_paths(paths):
    result, seen = [], set()
    for path in sorted(set(paths), key=lambda value: (value.count('/'), value)):
        parts = path.split('/')
        if any('/'.join(parts[:length]) in seen for length in range(1, len(parts))):
            continue
        result.append(path)
        seen.add(path)
    return sorted(result)


def _excluded_prefix(path):
    parts = path.split('/')
    for index, part in enumerate(parts[:-1]):
        if part.lower() in EXCLUDED_DIRS:
            return '/'.join(parts[:index + 1])
    name = parts[-1].lower()
    if (name.startswith(('test_', '.env')) or name == 'conftest.py'
            or re.search(r'(?:[._-](?:test|spec))\.[^.]+$', name)
            or re.search(r'(^|[._-])(secret|secrets|credential|credentials|private[-_]?key)([._-]|$)', name)):
        return path
    return None


def _foundation(path):
    parts = path.split('/')
    for index, part in enumerate(parts[:-1]):
        category = FOUNDATION_NAMES.get(_slug(part))
        if category:
            return category, '/'.join(parts[:index + 1])
    name = parts[-1].lower()
    stem = _slug(PurePosixPath(path).stem)
    if name in MANIFESTS or re.search(r'(^|[._-])(config|settings)([._-]|$)', name):
        return 'configuration', path
    if len(parts) == 1 and stem in DOCUMENTS:
        return 'documentation', path
    category = FOUNDATION_NAMES.get(stem)
    return (category, path) if category else None


def _wrapper(name, namespace):
    return (_slug(name) in WRAPPERS or _slug(name) == namespace
            or name.startswith('@') or (name.startswith('[') and name.endswith(']')))


def _area(path, namespace):
    """Return the first named folder beyond structural wrappers, or a layer file."""
    parts = path.split('/')
    for index, part in enumerate(parts[:-1]):
        if not _wrapper(part, namespace):
            return _key(part), part, '/'.join(parts[:index + 1])
    # routes/billing.py and services/billing.ts can describe the same area.
    if len(parts) > 1:
        name = re.sub(r'[-_](routes?|routers?|services?|controllers?|handlers?)$', '',
                      PurePosixPath(path).stem, flags=re.I)
        key = _slug(name)
        if key and key not in ENTRY_FILES and key not in WRAPPERS:
            return _key(name), name, path
    return None


class _Sources:
    def __init__(self, root, records):
        self.root, self.records = root, records
        self.cache = {}
        self.bytes_read = 0
        self.limit_reached = False

    def read(self, path):
        if path in self.cache:
            return self.cache[path]
        if self.bytes_read >= SOURCE_BUDGET:
            self.limit_reached = True
            return None
        candidate = safe_source(self.root, path)
        text = None
        if candidate:
            try:
                with candidate.open('rb') as source:
                    raw = source.read(min(MAX_SOURCE_BYTES, SOURCE_BUDGET - self.bytes_read))
                self.bytes_read += len(raw)
                if b'\0' not in raw:
                    text = raw.decode('utf-8')
            except (OSError, UnicodeError):
                pass
        self.cache[path] = text
        return text

    def evidence(self, path):
        text = self.read(path)
        if text is None:
            return None
        lines = text.splitlines()
        if not lines:
            return None
        if PurePosixPath(path).name.lower().startswith('readme'):
            # Skip titles, badges, code fences and lists; retain only a short prose paragraph.
            fenced, start, paragraph = False, None, []
            for number, line in enumerate(lines, 1):
                value = line.strip()
                if value.startswith(('```', '~~~')):
                    fenced = not fenced
                    if paragraph:
                        break
                    continue
                if fenced or not value or re.match(r'^(?:#|[!<>\[|]|[-*=]{3,}|[-*+] |[0-9]+[.)] )', value):
                    if paragraph:
                        break
                    continue
                start = start or number
                paragraph.append(value)
                if sum(map(len, paragraph)) > 240:
                    break
            if paragraph:
                return {'path': path, 'line': start, 'endLine': start + len(paragraph) - 1,
                        'kind': 'readme', 'excerpt': _excerpt(' '.join(paragraph))}
        if PurePosixPath(path).suffix in {'.py', '.pyi'}:
            try:
                for token in tokenize.generate_tokens(io.StringIO(text).readline):
                    if token.type in {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.ENCODING}:
                        continue
                    if token.type == tokenize.STRING:
                        value = ast.literal_eval(token.string)
                        if isinstance(value, str) and value.strip():
                            return {'path': path, 'line': token.start[0], 'endLine': token.end[0],
                                    'kind': 'module-docstring', 'excerpt': _excerpt(value)}
                    break
            except (SyntaxError, ValueError, tokenize.TokenError, IndentationError, RecursionError):
                pass
        number = next((index for index, line in enumerate(lines, 1) if line.strip()), None)
        return {'path': path, 'line': number, 'endLine': number, 'kind': 'file'} if number else None


def _excerpt(text):
    text = re.split(r'\n\s*\n', text.strip(), maxsplit=1)[0]
    text = ''.join(character for character in text if ord(character) >= 32 or character.isspace())
    text = re.sub(r'\s+', ' ', text).strip()
    return text if len(text) <= 220 else text[:219].rsplit(' ', 1)[0] + '…'


def _evidence(paths, records, sources, fallback=False):
    candidates = []
    readmes = defaultdict(list)
    for path in records:
        if PurePosixPath(path).name.lower().startswith('readme'):
            readmes[str(PurePosixPath(path).parent)].append(path)
    # Prefer a group's own README, then an initializer, then another owned file.
    for path in sorted(paths):
        directory = str(PurePosixPath(path).parent) if path in sources.records else path
        candidates.extend(sorted(readmes[directory]))
    if fallback:
        candidates.extend(sorted(path for path in sources.records
                                 if '/' not in path and PurePosixPath(path).name.lower().startswith('readme')))
    candidates.extend(sorted(records, key=lambda path: (
        not PurePosixPath(path).name.lower().startswith('readme'),
        PurePosixPath(path).name != '__init__.py', path.count('/'), path)))
    evidence = []
    # Bounded attempts also keep stale or unreadable source trees cheap to inspect.
    for path in list(dict.fromkeys(candidates))[:MAX_EVIDENCE * 3]:
        item = sources.evidence(path)
        if item:
            evidence.append(item)
        if len(evidence) >= MAX_EVIDENCE:
            break
    return evidence


def _node(key, name, paths, records, sources, reason, *, fallback=False, foundation=False):
    paths = _minimal_paths(paths)[:MAX_PATHS]
    prefixes = set(paths)
    records = [path for path in records if any(
        '/'.join(path.split('/')[:length]) in prefixes for length in range(1, len(path.split('/')) + 1))]
    evidence = _evidence(paths, records, sources, fallback)
    note = next((item for item in evidence if item.get('excerpt')), None)
    summary = (f'Suggested support area for {_title(name).lower()} files.' if foundation
               else f'Suggested group for {_title(name).lower()}.')
    if note:
        summary += f' {PurePosixPath(note["path"]).name} says: “{note["excerpt"]}”'
    else:
        summary += ' The folder and file names suggest this grouping; its purpose needs review.'
    return {'id': key, 'name': name if foundation else _title(name), 'summary': summary, 'paths': paths,
            'evidence': evidence, 'status': 'suggested', 'reviewed': False,
            'origin': 'local-discovery', 'confidence': 'low' if fallback else 'medium',
            'reason': reason, 'summarySource': note['kind'] if note else 'file-layout'}


def _capabilities(feature, records, sources, namespace, anchors):
    groups, direct = {}, []
    for path in records:
        prefix = next((prefix for prefix in anchors if _within(path, prefix)), None)
        if not prefix or path == prefix:
            direct.append(path)
            continue
        rest = path[len(prefix) + 1:].split('/')
        for index, part in enumerate(rest[:-1]):
            if not _wrapper(part, namespace):
                key = _key(part)
                group = groups.setdefault(key, {'name': part, 'paths': set(), 'files': []})
                group['paths'].add(prefix + '/' + '/'.join(rest[:index + 1]))
                group['files'].append(path)
                break
        else:
            direct.append(path)
    if not groups:
        return []
    ordered = sorted(groups, key=lambda key: (-len(groups[key]['files']), key))
    kept = ordered[:MAX_CAPABILITIES - 1] if len(ordered) + bool(direct) > MAX_CAPABILITIES else ordered
    remainder = direct + [path for key in ordered if key not in kept for path in groups[key]['files']]
    def contained_paths(paths):
        return {candidate if _within(candidate, parent) else parent
                for candidate in paths for parent in feature['paths']
                if _within(candidate, parent) or _within(parent, candidate)}

    result = [_node(key, groups[key]['name'], contained_paths(groups[key]['paths']), groups[key]['files'], sources,
                    'Named subfolders within the suggested area; this is not a verified product capability.')
              for key in kept]
    if remainder:
        key = _unique_id('other-code', set(kept))
        result.append(_node(key, 'Other code', remainder, remainder, sources,
                            'Files without a separate named subfolder, or beyond the capability limit.',
                            fallback=True))
    return result


def _unique_id(preferred, used):
    value, number = preferred, 2
    while value in used:
        value, number = f'{preferred}-{number}', number + 1
    return value


def _partition_paths(records, areas, support, excluded_files, namespace):
    """Compress only uniformly owned subtrees, so truncation cannot misassign files."""
    assignments = {path: 'excluded' for path in excluded_files}
    for kind, groups in [('product', areas), ('foundation', support)]:
        for key, group in groups.items():
            assignments.update((path, f'{kind}:{key}') for path in group['files'])
    owners = {}
    for path, owner in assignments.items():
        parts = path.split('/')
        for length in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:length])
            if prefix not in owners:
                owners[prefix] = owner
            elif owners[prefix] != owner:
                owners[prefix] = None
    result, accepted = defaultdict(list), set()
    for path in sorted(owners, key=lambda value: (value.count('/'), value)):
        owner = owners[path]
        if not owner or (owner != 'excluded' and path not in records
                         and _wrapper(path.rsplit('/', 1)[-1], namespace)):
            continue
        parts = path.split('/')
        if any('/'.join(parts[:length]) in accepted for length in range(1, len(parts))):
            continue
        accepted.add(path)
        result[owner].append(path)
    return result


def discover_product_map(db_path, root):
    """Return deterministic, unreviewed suggestions; never write the repository or index."""
    root, db_path = Path(root).resolve(), Path(db_path).resolve()
    if not root.is_dir():
        raise ValueError(f'Repository does not exist: {root}')
    if not db_path.is_file():
        raise ValueError(f'Index does not exist: {db_path}')
    records, languages, status = {}, Counter(), {}
    invalid = 0
    try:
        with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            row = db.execute("SELECT value FROM metadata WHERE key='status'").fetchone()
            status = json.loads(row[0]) if row else {}
            if status.get('root') and Path(status['root']).resolve() != root:
                raise ValueError('The index belongs to a different repository. Reindex the selected folder.')
            for path, data in db.execute("SELECT path,data FROM nodes WHERE kind='file' ORDER BY path"):
                if not _valid_path(path):
                    invalid += 1
                    continue
                record = json.loads(data)
                records[path] = record
                languages[record.get('language') or EXTENSIONS.get(PurePosixPath(path).suffix, 'Unknown')] += 1
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot read the source index: {exc}') from exc

    sources = _Sources(root, records)
    namespace = _slug(root.name)
    areas, support = {}, {}
    excluded_files, loose = [], []
    for path in records:
        exclusion = _excluded_prefix(path)
        if exclusion:
            excluded_files.append(path)
            continue
        foundation = _foundation(path)
        if foundation:
            key, prefix = foundation
            group = support.setdefault(key, {'paths': set(), 'files': []})
            group['paths'].add(prefix)
            group['files'].append(path)
            continue
        area = _area(path, namespace)
        if not area:
            loose.append(path)
            continue
        key, name, prefix = area
        group = areas.setdefault(key, {'name': name, 'paths': set(), 'files': []})
        group['paths'].add(prefix)
        group['files'].append(path)

    # A few package bootstrap files should not create a separate planet in a large project.
    if areas and loose and all(PurePosixPath(path).name in {'__init__.py', '__init__.pyi'} for path in loose):
        group = support.setdefault('shared', {'paths': set(), 'files': []})
        group['paths'].update(loose)
        group['files'].extend(loose)
        loose = []
    if loose:
        key = _unique_id('repository-code', set(areas))
        areas[key] = {'name': 'Application code', 'paths': set(loose), 'files': loose, 'fallback': True}
    ordered = sorted(areas, key=lambda key: (
        -sum(records[path].get('language') not in NON_CODE_LANGUAGES for path in areas[key]['files']),
        -len(areas[key]['files']), key))
    limited = len(ordered) > MAX_FEATURES
    if limited:
        overflow = [areas[key] for key in ordered[MAX_FEATURES - 1:]]
        areas = {key: areas[key] for key in ordered[:MAX_FEATURES - 1]}
        key = _unique_id('other-application-code', set(areas))
        areas[key] = {'name': 'Other application code', 'fallback': True,
                      'paths': {path for group in overflow for path in group['paths']},
                      'files': [path for group in overflow for path in group['files']]}

    partition = _partition_paths(records, areas, support, excluded_files, namespace)
    excluded = sorted(partition['excluded'])[:MAX_EXCLUSIONS]
    paths_limited = len(partition['excluded']) > MAX_EXCLUSIONS
    for kind, groups in [('product', areas), ('foundation', support)]:
        for key, group in groups.items():
            paths = sorted(partition[f'{kind}:{key}'])
            paths_limited |= len(paths) > MAX_PATHS
            group['anchors'] = _minimal_paths(group['paths'])
            group['paths'] = paths[:MAX_PATHS]
            prefixes = set(group['paths'])
            group['files'] = [path for path in group['files'] if any(
                '/'.join(path.split('/')[:length]) in prefixes
                for length in range(1, len(path.split('/')) + 1))]

    features = []
    for key, group in sorted(areas.items()):
        fallback = group.get('fallback', False)
        reason = ('Files without a clearer feature boundary, or beyond the feature limit.' if fallback
                  else 'Named folders or layer files after structural wrappers. Matching names across layers are grouped together.')
        node = _node(key, group['name'], group['paths'], group['files'], sources, reason, fallback=fallback)
        node['capabilities'] = _capabilities(node, group['files'], sources, namespace, group['anchors'])
        features.append(node)
    foundations = [_node(key, FOUNDATIONS[key][0], group['paths'], group['files'], sources,
                         'Common infrastructure names suggest shared support code; review this ownership.',
                         foundation=True)
                   for key, group in sorted(support.items())]
    product_count = sum(len(group['files']) for group in areas.values())
    foundation_count = sum(len(group['files']) for group in support.values())
    exclusion_set = set(excluded)
    excluded_count = sum(any('/'.join(path.split('/')[:length]) in exclusion_set
                             for length in range(1, len(path.split('/')) + 1))
                         or PurePosixPath(path).name == 'conftest.py'
                         or PurePosixPath(path).name.startswith('test_')
                         or PurePosixPath(path).name.endswith('_test.py') for path in excluded_files)
    reported = {item['name']: item for item in status.get('languageAnalysis', [])
                if isinstance(item, dict) and isinstance(item.get('name'), str)}
    language_analysis = []
    for name, count in sorted(languages.items()):
        detail = reported.get(name, {})
        mode = detail.get('mode', 'semantic' if name == 'Python' else 'file-only')
        if mode not in {'semantic', 'imports-and-symbols', 'file-only'}:
            mode = 'file-only'
        parsed = detail.get('parsedFiles')
        language_analysis.append({'name': name, 'files': count, 'mode': mode,
                                  'parsedFiles': min(count, max(0, parsed)) if isinstance(parsed, int) else None})
    semantic = [item['name'] for item in language_analysis if item['mode'] == 'semantic']
    imports_symbols = [item['name'] for item in language_analysis if item['mode'] == 'imports-and-symbols']
    file_only = [item['name'] for item in language_analysis if item['mode'] == 'file-only']
    coverage_note = 'Language coverage follows the saved index. ' + ' '.join(
        f'{label}: {", ".join(names)}.' for label, names in
        [('Static symbol and relationship analysis', semantic),
         ('Imports and symbols only', imports_symbols), ('Files only', file_only)] if names)
    return {
        'version': 1, 'features': features, 'foundations': foundations,
        'excludedPaths': _minimal_paths(excluded),
        'notes': [
            'Automatically suggested from indexed file paths and local documentation; no one has reviewed these boundaries.',
            'Matching folder names are a grouping hint, not proof that code serves the same user outcome.',
            'Descriptions quote source notes when available. A note about one file may not describe its whole group.',
            'Connections must come from the source index. This discovery pass creates no relationships.',
            coverage_note,
            'Source evidence is read from current files; the graph and file inventory come from the last index.',
            *(['Some paths exceed the suggestion size limit and remain available in Technical depth.'] if paths_limited else []),
            *(['No product feature boundary was found; shared support areas are listed separately.'] if not features else []),
        ],
        'discovery': {'method': 'local-path-and-documentation-heuristics', 'version': 1,
                      'status': 'suggested', 'reviewed': False, 'confidenceScale': 'heuristic, not a probability',
                      'maxFeatures': MAX_FEATURES, 'maxCapabilitiesPerFeature': MAX_CAPABILITIES,
                      'featureLimitReached': limited, 'pathLimitReached': paths_limited},
        'analysis': {
            'indexedFiles': len(records) + invalid, 'productFiles': product_count,
            'foundationFiles': foundation_count, 'excludedFiles': excluded_count,
            'unmappedFiles': len(records) + invalid - product_count - foundation_count - excluded_count,
            'skippedUnsafePaths': invalid,
            'languages': language_analysis, 'semanticLanguages': semantic,
            'importsAndSymbolsLanguages': imports_symbols, 'fileOnlyLanguages': file_only,
            'parseWarnings': status.get('parseWarnings', 0), 'indexedAt': status.get('indexedAt'),
            'sourceBytesRead': sources.bytes_read, 'sourceReadLimitReached': sources.limit_reached,
            'relationshipsCreated': 0,
        },
    }
