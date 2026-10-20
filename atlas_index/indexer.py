from __future__ import annotations

import ast
import json
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .javascript import SUFFIXES as JAVASCRIPT_SUFFIXES, resolve_import, scan_batch

DEFAULT_DB = Path(__file__).resolve().parent.parent / 'data' / 'atlas.db'
EXTENSIONS = {
    '.py': 'Python', '.pyi': 'Python', '.ts': 'TypeScript', '.tsx': 'TypeScript',
    '.js': 'JavaScript', '.jsx': 'JavaScript', '.go': 'Go', '.rs': 'Rust',
    '.mjs': 'JavaScript', '.cjs': 'JavaScript', '.mts': 'TypeScript', '.cts': 'TypeScript',
    '.java': 'Java', '.rb': 'Ruby', '.sql': 'SQL', '.sh': 'Shell',
    '.yaml': 'YAML', '.yml': 'YAML', '.toml': 'TOML', '.json': 'JSON',
    '.md': 'Markdown', '.html': 'HTML', '.css': 'CSS', '.scss': 'CSS',
    '.graphql': 'GraphQL', '.proto': 'Protocol Buffers', '.tf': 'Terraform',
}
EXCLUDED_DIRS = {'.atlas', '.git', '.venv', 'venv', 'node_modules', '__pycache__', 'vendor',
                 'generated', '_generated', '.next', 'dist', 'build', '.cache',
                 '.aws', '.ssh', '.secrets', '.agents', '.claude', '.codex'}
EXCLUDED_NAMES = {'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'uv.lock',
                  'poetry.lock', 'credentials.json', 'service-account.json'}
MAX_FILE_BYTES = 2_000_000
MAX_SUMMARY = 300


def short_docstring(value: str | None) -> str | None:
    if not value:
        return None
    # The first paragraph is enough context; do not reproduce whole documents.
    text = re.split(r'\n\s*\n', value.strip(), maxsplit=1)[0]
    text = re.sub(r'\s+', ' ', text).strip()
    return text if len(text) <= MAX_SUMMARY else text[:MAX_SUMMARY - 1].rsplit(' ', 1)[0] + '…'


def safe_source(root: Path, relative: str) -> Path | None:
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        return None
    candidate = root / path
    try:
        # Never follow repository symlinks, even ones pointing back into the repo.
        if any(p.is_symlink() for p in [candidate, *candidate.parents] if p != root and root in p.parents):
            return None
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
            return None
        return resolved
    except (OSError, RuntimeError):
        return None


def source_files(root: Path, excluded: Counter | None = None):
    excluded = excluded if excluded is not None else Counter()
    try:
        result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached',
                                 '--others', '--exclude-standard'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ValueError('Choose a local Git repository; Atlas uses Git’s ignore rules when indexing.') from exc
    for relative in sorted(set(result.stdout.decode('utf-8', errors='surrogateescape').split('\0'))):
        path = Path(relative)
        if not relative:
            continue
        if any(p in EXCLUDED_DIRS for p in path.parts):
            excluded['generated, vendor, build, and tool directories'] += 1
            continue
        if path.name in EXCLUDED_NAMES or path.name.startswith('.env'):
            excluded['lockfiles and secret-named files'] += 1
            continue
        if re.search(r'(^|[._-])(secret|secrets|credential|credentials|private[-_]?key)([._-]|$)', path.name, re.I):
            excluded['lockfiles and secret-named files'] += 1
            continue
        if path.suffix not in EXTENSIONS:
            excluded['unsupported file types'] += 1
            continue
        candidate = safe_source(root, relative)
        if candidate is not None:
            yield relative, candidate
        else:
            excluded['symlinks and missing files'] += 1


def make_node(node_id, name, kind, parent_id=None, path=None, line=None, end_line=None,
              summary=None, summary_source='facts', language=None):
    return {'id': node_id, 'name': name, 'kind': kind, 'parentId': parent_id,
            'path': path, 'line': line, 'endLine': end_line, 'summary': summary or '',
            'summarySource': summary_source, 'language': language,
            'metrics': {'files': 0, 'symbols': 0, 'lines': 0, 'children': 0, 'imports': 0, 'importedBy': 0}}


