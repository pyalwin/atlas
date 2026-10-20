"""Commit-pinned, read-only review scopes; no project checkout is modified."""
from __future__ import annotations

import ast
import copy
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from pathlib import Path, PurePosixPath

from .indexer import EXCLUDED_DIRS, EXCLUDED_NAMES, EXTENSIONS, MAX_FILE_BYTES, index_repository, safe_source

MAX_FILES = 500
MAX_NODES = 300
MAX_EDGES = 1000
MAX_SYMBOLS = 2000
MAX_DIFF_BYTES = 250_000
MAX_SNAPSHOT_BYTES = 512_000_000
VERSION = 1


def git(root, *args, timeout=60, limit=32_000_000, allowed_exit=(0,)):
    # Never use a shell, pagers, diff helpers, textconv, or repository hooks.
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0',
           'GIT_PAGER': 'cat', 'GIT_CONFIG_NOSYSTEM': '1'}
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                                     '-C', str(root), *args], stdout=output, stderr=subprocess.PIPE,
                                    env=env, timeout=timeout)
            if result.returncode not in allowed_exit:
                raise ValueError('Git could not complete this review operation. Check that the refs exist and repository history is available.')
            size = output.tell()
            if size > limit:
                raise ValueError('This Git result exceeds the review safety limit; choose a smaller comparison.')
            output.seek(0)
            return output.read()
    except subprocess.TimeoutExpired as exc:
        raise ValueError('The Git operation timed out. Try again or choose local refs.') from exc


def valid_ref(value):
    if not isinstance(value, str) or not value or len(value) > 250 or value.startswith('-') or any(c.isspace() or ord(c) < 32 for c in value) or any(c in value for c in '\\:\x00'):
        raise ValueError('Choose a valid Git branch, tag, or commit.')
    return value


