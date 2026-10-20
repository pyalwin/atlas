"""Persistent local repositories, serialized indexing, and source freshness."""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import queue
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .indexer import DEFAULT_DB, MAX_FILE_BYTES, index_repository, source_files
from .product import Taxonomy, clean_prefix, matches

MAX_MAP_BYTES = 1_000_000


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=True)
    temporary.replace(path)


def read_index_status(path):
    with closing(sqlite3.connect(f'file:{Path(path)}?mode=ro', uri=True)) as connection:
        row = connection.execute("SELECT value FROM metadata WHERE key='status'").fetchone()
        if not row:
            raise ValueError('The index has no repository metadata.')
        status = json.loads(row[0])
    return status


def canonical_repository(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or '\0' in value:
        raise ValueError('Choose a local Git repository path.')
    candidate = Path(value).expanduser().resolve()
    if not candidate.is_dir():
        raise ValueError('That local folder does not exist.')
    try:
        result = subprocess.run(['git', '-C', str(candidate), 'rev-parse', '--show-toplevel'],
                                check=True, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('Choose a Git working directory on this computer.') from exc
    return Path(result.stdout.strip()).resolve()


def project_id(root):
    return 'repo-' + hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]


def validate_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 100 or any(ord(c) < 32 for c in value):
        raise ValueError('Project names must be between 1 and 100 readable characters.')
    return value.strip()


def validate_map(raw):
    if not isinstance(raw, dict) or raw.get('version') != 1:
        raise ValueError('A product map must be a version 1 JSON object.')
    try:
        encoded = json.dumps(raw, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('The map must contain ordinary JSON values.') from exc
    if len(encoded.encode()) > MAX_MAP_BYTES:
        raise ValueError('The product map is too large (maximum 1 MB).')

    def bounded(value, depth=0):
        if depth > 24:
            raise ValueError('The product map is nested too deeply.')
        if isinstance(value, str) and (len(value) > 12000 or '\0' in value):
            raise ValueError('Map text is too long or contains a null character.')
        if isinstance(value, list):
            if len(value) > 1000:
                raise ValueError('A map list has too many entries.')
            for child in value:
                bounded(child, depth + 1)
        elif isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or len(key) > 100:
                    raise ValueError('Map field names must be short strings.')
                bounded(child, depth + 1)
    bounded(raw)

    def paths(item, label):
        values = item.get('paths', [])
        if not isinstance(values, list) or not values or len(values) > 200:
            raise ValueError(f'{label} needs between 1 and 200 code paths.')
        for value in values:
            if not isinstance(value, str) or len(value) > 1000:
                raise ValueError('Code paths must be short repository-relative strings.')
            clean_prefix(value)
        return values

    if 'project' in raw:
        project = raw['project']
        if not isinstance(project, dict) or not isinstance(project.get('name'), str) or not 1 <= len(project['name'].strip()) <= 160 or not isinstance(project.get('summary'), str) or not 1 <= len(project['summary'].strip()) <= 4000:
            raise ValueError('Project overview needs a name and summary.')
        validate_optional_documentation(project)
    ids = set()
    for key in ('features', 'foundations'):
        items = raw.get(key, [])
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError(f'{key} must be a list with at most 100 entries.')
        for item in items:
            validate_item(item)
            identity = key + ':' + item['id']
            if identity in ids:
                raise ValueError('Map IDs must be unique within their category.')
            ids.add(identity)
            parent_paths = paths(item, item['name'])
            caps = item.get('capabilities', [])
            if not isinstance(caps, list) or len(caps) > 100 or (key == 'foundations' and caps):
                raise ValueError('Only features may have up to 100 capabilities.')
            cap_ids, peer_paths = set(), []
            for cap in caps:
                validate_item(cap)
                if cap['id'] in cap_ids:
                    raise ValueError('Capability IDs must be unique within their feature.')
                cap_ids.add(cap['id'])
                cap_paths = paths(cap, cap['name'])
                if any(not any(matches(p, parent) for parent in parent_paths) for p in cap_paths):
                    raise ValueError('Each capability path must sit within one of its feature paths.')
                if any(matches(p, other) or matches(other, p) for p in cap_paths for other in peer_paths):
                    raise ValueError('Peer capability paths must not overlap.')
                peer_paths.extend(cap_paths)
    if isinstance(raw.get('project'), dict) and 'overview' in raw['project']:
        from .documentation import validate_overview
        validate_overview(raw['project']['overview'], {item['id'] for item in raw.get('features', [])})
    exclusions = raw.get('excludedPaths', [])
    if not isinstance(exclusions, list):
        raise ValueError('excludedPaths must be a list of relative paths.')
    for path in exclusions:
        if not isinstance(path, str) or len(path) > 1000:
            raise ValueError('Excluded paths must be short strings.')
        clean_prefix(path)
    if not isinstance(raw.get('notes', []), list) or any(not isinstance(note, str) for note in raw.get('notes', [])):
        raise ValueError('Map notes must be a list of strings.')
    Taxonomy(raw)
    return copy.deepcopy(raw)


def validate_item(item):
    if not isinstance(item, dict):
        raise ValueError('Each feature, capability, and foundation must be an object.')
    if not isinstance(item.get('id'), str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}', item['id']):
        raise ValueError('Map IDs must use lowercase letters, numbers, dashes, or underscores.')
    if not isinstance(item.get('name'), str) or not 1 <= len(item['name'].strip()) <= 160:
        raise ValueError('Every map area needs a name of at most 160 characters.')
    if not isinstance(item.get('summary'), str) or not 1 <= len(item['summary'].strip()) <= 4000:
        raise ValueError('Every map area needs an explanation of at most 4,000 characters.')
    validate_optional_documentation(item)


def validate_optional_documentation(item):
    if 'readerGuide' in item:
        from .reader_guide import validate_reader_guide
        validate_reader_guide(item['readerGuide'])
    if 'documentation' in item:
        from .documentation import SECTIONS
        value = item['documentation']
        if not isinstance(value, dict) or any(key not in SECTIONS or not isinstance(text, str) or len(text) > 8000 for key, text in value.items()):
            raise ValueError('Documentation sections must be known text fields of at most 8000 characters.')
    if 'entryPoints' in item:
        entries = item['entryPoints']
        if not isinstance(entries, list) or len(entries) > 20:
            raise ValueError('Entry points must be a bounded list.')
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not isinstance(entry.get('line'), int) or isinstance(entry['line'], bool) or entry['line'] < 1 or not isinstance(entry.get('reason'), str):
                raise ValueError('Entry points need path, positive line and explanation.')
            clean_prefix(entry['path'])


def mark_map(raw, source):
    raw = copy.deepcopy(raw)
    raw['summarySource'] = source
    if isinstance(raw.get('project'), dict):
        raw['project']['summarySource'] = source
    for item in [*raw.get('features', []), *raw.get('foundations', [])]:
        item['summarySource'] = source
        for capability in item.get('capabilities', []):
            capability['summarySource'] = source
    return raw


def snapshot(root):
    result = {}
    for relative, candidate in source_files(Path(root)):
        try:
            stat = candidate.stat()
            if stat.st_size <= MAX_FILE_BYTES:
                result[relative] = [stat.st_size, stat.st_mtime_ns]
        except OSError:
            continue
    return result


def diff_snapshots(before, after):
    added, deleted = set(after) - set(before), set(before) - set(after)
    modified = {path for path in set(before) & set(after) if before[path] != after[path]}
    return {'count': len(added) + len(deleted) + len(modified),
            'added': len(added), 'deleted': len(deleted), 'modified': len(modified)}


class ProjectRegistry:
    def __init__(self, data_dir=None, legacy_db=DEFAULT_DB, legacy_map=None,
                 indexer=None, discoverer=None, poll_interval=30, start_workers=True, monitor=True):
        self.data_dir = Path(data_dir or DEFAULT_DB.parent).resolve()
        self.registry_path = self.data_dir / 'projects.json'
        self.lock, self.heavy = threading.RLock(), threading.Lock()
        self.queue, self.pending = queue.Queue(), set()
        self.stop = threading.Event()
        self.indexer, self.discoverer = indexer or index_repository, discoverer
        self.poll_interval = max(1, poll_interval)
        self.records, self.active = {}, None
        self.workers = []
        if self.registry_path.exists():
            saved = json.loads(self.registry_path.read_text())
            self.records = {p['id']: p for p in saved.get('projects', [])}
            self.active = saved.get('activeProjectId')
            for entry in self.records.values():
                entry.pop('_deferredJobs', None)
                entry.pop('example', None)
                if not Path(entry['_map']).is_file():
                    directory = Path(entry['_db']).parent
                    entry['_map'] = str(directory / 'product-map.json')
                    entry['_cache'] = str(directory / 'product-projection.json')
                    if not Path(entry['_map']).is_file():
                        entry['mapStatus'] = 'suggested'
                if (entry.get('job') or {}).get('state') in {'queued', 'running'}:
                    entry['state'] = 'ready' if entry.get('indexedAt') else 'error'
                    entry['job'] = {'state': 'error', 'phase': 'interrupted', 'message': 'The previous local job was interrupted.', 'error': 'Restart the index when ready.'}
        if legacy_db and Path(legacy_db).is_file():
            legacy_db = Path(legacy_db).resolve()
            status = read_index_status(legacy_db)
            indexed_root = Path(status['root']).resolve()
            explicit_map = Path(legacy_map).resolve() if legacy_map else None
            for record in self.records.values():
                if Path(record['_db']).resolve() == legacy_db and Path(record['path']).resolve() != indexed_root:
                    # An explicit standalone CLI index may have replaced this file.
                    record['_db'] = str(self.data_dir / 'projects' / record['id'] / 'atlas.db')
                    record['_cache'] = str(self.data_dir / 'projects' / record['id'] / 'product-projection.json')
                    record.update(state='error', indexedAt=None, stats=None, languages=[], languageAnalysis=[],
                                  changes={'state': 'unknown', 'count': 0, 'checkedAt': None})
                    record['job'] = {'state': 'error', 'phase': 'index',
                                     'message': 'The previous index file now belongs to another checkout.',
                                     'error': 'Refresh this saved project to build its own isolated index.'}
            identity = project_id(status['root'])
            if identity not in self.records:
                entry = self._new(status['root'], status['name'])
                nearby_map = legacy_db.parent / 'product-map.json'
                chosen_map = explicit_map or (nearby_map if nearby_map.is_file() else Path(entry['_map']))
                entry.update(_db=str(legacy_db), _map=str(chosen_map),
                             mapStatus='reviewed' if chosen_map.is_file() else 'suggested')
                self._set_status(entry, status)
                self.records[identity] = entry
                self.active = self.active or identity
            elif Path(self.records[identity]['_db']).resolve() == legacy_db or not Path(self.records[identity]['_db']).is_file():
                entry = self.records[identity]
                entry['_db'] = str(legacy_db)
                if entry.get('indexedAt') != status['indexedAt']:
                    self._set_status(entry, status)
                    entry['_baselineTrusted'] = False
                    entry['changes'] = {'state': 'unknown', 'count': 0, 'checkedAt': None}
        missing_maps = [identity for identity, entry in self.records.items()
                        if entry.get('indexedAt') and Path(entry['_db']).is_file()
                        and not Path(entry['_map']).is_file()]
        if self.active not in self.records:
            self.active = next(iter(self.records), None)
        self._save()
        if start_workers:
            self.workers = [threading.Thread(target=self._worker, name='atlas-index-worker', daemon=True)]
            if monitor:
                self.workers.append(threading.Thread(target=self._poll, name='atlas-change-poller', daemon=True))
            for worker in self.workers:
                worker.start()
            for identity in missing_maps:
                self.enqueue(identity, 'suggestions')

    def close(self):
        self.stop.set()
        self.queue.put(None)

    def _new(self, root, name):
        identity = project_id(root)
        directory = self.data_dir / 'projects' / identity
        return {'id': identity, 'name': name, 'path': str(root), 'state': 'new', 'indexedAt': None,
                'stats': None, 'languages': [], 'languageAnalysis': [], 'warnings': [], 'mapStatus': 'suggested',
                'autoUpdate': False, 'hasSuggestion': False, 'suggestionUpdatedAt': None,
                'changes': {'state': 'unknown', 'count': 0, 'checkedAt': None}, 'job': None,
                '_db': str(directory / 'atlas.db'), '_map': str(directory / 'product-map.json'),
                '_cache': str(directory / 'product-projection.json'), '_suggestion': str(directory / 'suggested-map.json'),
                '_fingerprint': str(directory / 'source-fingerprint.json'), '_baselineTrusted': False}

    def _save(self):
        with self.lock:
            atomic_json(self.registry_path, {'version': 1, 'activeProjectId': self.active, 'projects': list(self.records.values())})

    def _get(self, identity=None):
        identity = identity or self.active
        if identity not in self.records:
            raise KeyError('Choose a saved project first.' if identity is None else 'Project not found.')
        return self.records[identity]

    @staticmethod
    def _public(entry):
        return copy.deepcopy({key: value for key, value in entry.items() if not key.startswith('_')})

    def project(self, identity=None):
        with self.lock:
            return self._public(self._get(identity))

    def paths(self, identity=None):
        with self.lock:
            entry = self._get(identity)
            return {key: Path(entry['_' + key]) for key in ('db', 'map', 'cache', 'suggestion', 'fingerprint')}

    def list(self):
        with self.lock:
            return {'projects': [self._public(p) for p in self.records.values()], 'activeProjectId': self.active}

    def register(self, path, name=None):
        root = canonical_repository(path)
        identity = project_id(root)
        with self.lock:
            if identity in self.records:
                return self._public(self.records[identity])
            self.records[identity] = self._new(root, validate_name(name) if name is not None else root.name[:100])
            self.active = self.active or identity
            self._save()
        return self.enqueue(identity, 'index')

    def activate(self, identity):
        with self.lock:
            self._get(identity)
            self.active = identity
            self._save()
            return self.project(identity)

    def update(self, identity, body):
        if not body or any(key not in {'name', 'autoUpdate'} for key in body):
            raise ValueError('Update name or autoUpdate only.')
        if 'autoUpdate' in body and not isinstance(body['autoUpdate'], bool):
            raise ValueError('autoUpdate must be true or false.')
        name = validate_name(body['name']) if 'name' in body else None
        with self.lock:
            entry = self._get(identity)
            if name is not None:
                entry['name'] = name
            if 'autoUpdate' in body:
                entry['autoUpdate'] = body['autoUpdate']
            self._save()
            return self.project(identity)

    def enqueue(self, identity, kind):
        with self.lock:
            entry = self._get(identity)
            if any(project == identity for project, _ in self.pending):
                # Preserve an explicit refresh/suggestion request made during another job.
                # Repeated clicks on the running action remain idempotent.
                if kind != 'check' and (identity, kind) not in self.pending:
                    deferred = entry.setdefault('_deferredJobs', [])
                    if kind not in deferred:
                        deferred.append(kind)
                        self._save()
                return self.project(identity)
            if kind == 'suggestions' and not Path(entry['_db']).is_file():
                raise ValueError('Index this project before requesting map suggestions.')
            self.pending.add((identity, kind))
            if kind == 'index':
                entry['state'] = 'indexing'
            entry['job'] = {'state': 'queued', 'phase': kind, 'message': 'Waiting for the local worker.'}
            self._save()
            self.queue.put((identity, kind))
            return self.project(identity)

    def _progress(self, identity, update):
        with self.lock:
            entry = self._get(identity)
            entry['job'] = {'state': 'running', 'phase': str(update.get('phase', 'index'))[:80],
                            'message': str(update.get('message', 'Reading local source files.'))[:500]}
            if 'files' in update:
                entry['job']['files'] = update['files']
            self._save()

    def _set_status(self, entry, status):
        for key in ('indexedAt', 'stats', 'languages', 'warnings'):
            entry[key] = status.get(key)
        entry['languageAnalysis'] = status.get('languageAnalysis', [])
        entry['state'] = 'ready'

    def _discover(self, db, root):
        if self.discoverer:
            raw = self.discoverer(db, root)
        else:
            from .discovery import discover_product_map
            raw = discover_product_map(db, root)
        return mark_map(validate_map(raw), 'suggested')

    def _index(self, identity):
        entry = self.project(identity)
        paths = self.paths(identity)
        before = snapshot(entry['path'])
        self._progress(identity, {'phase': 'index', 'message': 'Reading local source files.'})
        options = {'progress': lambda update: self._progress(identity, update)} if 'progress' in inspect.signature(self.indexer).parameters else {}
        status = self.indexer(Path(entry['path']), paths['db'], **options)
        after = snapshot(entry['path'])
        atomic_json(paths['fingerprint'], before)
        if not paths['map'].exists():
            self._progress(identity, {'phase': 'suggestions', 'message': 'Preparing a first suggested product map.'})
            candidate = self._discover(paths['db'], Path(entry['path']))
            # A reviewed map may have been saved while indexing; never replace it.
            with self.lock:
                if not paths['map'].exists():
                    atomic_json(paths['map'], candidate)
                    self._get(identity)['mapStatus'] = 'suggested'
        with self.lock:
            record = self._get(identity)
            self._set_status(record, status)
            record['_baselineTrusted'] = True
            difference = diff_snapshots(before, after)
            record['changes'] = {'state': 'changed' if difference['count'] else 'current', **difference, 'checkedAt': now()}
            self._save()

    def _suggest(self, identity):
        entry, paths = self.project(identity), self.paths(identity)
        self._progress(identity, {'phase': 'suggestions', 'message': 'Reading the indexed code for map suggestions.'})
        candidate = self._discover(paths['db'], Path(entry['path']))
        with self.lock:
            if not paths['map'].exists():
                atomic_json(paths['map'], candidate)
                self._get(identity)['mapStatus'] = 'suggested'
            atomic_json(paths['suggestion'], candidate)
            record = self._get(identity)
            record['hasSuggestion'], record['suggestionUpdatedAt'] = True, now()
            self._save()

    def map(self, identity):
        with self.lock:
            paths = self.paths(identity)
            raw = json.loads(paths['map'].read_text()) if paths['map'].exists() else None
            candidate = json.loads(paths['suggestion'].read_text()) if paths['suggestion'].exists() else None
            return {'map': raw, 'project': self.project(identity), 'suggestion': candidate}

    def save_candidate(self, identity, raw, indexed_at, agent):
        """Store agent output separately; never imply human review."""
        if not isinstance(agent, str) or not 1 <= len(agent.strip()) <= 160:
            raise ValueError('Provide an agent name of at most 160 characters.')
        candidate = mark_map(validate_map(raw), 'agent-generated')
        with self.lock:
            record = self._get(identity)
            if not indexed_at or indexed_at != record.get('indexedAt'):
                raise ValueError('The source index changed. Read fresh context before submitting.')
            if (record.get('job') or {}).get('state') in {'queued', 'running'}:
                raise ValueError('Wait for the project job to finish before submitting.')
            from .documentation import validate_candidate_documentation
            validate_candidate_documentation(candidate, self.paths(identity)['db'], record['path'])
            candidate['generation'] = {'agent': agent.strip(), 'indexedAt': indexed_at,
                                       'createdAt': now(), 'reviewed': False}
            candidate['reviewed'] = False
            candidate['status'] = 'suggested'
            for item in [*candidate.get('features', []), *candidate.get('foundations', [])]:
                for area in [item, *item.get('capabilities', [])]:
                    area['reviewed'] = False
                    area['status'] = 'suggested'
            atomic_json(self.paths(identity)['suggestion'], candidate)
            record['hasSuggestion'], record['suggestionUpdatedAt'] = True, now()
            self._save()
        return self.map(identity)

    def save_map(self, identity, raw):
        reviewed = mark_map(validate_map(raw), 'reviewed')
        with self.lock:
            paths = self.paths(identity)
            atomic_json(paths['map'], reviewed)
            record = self._get(identity)
            record['mapStatus'] = 'reviewed'
            record['hasSuggestion'] = False
            if paths['suggestion'].exists():
                paths['suggestion'].unlink()
            self._save()
        return self.map(identity)

    def _check(self, identity):
        entry, paths = self.project(identity), self.paths(identity)
        current = snapshot(entry['path'])
        if paths['fingerprint'].exists():
            baseline = json.loads(paths['fingerprint'].read_text())
        else:
            # Adoption cannot reconstruct historical file mtimes from the old index.
            baseline = current
            atomic_json(paths['fingerprint'], current)
        difference = diff_snapshots(baseline, current)
        digest = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
        with self.lock:
            record = self._get(identity)
            state = 'changed' if difference['count'] else ('current' if record.get('_baselineTrusted') else 'unknown')
            record['changes'] = {'state': state, **difference, 'checkedAt': now()}
            if record.get('_observedFingerprint') != digest:
                record['_observedFingerprint'], record['_lastSourceChange'] = digest, time.time()
            should_update = record['autoUpdate'] and state == 'changed' and time.time() - record.get('_lastSourceChange', time.time()) >= 5
            self._save()
            result = {'project': self.project(identity), 'changes': copy.deepcopy(record['changes']),
                      'checking': False, '_autoIndexRequested': should_update}
        return result

    def changes(self, identity):
        self.project(identity)
        if self.heavy.acquire(blocking=False):
            try:
                result = self._check(identity)
            finally:
                self.heavy.release()
            if result.pop('_autoIndexRequested'):
                result['project'] = self.enqueue(identity, 'index')
            return result
        self.enqueue(identity, 'check')
        project = self.project(identity)
        return {'project': project, 'changes': project['changes'], 'checking': True}

    def _worker(self):
        while not self.stop.is_set():
            task = self.queue.get()
            if task is None:
                return
            identity, kind = task
            request_index = False
            try:
                with self.heavy:
                    if kind == 'index':
                        self._index(identity)
                    elif kind == 'suggestions':
                        self._suggest(identity)
                    else:
                        self._progress(identity, {'phase': 'check', 'message': 'Checking local file changes.'})
                        request_index = self._check(identity)['_autoIndexRequested']
                with self.lock:
                    self._get(identity)['job'] = {'state': 'complete', 'phase': kind, 'message': 'Local work is complete.'}
            except Exception as exc:
                with self.lock:
                    entry = self._get(identity)
                    if kind == 'index':
                        entry['state'] = 'error'
                    entry['job'] = {'state': 'error', 'phase': kind, 'message': 'The local job could not finish.', 'error': str(exc)[:1000]}
            finally:
                with self.lock:
                    self.pending.discard(task)
                    deferred = self._get(identity).get('_deferredJobs', [])
                    next_job = deferred.pop(0) if deferred else None
                    if next_job:
                        self.enqueue(identity, next_job)
                    elif request_index:
                        self.enqueue(identity, 'index')
                    self._save()
                self.queue.task_done()

    def _poll(self):
        while not self.stop.wait(self.poll_interval):
            with self.lock:
                identities = [p['id'] for p in self.records.values() if p['autoUpdate'] and p.get('indexedAt')]
            for identity in identities:
                self.enqueue(identity, 'check')