def module_name(path: str) -> str:
    parts = Path(path).with_suffix('').parts
    if parts[0] == 'src':
        parts = parts[1:]
    if parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join(parts)


class PythonScanner(ast.NodeVisitor):
    def __init__(self, file_id, path, nodes, bindings, scope_parents, imports, references):
        self.file_id, self.path, self.nodes = file_id, path, nodes
        self.bindings, self.scope_parents = bindings, scope_parents
        self.imports, self.references = imports, references
        self.scope, self.qual, self.classes = file_id, [], []
        self.counts = Counter()

    def bind(self, name, value):
        key = (self.scope, name)
        old = self.bindings.get(key)
        self.bindings[key] = value if old is None or old == value else ('unknown',)

    def symbol(self, node, kind):
        qualified = '.'.join([*self.qual, node.name])
        base = f'{kind}:{self.path}:{qualified}'
        self.counts[base] += 1
        node_id = base if self.counts[base] == 1 else f'{base}#{self.counts[base]}'
        doc = short_docstring(ast.get_docstring(node))
        if not doc:
            if kind == 'class':
                methods = sum(isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) for x in node.body)
                summary = f'Defines {node.name}, with {methods:,} method' + ('' if methods == 1 else 's') + '.'
            else:
                args = [a.arg for a in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                        if a.arg not in {'self', 'cls'}]
                if len(args) <= 4 and args:
                    summary = f'{node.name.replace("_", " ").capitalize()}. Takes ' + ', '.join(args) + '.'
                elif args:
                    summary = f'{node.name.replace("_", " ").capitalize()}. Takes {len(args)} named inputs.'
                else:
                    summary = f'{node.name.replace("_", " ").capitalize()}. Takes no named inputs.'
        else:
            summary = doc
        self.nodes[node_id] = make_node(node_id, node.name, kind, self.scope, self.path,
                                       node.lineno, node.end_lineno, summary,
                                       'docstring' if doc else 'facts', 'Python')
        self.nodes[node_id]['metrics']['symbols'] = 1
        self.nodes[node_id]['metrics']['lines'] = node.end_lineno - node.lineno + 1
        self.bind(node.name, ('symbol', node_id))
        self.scope_parents[node_id] = self.scope
        if kind == 'class':
            for base_expr in node.bases:
                base_target = base_expr.value if isinstance(base_expr, ast.Subscript) else base_expr
                self.references.append((node_id, self.scope, self.path, base_expr.lineno, 'inherits', self.expr(base_target)))
        # Defaults and decorators execute in the enclosing scope.
        for decorator in node.decorator_list:
            self.visit(decorator)
        if kind == 'function':
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                if default is not None:
                    self.visit(default)
        previous = self.scope
        self.scope = node_id
        self.qual.append(node.name)
        if kind == 'class':
            self.classes.append(node_id)
        else:
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                self.bind(arg.arg, ('unknown',))
            if node.args.vararg:
                self.bind(node.args.vararg.arg, ('unknown',))
            if node.args.kwarg:
                self.bind(node.args.kwarg.arg, ('unknown',))
            static_method = any(isinstance(d, ast.Name) and d.id == 'staticmethod' for d in node.decorator_list)
            if not static_method and self.nodes[previous]['kind'] == 'class' and node.args.args and node.args.args[0].arg in {'self', 'cls'}:
                self.bindings[(self.scope, node.args.args[0].arg)] = ('symbol', self.classes[-1])
        for child in node.body:
            self.visit(child)
        if kind == 'class':
            self.classes.pop()
        self.qual.pop()
        self.scope = previous

    def visit_ClassDef(self, node):
        self.symbol(node, 'class')

    def visit_FunctionDef(self, node):
        self.symbol(node, 'function')

    visit_AsyncFunctionDef = visit_FunctionDef

    @staticmethod
    def expr(node):
        if isinstance(node, ast.Name):
            return (node.id,)
        if isinstance(node, ast.Attribute):
            head = PythonScanner.expr(node.value)
            return (*head, node.attr) if head else None
        return None

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append((self.scope, self.path, node.lineno, alias.name, None))
            self.bind(alias.asname or alias.name.split('.')[0], ('module', alias.name if alias.asname else alias.name.split('.')[0]))

    def visit_ImportFrom(self, node):
        current = module_name(self.path).split('.')
        package = current if Path(self.path).stem == '__init__' else current[:-1]
        if node.level:
            if node.level > len(package):
                return
            prefix = package[:len(package) - node.level + 1]
            target = '.'.join([*prefix, *([node.module] if node.module else [])])
        else:
            target = node.module or ''
        for alias in node.names:
            if alias.name == '*':
                self.imports.append((self.scope, self.path, node.lineno, target, None))
                continue
            self.imports.append((self.scope, self.path, node.lineno, target, alias.name))
            self.bind(alias.asname or alias.name, ('import', target, alias.name))

    def visit_Call(self, node):
        expression = self.expr(node.func)
        if expression:
            self.references.append((self.scope, self.scope, self.path, node.lineno, 'calls', expression))
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id, ('unknown',))

    def visit_Global(self, node):
        for name in node.names:
            if self.scope != self.file_id:
                self.bindings[(self.scope, name)] = ('global', name)

    def visit_Nonlocal(self, node):
        # Nonlocal writes and later rebinding need data-flow analysis; do not guess.
        for name in node.names:
            self.bindings[(self.scope, name)] = ('unknown',)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bind(node.name, ('unknown',))
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        if node.name:
            self.bind(node.name, ('unknown',))
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        if node.rest:
            self.bind(node.rest, ('unknown',))
        self.generic_visit(node)

    def visit_Lambda(self, node):
        # Anonymous scopes cannot safely borrow their outer scope's argument bindings.
        return

    def visit_ListComp(self, node):
        return

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