def commit(root, ref):
    ref = valid_ref(ref)
    value = git(root, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()
    if not re.fullmatch(r'[0-9a-f]{40,64}', value):
        raise ValueError('The selected ref is not a commit.')
    return value


def relative_path(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value or '\\' in value:
        raise ValueError('Choose a repository-relative file path.')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '.' in path.parts or value != str(path):
        raise ValueError('Choose a repository-relative file path.')
    return value


def excluded_reason(path):
    path = PurePosixPath(path)
    if any(p in EXCLUDED_DIRS for p in path.parts):
        return 'Generated, vendor, build, or tool directory.'
    if path.name in EXCLUDED_NAMES or path.name.startswith('.env') or re.search(r'(^|[._-])(secret|secrets|credential|credentials|private[-_]?key)([._-]|$)', path.name, re.I):
        return 'Excluded by secret-name or lockfile protection.'
    if path.suffix not in EXTENSIONS:
        return 'This file type is not indexed or previewed.'
    return None


def github_origin(root):
    try:
        origin = git(root, 'remote', 'get-url', 'origin').decode().strip()
    except ValueError:
        return None
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?', origin)
    return match.group(1) if match else None


def parse_pr(value, repository):
    if not repository:
        raise ValueError('PR import requires this project’s origin to be a github.com repository. You can compare local refs instead.')
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise ValueError('Enter a GitHub PR URL or number.')
    value = value.strip()
    if re.fullmatch(r'#?[1-9]\d{0,8}', value):
        return int(value.lstrip('#'))
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9]\d{0,8})/?(?:[?#].*)?', value)
    if not match or match.group(1).lower() != repository.lower():
        raise ValueError('Use a GitHub PR belonging to this project’s origin repository.')
    return int(match.group(2))


def pr_metadata(repository, number):
    request = urllib.request.Request(f'https://api.github.com/repos/{repository}/pulls/{number}',
                                     headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'Atlas-local-review'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError('The PR metadata response is too large.')
        return json.loads(raw)
    except (urllib.error.URLError, TimeoutError):
        # gh supports private origins and authenticated API rate limits, if installed.
        try:
            response = subprocess.run(['gh', 'api', f'repos/{repository}/pulls/{number}'],
                                      capture_output=True, timeout=30, check=True)
            if len(response.stdout) > 1_000_000:
                raise ValueError('The PR metadata response is too large.')
            return json.loads(response.stdout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('Could not load this PR from GitHub. Check the PR number and network access, or compare local refs.') from exc


def tree(root, sha):
    records = {}
    for record in git(root, 'ls-tree', '-rlz', sha).split(b'\0'):
        if not record:
            continue
        info, raw_path = record.split(b'\t', 1)
        mode, kind, oid, size = info.decode().split()
        path = raw_path.decode('utf-8', errors='replace')
        try:
            relative_path(path)
        except ValueError:
            continue
        records[path] = {'mode': mode, 'type': kind, 'oid': oid, 'size': int(size) if size != '-' else 0}
    return records


def file_reason(path, info):
    return excluded_reason(path) or ('Symlinks and submodules are not previewed.' if info and info['mode'] not in {'100644', '100755'} else None) or ('File exceeds the 2 MB preview limit.' if info and info['size'] > MAX_FILE_BYTES else None)


def parse_hunks(diff):
    hunks, current = [], None
    old_line = new_line = 0
    for line in diff.splitlines():
        match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)', line)
        if match:
            old_line, new_line = int(match[1]), int(match[3])
            current = {'oldStart': old_line, 'oldLines': int(match[2] or 1),
                       'newStart': new_line, 'newLines': int(match[4] or 1),
                       'heading': match[5].strip(), 'lines': []}
            hunks.append(current)
        elif current is not None and line[:1] in {'+', '-', ' '}:
            kind = {'+': 'added', '-': 'removed', ' ': 'context'}[line[0]]
            current['lines'].append({'kind': kind, 'text': line[1:][:4000],
                                     'oldLine': None if kind == 'added' else old_line,
                                     'newLine': None if kind == 'removed' else new_line})
            old_line += kind != 'added'
            new_line += kind != 'removed'
    return hunks


class ReviewStore:
    def __init__(self, root, directory):
        self.root, self.directory = Path(root), Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.worker = None
        self.closed = threading.Event()
        self.state = {'job': None, 'review': None}
        path = self.directory / 'latest.json'
        if path.exists():
            try:
                saved = json.loads(path.read_text())
                if saved.get('version') == VERSION:
                    self.state = saved['state']
            except (OSError, ValueError, KeyError):
                pass

    def options(self):
        try:
            head = commit(self.root, 'HEAD')
        except ValueError:
            return {'available': False, 'base': '', 'head': 'HEAD', 'branches': [],
                    'repository': github_origin(self.root), 'error': 'Commit at least one change before starting a review.'}
        refs = git(self.root, 'for-each-ref', '--format=%(refname:short)', 'refs/heads/', 'refs/remotes/', 'refs/tags/').decode().splitlines()
        refs = [r for r in refs if not r.endswith('/HEAD')][:300]
        default = next((r for r in ('origin/main', 'origin/master', 'main', 'master') if r in refs and commit(self.root, r) != head), None)
        if default is None:
            try:
                commit(self.root, 'HEAD~1')
                default = 'HEAD~1'
            except ValueError:
                default = 'HEAD'
        repository = github_origin(self.root)
        return {'available': True, 'base': default, 'head': 'HEAD', 'branches': refs,
                'repository': repository, 'origin': f'https://github.com/{repository}' if repository else None}

    def get(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def start(self, body):
        if set(body) == {'pr'}:
            parse_pr(body['pr'], github_origin(self.root))
        elif set(body) == {'base', 'head'}:
            valid_ref(body['base'])
            valid_ref(body['head'])
        else:
            raise ValueError('Submit a PR URL/number, or both base and head refs.')
        with self.lock:
            if self.closed.is_set():
                raise ValueError('This review server is shutting down.')
            if self.worker and self.worker.is_alive():
                return self.get()
            identity = uuid.uuid4().hex[:16]
            self.state['job'] = {'id': identity, 'state': 'queued', 'phase': 'resolving', 'message': 'Resolving immutable Git commits.'}
            self.worker = threading.Thread(target=self._run, args=(identity, copy.deepcopy(body)), daemon=True)
            self.worker.start()
            return self.get()

    def _progress(self, phase, message):
        if self.closed.is_set():
            raise ValueError('Review interrupted because the server is shutting down.')
        with self.lock:
            self.state['job'].update(state='running', phase=phase, message=message)

    def close(self):
        self.closed.set()
        if self.worker:
            self.worker.join(timeout=2)

    def _run(self, identity, body):
        try:
            result = self.build(identity, body)
            with self.lock:
                self.state = {'job': {'id': identity, 'state': 'complete', 'phase': 'complete', 'message': 'Pinned review scope is ready.'}, 'review': result}
                temporary = self.directory / 'latest.tmp'
                temporary.write_text(json.dumps({'version': VERSION, 'state': self.state}))
                temporary.replace(self.directory / 'latest.json')
        except Exception as exc:
            with self.lock:
                error = str(exc) if isinstance(exc, ValueError) else 'Atlas could not build this review. Check local repository access and available disk space.'
                self.state['job'].update(state='error', phase='error', message=error, error=error)

    def _resolve(self, body):
        if 'pr' not in body:
            return {'baseRef': body['base'], 'headRef': body['head'], 'baseTip': commit(self.root, body['base']),
                    'headSha': commit(self.root, body['head']), 'title': f"{body['base']} → {body['head']}", 'url': None}
        repository = github_origin(self.root)
        number = parse_pr(body['pr'], repository)
        self._progress('fetching', f'Loading GitHub PR #{number} and fetching its refs from origin.')
        metadata = pr_metadata(repository, number)
        base_ref = metadata.get('base', {}).get('ref')
        valid_ref(base_ref)
        git(self.root, 'check-ref-format', 'refs/heads/' + base_ref)
        base_dest, head_dest = f'refs/atlas/review/pr-{number}-base', f'refs/atlas/review/pr-{number}-head'
        depth = ['--depth=200'] if git(self.root, 'rev-parse', '--is-shallow-repository').strip() == b'true' else []
        git(self.root, '-c', 'protocol.ext.allow=never', '-c', 'protocol.file.allow=never',
            'fetch', '--no-tags', '--no-write-fetch-head', *depth, 'origin',
            f'+refs/heads/{base_ref}:{base_dest}', f'+refs/pull/{number}/head:{head_dest}', timeout=120)
        return {'baseRef': base_ref, 'headRef': f'PR #{number}', 'baseTip': commit(self.root, base_dest),
                'headSha': commit(self.root, head_dest), 'title': str(metadata.get('title') or f'PR #{number}')[:300],
                'url': f'https://github.com/{repository}/pull/{number}', 'number': number}

    def snapshot(self, sha, side):
        directory = self.directory / 'snapshots' / sha
        source, db = directory / 'source', directory / 'atlas.db'
        if db.exists():
            return source, db, tree(self.root, sha)
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        records = tree(self.root, sha)
        eligible = {p: info for p, info in records.items() if not file_reason(p, info)}
        if len(eligible) > 100_000 or sum(info['size'] for info in eligible.values()) > MAX_SNAPSHOT_BYTES:
            raise ValueError('This repository snapshot exceeds the 512 MB / 100,000 source-file review limit.')
        self._progress('snapshot', f'Preparing the exact {side} snapshot ({sha[:8]}).')
        # A shallow clone can omit commits reachable only through our PR refs,
        # even with --shared. An empty private index with an explicit object
        # alternate can read every pinned tree without fetching or checking out.
        git(self.root, 'init', '--quiet', '--template=', str(source))
        objects = git(self.root, 'rev-parse', '--path-format=absolute', '--git-path', 'objects').decode().strip()
        if '\n' in objects or '\r' in objects:
            raise ValueError('Review snapshots require a repository path without line breaks.')
        (source / '.git/objects/info/alternates').write_text(objects + '\n')
        git(source, 'config', 'core.fsmonitor', 'false')
        git(source, 'read-tree', sha)
        # Materialize regular, permitted blobs only. Never extract a tar or follow a symlink.
        process = subprocess.Popen(['git', '-C', str(self.root), 'cat-file', '--batch'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            for path, info in eligible.items():
                process.stdin.write((info['oid'] + '\n').encode())
                process.stdin.flush()
                header = process.stdout.readline().decode().strip().split()
                if len(header) != 3 or header[1] != 'blob' or int(header[2]) != info['size']:
                    raise ValueError('A pinned Git blob could not be read.')
                raw = process.stdout.read(info['size'])
                if len(raw) != info['size'] or process.stdout.read(1) != b'\n':
                    raise ValueError('A pinned Git blob was incomplete.')
                if b'\0' in raw:
                    continue
                try:
                    raw.decode('utf-8')
                except UnicodeError:
                    continue
                target = source / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
        finally:
            process.stdin.close()
            process.stdout.close()
            process.wait(timeout=10)
        index_repository(source, db, progress=lambda event: self._progress('indexing', f"Indexing {side}: {event['message']}"))
        return source, db, records

    def _changes(self, base, head):
        tokens = git(self.root, 'diff', '--no-ext-diff', '--no-textconv', '--name-status', '-z', '-M', base, head, '--').split(b'\0')
        files, i = [], 0
        while i < len(tokens) and tokens[i]:
            status = tokens[i].decode()
            i += 1
            old = tokens[i].decode('utf-8', errors='replace')
            i += 1
            path = old
            if status[:1] in {'R', 'C'}:
                path = tokens[i].decode('utf-8', errors='replace')
                i += 1
            try:
                relative_path(path)
                relative_path(old)
            except ValueError:
                continue
            files.append({'path': path, 'oldPath': old if old != path or status == 'D' else None,
                          'status': {'A': 'added', 'D': 'deleted', 'R': 'renamed', 'C': 'added'}.get(status[0], 'modified'),
                          'additions': 0, 'deletions': 0, 'binary': False, 'excluded': None, 'changedSymbols': []})
        stats = git(self.root, 'diff', '--no-ext-diff', '--no-textconv', '--numstat', '-z', '-M', base, head, '--').split(b'\0')
        counts, i = {}, 0
        while i < len(stats) and stats[i]:
            added, removed, path = stats[i].split(b'\t', 2)
            i += 1
            if not path:
                i += 1  # old path of a rename
                path = stats[i]
                i += 1
            counts[path.decode('utf-8', errors='replace')] = (int(added) if added != b'-' else 0, int(removed) if removed != b'-' else 0, added == b'-')
        for file in files:
            file['additions'], file['deletions'], file['binary'] = counts.get(file['path'], (0, 0, False))
        return files

    def _diff(self, base, head, file):
        if file['excluded'] or file['binary']:
            return '', [], False
        old_path = file['oldPath'] or file['path']
        base_file = self.directory / 'snapshots' / base / 'source' / old_path
        head_file = self.directory / 'snapshots' / head / 'source' / file['path']
        # Literal pathspecs still recurse into directories. Comparing these exact
        # snapshot files prevents a renamed path, now a directory, leaking its diff.
        left = str(base_file) if base_file.is_file() else '/dev/null'
        right = str(head_file) if head_file.is_file() else '/dev/null'
        raw = git(self.root, 'diff', '--no-index', '--no-ext-diff', '--no-textconv', '--no-color',
                  '--unified=3', '--', left, right, limit=12_000_000, allowed_exit=(0, 1))
        # Only hunk text is needed; do not expose snapshot storage paths in headers.
        lines = raw.splitlines(keepends=True)
        offset = next((i for i, line in enumerate(lines) if line.startswith(b'@@ ')), len(lines))
        raw = ((f'--- {"a/" + old_path if left != "/dev/null" else "/dev/null"}\n'
                f'+++ {"b/" + file["path"] if right != "/dev/null" else "/dev/null"}\n').encode()
               + b''.join(lines[offset:])) if offset < len(lines) else b''
        truncated = len(raw) > MAX_DIFF_BYTES
        if truncated:
            raw = raw[:MAX_DIFF_BYTES].rsplit(b'\n', 1)[0]
        diff = raw.decode('utf-8', errors='replace')
        truncated |= any(len(line) > 4001 for line in diff.splitlines())
        return diff, parse_hunks(diff), truncated

    @staticmethod
    def _load(db):
        with closing(sqlite3.connect(db)) as connection:
            nodes = {row[0]: json.loads(row[1]) for row in connection.execute('SELECT id,data FROM nodes')}
            edges = {(s, t, k): {'weight': w, 'evidence': json.loads(e)} for s, t, k, w, e in
                     connection.execute("SELECT source,target,kind,weight,evidence FROM edges WHERE kind != 'contains'")}
            status = json.loads(connection.execute("SELECT value FROM metadata WHERE key='status'").fetchone()[0])
        return nodes, edges, status

    @staticmethod
    def _symbols(file, hunks, base_nodes, head_nodes, base_source, head_source):
        changed = {'base': {line['oldLine'] for h in hunks for line in h['lines'] if line['kind'] == 'removed'},
                   'head': {line['newLine'] for h in hunks for line in h['lines'] if line['kind'] == 'added'}}
        matched, present = {}, {}
        for side, nodes, source in [('base', base_nodes, base_source), ('head', head_nodes, head_source)]:
            path = (file['oldPath'] or file['path']) if side == 'base' else file['path']
            symbols = [node for node in nodes.values() if node['path'] == path and node['kind'] in {'function', 'class'}]
            starts = {}
            if path.endswith(('.py', '.pyi')) and symbols:
                try:
                    for node in ast.walk(ast.parse((source / path).read_text())):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.decorator_list:
                            starts[node.lineno] = min(d.lineno for d in node.decorator_list)
                except (OSError, SyntaxError, ValueError, RecursionError):
                    pass
            for node in symbols:
                key = node['id'].replace(path, file['path'], 1)
                present.setdefault(key, {})[side] = node
                if any(starts.get(node['line'], node['line']) <= line <= node['endLine'] for line in changed[side]):
                    matched[key] = True
        result = []
        for key in matched:
            pair = present[key]
            side = 'head' if 'head' in pair else 'base'
            node = pair[side]
            result.append({k: node[k] for k in ('id', 'name', 'kind', 'line', 'endLine')} |
                          {'side': side, 'change': 'modified' if len(pair) == 2 else 'added' if side == 'head' else 'deleted'})
        return sorted(result, key=lambda n: (n['line'], n['name']))

    def build(self, identity, body):
        refs = self._resolve(body)
        try:
            merge_base = git(self.root, 'merge-base', refs['baseTip'], refs['headSha']).decode().strip()
        except ValueError as exc:
            raise ValueError('No common ancestor is available. For a shallow repository, fetch more history for the base and head branches, then retry.') from exc
        if not re.fullmatch(r'[0-9a-f]{40,64}', merge_base):
            raise ValueError('No common ancestor was found. Fetch sufficient history and try again.')
        refs.update(baseSha=merge_base, mergeBase=merge_base)
        files = self._changes(merge_base, refs['headSha'])
        base_source, base_db, base_tree = self.snapshot(merge_base, 'base')
        head_source, head_db, head_tree = self.snapshot(refs['headSha'], 'head')
        self._progress('impact', 'Comparing changed symbols and direct static connections.')
        base_nodes, base_edges, base_status = self._load(base_db)
        head_nodes, head_edges, head_status = self._load(head_db)
        warnings = ['Impact shows direct, statically resolved references to changed files. It is not a runtime prediction or a test-coverage guarantee.',
                    'Python resolves definitions, imports, direct calls, and inheritance. JavaScript/TypeScript resolve definitions and local relative imports; other languages have file-level diffs only.',
                    'Source and evidence are pinned to the displayed commits. Uncommitted working-tree changes are outside this scope.']
        symbol_count = 0
        diff_limited = False
        for file in files[:MAX_FILES]:
            old_path = file['oldPath'] or file['path']
            file['excluded'] = file_reason(old_path, base_tree.get(old_path)) or file_reason(file['path'], head_tree.get(file['path']))
            if not file['excluded'] and not file['binary']:
                for path, info, source in [(old_path, base_tree.get(old_path), base_source), (file['path'], head_tree.get(file['path']), head_source)]:
                    if info and not (source / path).is_file():
                        file['excluded'] = 'Binary or non-UTF-8 file; source preview and semantic analysis are unavailable.'
            diff, hunks, limited = self._diff(merge_base, refs['headSha'], file)
            diff_limited |= limited
            symbols = self._symbols(file, hunks, base_nodes, head_nodes, base_source, head_source)
            symbol_count += len(symbols)
            file['changedSymbols'] = symbols[:max(0, MAX_SYMBOLS - sum(len(f['changedSymbols']) for f in files[:MAX_FILES]))]
            file['diffTruncated'] = limited
        aliases = {f['oldPath']: f['path'] for f in files if f['oldPath']}
        changed_paths = {f['path'] for f in files}
        all_nodes, grouped = {**base_nodes, **head_nodes}, {}
        # A pure rename preserves semantic identity. Keep base evidence paths
        # unchanged, but compare endpoint IDs using their corresponding head path.
        def canonical_id(identity):
            path = base_nodes[identity]['path']
            return identity.replace(path, aliases[path], 1) if path in aliases else identity
        renamed_nodes = {canonical_id(key): {**node, 'path': aliases.get(node['path'], node['path'])}
                         for key, node in base_nodes.items() if node['path']}
        base_edges = {(canonical_id(source), canonical_id(target), kind): edge
                      for (source, target, kind), edge in base_edges.items()}
        base_nodes = {**base_nodes, **renamed_nodes}
        for key in base_edges.keys() | head_edges.keys():
            source_id, target_id, kind = key
            side = 'head' if key in head_edges else 'base'
            nodes = head_nodes if side == 'head' else base_nodes
            source_path, target_path = nodes[source_id]['path'], nodes[target_id]['path']
            source_path, target_path = aliases.get(source_path, source_path), aliases.get(target_path, target_path)
            if source_path == target_path or not ({source_path, target_path} & changed_paths):
                continue
            change = 'unchanged' if key in base_edges and key in head_edges else 'added' if side == 'head' else 'removed'
            group = (source_path, target_path, kind, change)
            edge = grouped.setdefault(group, {'source': 'file:' + source_path, 'target': 'file:' + target_path, 'kind': kind,
                                              'change': change, 'weight': 0, 'evidence': []})
            evidence = (head_edges if side == 'head' else base_edges)[key]
            edge['weight'] += evidence['weight']
            edge['evidence'].extend({**item, 'side': side} for item in evidence['evidence'] if {**item, 'side': side} not in edge['evidence'])
        incoming = {s for s, t, _, _ in grouped if t in changed_paths and s not in changed_paths}
        outgoing = {t for s, t, _, _ in grouped if s in changed_paths and t not in changed_paths}
        ordered_paths = [*sorted(changed_paths), *sorted(incoming), *sorted(outgoing - incoming)]
        visible_paths = set(ordered_paths[:MAX_NODES])
        nodes = []
        statuses = {f['path']: f['status'] for f in files}
        for path in ordered_paths[:MAX_NODES]:
            original = all_nodes.get('file:' + path, {})
            role = 'changed' if path in changed_paths else 'related' if path in incoming & outgoing else 'dependent' if path in incoming else 'dependency'
            nodes.append({'id': 'file:' + path, 'path': path, 'name': PurePosixPath(path).name, 'role': role,
                          'status': statuses.get(path), 'metrics': original.get('metrics', {})})
        edges = [edge for key, edge in sorted(grouped.items(), key=lambda item: (item[0][3] == 'unchanged', item[0])) if key[0] in visible_paths and key[1] in visible_paths]
        evidence_limited = any(len(edge['evidence']) > 12 for edge in edges)
        for edge in edges:
            edge['evidenceCount'] = len(edge['evidence'])
            edge['evidence'] = edge['evidence'][:12]
        truncated = {'files': len(files) > MAX_FILES, 'nodes': len(ordered_paths) > MAX_NODES,
                     'edges': len(grouped) > min(len(edges), MAX_EDGES), 'symbols': symbol_count > MAX_SYMBOLS,
                     'diffs': diff_limited, 'evidence': evidence_limited}
        if any(truncated.values()):
            warnings.append('Review output is capped: 500 changed files, 300 graph files, 1,000 connections, 2,000 changed symbols, 12 evidence locations per connection, 250 KB per diff, and 4,000 characters per rendered line. Some details are omitted; totals describe the full comparison where available.')
        if base_status['parseWarnings'] or head_status['parseWarnings']:
            warnings.append(f"Parser warnings: {base_status['parseWarnings']} in base and {head_status['parseWarnings']} in head. Connections in those files may be incomplete.")
        if any(f['excluded'] or f['binary'] for f in files[:MAX_FILES]):
            warnings.append('Some changed files have no preview or semantic analysis because of file-type, size, binary, symlink, or secret-name exclusions.')
        result = {'id': identity, **refs, 'files': files[:MAX_FILES], 'graph': {'nodes': nodes, 'edges': edges[:MAX_EDGES]},
                  'stats': {'changedFiles': len(files), 'additions': sum(f['additions'] for f in files), 'deletions': sum(f['deletions'] for f in files),
                            'changedSymbols': symbol_count, 'dependents': len(incoming), 'dependencies': len(outgoing),
                            'addedEdges': sum(e['change'] == 'added' for e in grouped.values()),
                            'removedEdges': sum(e['change'] == 'removed' for e in grouped.values())},
                  'warnings': warnings, 'truncated': truncated}
        (self.directory / f'{identity}.json').write_text(json.dumps(result))
        return result

    def _review(self, identity=None):
        if identity:
            if not re.fullmatch(r'[0-9a-f]{16}', identity):
                raise ValueError('Choose a valid review scope.')
            path = self.directory / f'{identity}.json'
            if not path.is_file():
                raise KeyError('This review scope is not available.')
            return json.loads(path.read_text())
        with self.lock:
            if not self.state['review']:
                raise ValueError('Build a review scope first.')
            return copy.deepcopy(self.state['review'])

    def diff(self, path, identity=None):
        path = relative_path(path)
        review = self._review(identity)
        file = next((f for f in review['files'] if f['path'] == path), None)
        if file is None:
            raise KeyError('This file is not in the displayed review scope.')
        diff, hunks, truncated = self._diff(review['baseSha'], review['headSha'], file)
        return {**file, 'diff': diff, 'hunks': hunks, 'truncated': truncated,
                'baseSha': review['baseSha'], 'headSha': review['headSha']}

    def guide(self, identity=None):
        from .review_guide import get_guide
        from .review_analysis import attach_analysis, load_analysis
        review = self._review(identity)
        return attach_analysis(get_guide(self, review['id']), load_analysis(self, review))

    def analysis(self, identity):
        from .review_analysis import load_analysis
        return load_analysis(self, self._review(identity))

    def save_analysis(self, identity, raw):
        from .review_analysis import save_analysis
        return save_analysis(self, identity, raw)

    def context(self, identity=None):
        if not identity and (self.get().get('job') or {}).get('state') in {'queued', 'running'}:
            raise ValueError('Wait for the review job to complete, or select an existing pinned review ID.')
        review = self._review(identity)
        return {'reviewId': review['id'], 'baseSha': review['baseSha'], 'headSha': review['headSha'],
                'title': review.get('title'), 'stats': review['stats'],
                'files': [{key: file.get(key) for key in ('path', 'oldPath', 'status', 'additions', 'deletions', 'binary', 'excluded', 'diffTruncated')} for file in review['files']],
                'relationships': review['graph']['edges'][:150],
                'relationshipsTruncated': len(review['graph']['edges']) > 150,
                'truncated': review.get('truncated', {}), 'warnings': review.get('warnings', []),
                'analysis': self.analysis(review['id'])}

    def source(self, path, side, line=None, identity=None):
        path = relative_path(path)
        if side not in {'base', 'head'}:
            raise ValueError('Choose the base or head snapshot.')
        reason = excluded_reason(path)
        if reason:
            raise ValueError(reason)
        review = self._review(identity)
        sha = review['baseSha' if side == 'base' else 'headSha']
        root = self.directory / 'snapshots' / sha / 'source'
        candidate = safe_source(root, path)
        if candidate is None:
            raise FileNotFoundError('This file is unavailable in the selected snapshot.')
        raw = candidate.read_bytes()
        if len(raw) > MAX_FILE_BYTES or b'\0' in raw:
            raise ValueError('This file cannot be previewed.')
        lines = raw.decode('utf-8').splitlines()
        line = 1 if line is None else line
        if not isinstance(line, int) or isinstance(line, bool) or line < 1 or line > max(1, len(lines)):
            raise ValueError('Line must be a positive whole number within this snapshot file.')
        start, end = max(1, line - 7), min(len(lines), line + 15)
        selected, used, clipped_lines = [], 0, False
        for text in lines[start - 1:end]:
            clipped = text[:4000]
            clipped_lines |= len(text) > 4000
            if used + len(clipped) > 64000:
                break
            selected.append(clipped)
            used += len(clipped)
        return {'path': path, 'side': side, 'sha': sha, 'startLine': start, 'lines': selected,
                'highlightLine': line, 'truncated': clipped_lines or start > 1 or start + len(selected) - 1 < len(lines)}