def index_repository(root: Path | str, db_path: Path | str = DEFAULT_DB, progress=None) -> dict:
    root, db_path = Path(root).resolve(), Path(db_path).resolve()
    if not root.is_dir():
        raise ValueError(f'Repository does not exist: {root}')
    def report(phase, message, **values):
        if progress:
            progress({'phase': phase, 'message': message, **values})
    report('scanning', 'Finding source files using this repository’s ignore rules.', files=0)
    nodes = {'root': make_node('root', root.name, 'repository', path='')}
    bindings, scope_parents, modules = {}, {}, {}
    imports, references, warnings = [], [], []
    languages, excluded = Counter(), Counter()
    parsed_languages = Counter()
    js_pending, js_imports, parser_notices = [], [], set()
    js_bytes = 0
    parse_failures = 0

    def flush_javascript():
        nonlocal js_bytes, parse_failures
        if not js_pending:
            return
        records, notices = scan_batch(js_pending)
        for notice in notices:
            if notice.startswith('Could not parse '):
                warnings.append(notice)
                parse_failures += 1
            else:
                parser_notices.add(notice)
        for record in records:
            if record.get('error'):
                continue
            language = EXTENSIONS[Path(record['path']).suffix]
            parsed_languages[language] += 1
            for symbol in record['symbols']:
                node = make_node(symbol['id'], symbol['name'], symbol['kind'], symbol['parentId'], symbol['path'],
                                 symbol['line'], symbol['endLine'], symbol['summary'],
                                 'documentation' if symbol['summary'] else 'facts', language)
                node['summary'] = symbol['summary'] or f"Defines {symbol['name']} in {language}."
                node['metrics'].update(symbols=1, lines=symbol['endLine'] - symbol['line'] + 1)
                nodes[node['id']] = node
            js_imports.extend((record['path'], item['specifier'], item['line']) for item in record['imports'])
        js_pending.clear()
        js_bytes = 0
    for relative, candidate in source_files(root, excluded):
        try:
            if candidate.stat().st_size > MAX_FILE_BYTES:
                excluded['files over 2 MB'] += 1
                continue
            raw = candidate.read_bytes()
            if b'\0' in raw:
                excluded['binary files'] += 1
                continue
            source = raw.decode('utf-8')
        except (OSError, UnicodeError):
            excluded['unreadable or non-UTF-8 files'] += 1
            continue
        parent = 'root'
        parts = Path(relative).parts
        for index in range(1, len(parts)):
            directory = '/'.join(parts[:index])
            directory_id = f'dir:{directory}'
            if directory_id not in nodes:
                nodes[directory_id] = make_node(directory_id, parts[index - 1], 'directory', parent, directory)
            parent = directory_id
        language = EXTENSIONS[Path(relative).suffix]
        languages[language] += 1
        if sum(languages.values()) % 200 == 0:
            report('parsing', f'Read {sum(languages.values()):,} source files.', files=sum(languages.values()))
        file_id = f'file:{relative}'
        lines = len(source.splitlines())
        nodes[file_id] = make_node(file_id, parts[-1], 'file', parent, relative, 1, lines, language=language)
        nodes[file_id]['metrics'].update(files=1, lines=lines)
        if Path(relative).suffix in JAVASCRIPT_SUFFIXES:
            js_pending.append({'path': relative, 'source': source})
            js_bytes += len(raw)
            if len(js_pending) >= 100 or js_bytes >= 4_000_000:
                flush_javascript()
            continue
        if Path(relative).suffix not in {'.py', '.pyi'}:
            nodes[file_id]['summary'] = f'{language} file, {lines:,} lines.'
            continue
        module = module_name(relative)
        if module not in modules or Path(relative).suffix == '.py':
            modules[module] = file_id
        try:
            tree = ast.parse(source, filename=relative)
            doc = short_docstring(ast.get_docstring(tree))
            nodes[file_id]['summary'] = doc or ''
            nodes[file_id]['summarySource'] = 'docstring' if doc else 'facts'
            PythonScanner(file_id, relative, nodes, bindings, scope_parents, imports, references).visit(tree)
            parsed_languages[language] += 1
        except (SyntaxError, ValueError, RecursionError) as exc:
            parse_failures += 1
            warnings.append(f'Could not parse {relative}' + (f' at line {exc.lineno}.' if isinstance(exc, SyntaxError) else '.'))
        if languages[language] % 5000 == 0:
            print(f'Indexed {sum(languages.values()):,} files ({len(nodes):,} places and symbols)…', flush=True)

    flush_javascript()
    warnings.extend(sorted(parser_notices))
    report('linking', 'Following imports, calls, and class relationships.', files=sum(languages.values()))
    # Resolve only statically known names; unknown/reassigned bindings stop lookup.
    def binding(scope, name):
        original = scope
        while scope:
            value = bindings.get((scope, name))
            if value is not None:
                if value[0] == 'global':
                    return bindings.get((f"file:{nodes[scope]['path']}", name))
                return value
            next_scope = scope_parents.get(scope)
            # Methods do not implicitly close over a class namespace.
            if next_scope and nodes[next_scope]['kind'] == 'class' and nodes[original]['kind'] in {'function', 'class'}:
                next_scope = scope_parents.get(next_scope)
            scope = next_scope
        return None

    def resolve(value, tail=(), visited=None):
        if not value or value[0] == 'unknown':
            return None
        visited = set() if visited is None else visited
        key = (value, tail)
        if key in visited or len(visited) > 24:
            return None
        visited.add(key)
        if value[0] == 'symbol':
            target = value[1]
            if not tail:
                return target
            if nodes[target]['kind'] != 'class':
                return None
            child = bindings.get((target, tail[0]))
            return resolve(child, tail[1:], visited)
        if value[0] == 'module':
            fullname = '.'.join([value[1], *tail])
            if fullname in modules:
                return modules[fullname]
            for index in range(len(tail), -1, -1):
                module = '.'.join([value[1], *tail[:index]])
                if module in modules and index < len(tail):
                    child = bindings.get((modules[module], tail[index]))
                    return resolve(child, tail[index + 1:], visited)
            return None
        if value[0] == 'import':
            base, member = value[1:]
            submodule = f'{base}.{member}' if base else member
            if submodule in modules:
                return resolve(('module', submodule), tail, visited)
            if base in modules:
                return resolve(bindings.get((modules[base], member)), tail, visited)
        return None

    edges = {}
    def edge(source, target, kind, path, line):
        if source == target or source not in nodes or target not in nodes:
            return
        key = (source, target, kind)
        evidence = edges.setdefault(key, set())
        evidence.add((path, line))

    unresolved_imports = 0
    indexed_paths = {node['path'] for node in nodes.values() if node['kind'] == 'file'}
    for path, specifier, line in js_imports:
        target = resolve_import(path, specifier, indexed_paths)
        if target:
            edge('file:' + path, 'file:' + target, 'imports', path, line)
        else:
            unresolved_imports += 1
    for source_id, path, line, module, member in imports:
        submodule = f'{module}.{member}' if module and member else member
        target = modules.get(submodule) if submodule else None
        target = target or modules.get(module)
        if target:
            edge(source_id, target, 'imports', path, line)
        else:
            unresolved_imports += 1
    for source_id, scope, path, line, kind, expression in references:
        if not expression:
            continue
        target = resolve(binding(scope, expression[0]), expression[1:])
        if target and nodes[target]['kind'] in {'function', 'class'}:
            if kind != 'inherits' or nodes[target]['kind'] == 'class':
                edge(source_id, target, kind, path, line)

    report('grouping', 'Connecting files and folders.', files=sum(languages.values()))
    # Children and totals are accumulated bottom-up without counting symbol spans twice.
    order = list(nodes)
    for node_id in reversed(order):
        node = nodes[node_id]
        parent = nodes.get(node['parentId'])
        if parent:
            parent['metrics']['children'] += 1
            parent['metrics']['symbols'] += node['metrics']['symbols']
            if node['kind'] in {'directory', 'file'}:
                parent['metrics']['files'] += node['metrics']['files']
                parent['metrics']['lines'] += node['metrics']['lines']
        if not node['summary']:
            m = node['metrics']
            if node['kind'] in {'directory', 'repository'}:
                node['summary'] = f'{m["files"]:,} files and {m["symbols"]:,} functions and classes live here.'
            else:
                node['summary'] = f'{m["symbols"]:,} functions and classes in {m["lines"]:,} lines of {node["language"]}.'

    ancestor_cache = {}
    def ancestors(node_id):
        if node_id not in ancestor_cache:
            current, chain = node_id, []
            while current:
                chain.append(current)
                current = nodes[current]['parentId']
            ancestor_cache[node_id] = tuple(reversed(chain))
        return ancestor_cache[node_id]

    aggregate = Counter()
    boundary = {}
    for (source_id, target, kind), evidence in edges.items():
        left, right = ancestors(source_id), ancestors(target)
        if kind == 'imports':
            for n in left:
                nodes[n]['metrics']['imports'] += 1
            for n in right:
                nodes[n]['metrics']['importedBy'] += 1
        offset = 0
        while offset < min(len(left), len(right)) and left[offset] == right[offset]:
            offset += 1
        if 0 < offset < min(len(left), len(right)):
            aggregate[(left[offset - 1], left[offset], right[offset], kind)] += len(evidence)
            for chain, other_chain, direction in [(left, right, 'outgoing'), (right, left, 'incoming')]:
                for selected in chain[offset:]:
                    if nodes[selected]['kind'] not in {'directory', 'file'}:
                        continue
                    counterpart = other_chain[offset] if nodes[selected]['kind'] == 'directory' else other_chain[-1]
                    key = (selected, direction, counterpart, kind)
                    count, samples = boundary.get(key, (0, []))
                    samples = sorted(set(samples) | evidence)[:3]
                    boundary[key] = (count + len(evidence), samples)

    for reason, count in excluded.items():
        warnings.append(f'Skipped {count:,} {reason}.')
    warnings.append('Python symbols, imports, direct calls, and inheritance are parsed. JavaScript and TypeScript include named definitions and local relative imports; their calls, package aliases, and CommonJS require calls are not resolved. Other languages appear as files.')
    warnings.append('Dynamic calls, wildcard-import targets, and external packages are not linked. Ignored, generated, vendor, secret-named, and binary files are excluded.')
    stats = {'files': sum(languages.values()), 'symbols': sum(n['kind'] in {'function', 'class'} for n in nodes.values()),
             'nodes': len(nodes), 'edges': len(edges) + len(nodes) - 1, 'lines': nodes['root']['metrics']['lines'],
             'semanticEdges': len(edges)}
    status = {'name': root.name, 'root': str(root), 'indexedAt': datetime.now(timezone.utc).isoformat(),
              'stats': stats, 'languages': [{'name': name, 'files': count} for name, count in languages.most_common()],
              'warnings': warnings[:100], 'parseWarnings': parse_failures,
              'languageAnalysis': [{'name': name, 'files': count, 'parsedFiles': parsed_languages[name],
                                    'mode': 'semantic' if name == 'Python' else 'imports-and-symbols' if parsed_languages[name] else 'file-only'}
                                   for name, count in languages.most_common()],
              'coverage': {'parsedLanguage': 'Python', 'excludedFiles': sum(excluded.values()),
                           'parsedLanguages': list(parsed_languages),
                           'exclusions': dict(excluded), 'unresolvedOrExternalImports': unresolved_imports,
                           'importRoots': ['.', 'src']}}
    db_path.parent.mkdir(parents=True, exist_ok=True)
    report('saving', 'Saving the local graph.', files=sum(languages.values()))
    temporary = db_path.with_suffix('.building.db')
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    connection.executescript('''
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE nodes (id TEXT PRIMARY KEY, parent TEXT, name TEXT, kind TEXT, path TEXT, rank INTEGER, data TEXT NOT NULL);
        CREATE TABLE edges (source TEXT, target TEXT, kind TEXT, weight INTEGER, evidence TEXT, PRIMARY KEY(source,target,kind));
        CREATE TABLE aggregate_edges (parent TEXT, source TEXT, target TEXT, kind TEXT, weight INTEGER, PRIMARY KEY(parent,source,target,kind));
        CREATE TABLE boundary_edges (parent TEXT, direction TEXT, node TEXT, kind TEXT, weight INTEGER, evidence TEXT, PRIMARY KEY(parent,direction,node,kind));
    ''')
    connection.execute('INSERT INTO metadata VALUES (?,?)', ('status', json.dumps(status)))
    connection.executemany('INSERT INTO nodes VALUES (?,?,?,?,?,?,?)',
                           ((n['id'], n['parentId'], n['name'], n['kind'], n['path'],
                             n['metrics']['files'] * 10 + n['metrics']['symbols'] + n['metrics']['importedBy'],
                             json.dumps(n)) for n in nodes.values()))
    connection.executemany('INSERT INTO edges VALUES (?,?,?,?,?)',
                           ((s, t, k, len(e), json.dumps([{'path': p, 'line': l} for p, l in sorted(e)]))
                            for (s, t, k), e in edges.items()))
    connection.executemany('INSERT INTO edges VALUES (?,?,?,?,?)',
                           ((n['parentId'], n['id'], 'contains', 1, '[]') for n in nodes.values() if n['parentId']))
    connection.executemany('INSERT INTO aggregate_edges VALUES (?,?,?,?,?)', ((*key, weight) for key, weight in aggregate.items()))
    connection.executemany('INSERT INTO boundary_edges VALUES (?,?,?,?,?,?)',
                           ((*key, count, json.dumps([{'path': p, 'line': l} for p, l in samples]))
                            for key, (count, samples) in boundary.items()))
    connection.executescript('''
        CREATE INDEX nodes_parent ON nodes(parent,rank DESC,name);
        CREATE INDEX nodes_name ON nodes(name COLLATE NOCASE);
        CREATE INDEX edges_target ON edges(target);
        CREATE INDEX aggregate_parent ON aggregate_edges(parent);
    ''')
    connection.commit()
    connection.close()
    temporary.replace(db_path)
    return status
