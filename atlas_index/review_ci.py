"""Read-only GitHub CI observations for an immutable Atlas review head."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from .review import github_origin, parse_pr

API_VERSION = '2026-03-10'
MAX_RESPONSE_BYTES = 4_000_000
MAX_PAGES = 3
PAGE_SIZE = 100
TIMEOUT = 12
FAILURES = {'failure', 'cancelled', 'timed_out', 'action_required', 'startup_failure', 'stale'}
PENDING = {'queued', 'in_progress', 'requested', 'waiting', 'pending'}
INFORMATIONAL = {'neutral', 'skipped'}
LIMITATIONS = [
    'CI reports execution results; passing checks do not prove the changed behavior is sufficiently tested.',
    'Only this pinned head commit is queried. Merge-commit results, required-check rules, coverage artifacts, and test logs are not imported.',
]


class CIUnavailable(Exception):
    """An intentionally non-sensitive explanation suitable for the UI."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Endpoint hosts are fixed; never follow response-controlled destinations.
        return None


def _decode(raw):
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CIUnavailable('The GitHub CI response exceeded the preview limit.')
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CIUnavailable('GitHub returned an unreadable CI response.') from exc
    if not isinstance(result, dict):
        raise CIUnavailable('GitHub returned an unexpected CI response.')
    return result


def _github_json(endpoint):
    """A constructed github.com GET, with existing gh authentication as fallback."""
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'Atlas-local-review',
               'X-GitHub-Api-Version': API_VERSION}
    request = urllib.request.Request('https://api.github.com/' + endpoint, headers=headers)
    problem = 'GitHub CI could not be reached. Check network access and try again.'
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=TIMEOUT) as response:
            return _decode(response.read(MAX_RESPONSE_BYTES + 1))
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 429}:
            problem = 'GitHub CI access is rate-limited or not permitted. Existing gh authentication can provide access.'
        elif exc.code in {401, 404}:
            problem = 'GitHub CI is unavailable for this repository or commit. Existing gh authentication may be required.'
        else:
            problem = 'GitHub could not return CI results. Try again later.'
        exc.close()
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    if not shutil.which('gh'):
        raise CIUnavailable(problem)
    # No token is read, persisted, or returned by Atlas. gh uses its own credential
    # store. stderr is suppressed because an authentication error may contain data
    # unrelated to this review.
    env = {**os.environ, 'GH_PROMPT_DISABLED': '1', 'GH_PAGER': 'cat', 'GH_DEBUG': ''}
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                ['gh', 'api', '--hostname', 'github.com', '--method', 'GET', endpoint,
                 '-H', 'Accept: application/vnd.github+json', '-H', f'X-GitHub-Api-Version: {API_VERSION}'],
                stdout=output, stderr=subprocess.DEVNULL, env=env, timeout=TIMEOUT)
            if result.returncode:
                raise CIUnavailable(problem)
            if output.tell() > MAX_RESPONSE_BYTES:
                raise CIUnavailable('The GitHub CI response exceeded the preview limit.')
            output.seek(0)
            return _decode(output.read(MAX_RESPONSE_BYTES + 1))
    except (OSError, subprocess.SubprocessError) as exc:
        raise CIUnavailable(problem) from exc


def _pages(endpoint, key, params):
    rows, limitations, complete = [], [], True
    for page in range(1, MAX_PAGES + 1):
        try:
            data = _github_json(endpoint + '?' + urlencode({**params, 'per_page': PAGE_SIZE, 'page': page}))
            items, total = data.get(key), data.get('total_count')
            if (not isinstance(items, list) or not isinstance(total, int) or isinstance(total, bool)
                    or total < 0 or len(items) > PAGE_SIZE or not all(isinstance(item, dict) for item in items)):
                raise CIUnavailable('GitHub returned an unexpected CI list.')
        except CIUnavailable as exc:
            limitations.append(str(exc))
            return rows, False, limitations
        rows.extend(items)
        if len(rows) >= total:
            break
        if not items or page == MAX_PAGES:
            complete = False
            limitations.append(f'GitHub CI results are incomplete; at most {MAX_PAGES * PAGE_SIZE} entries per source are displayed.')
            break
    return rows, complete, limitations


def _text(value, fallback=''):
    return ''.join(character for character in str(value or fallback) if ord(character) >= 32)[:200]


def _github_link(value, repository):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.netloc.lower() != 'github.com'
                or not parsed.path.lower().startswith('/' + repository.lower() + '/')
                or any(ord(character) < 32 for character in value)):
            return None
    except ValueError:
        return None
    return value[:2000]


def _positive_id(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _normalize(rows, repository, sha, kind):
    selected, rejected = {}, 0
    for row in rows:
        identity = _positive_id(row.get('id'))
        repo = row.get('repository', {}).get('full_name') if isinstance(row.get('repository'), dict) else None
        if (not identity or row.get('head_sha') != sha
                or (repo is not None and (not isinstance(repo, str) or repo.lower() != repository.lower()))):
            rejected += 1
            continue
        item = {'id': identity, 'name': _text(row.get('name'), 'Unnamed workflow' if kind == 'runs' else 'Unnamed check'),
                'status': _text(row.get('status'), 'unknown'), 'conclusion': _text(row.get('conclusion')) or None,
                'url': _github_link(row.get('html_url'), repository), 'sha': sha}
        if kind == 'runs':
            item.update(event=_text(row.get('event')), attempt=_positive_id(row.get('run_attempt')) or 1)
            # Multiple runs for one workflow/event may include older failures.
            # Preserve only its newest observation, while keeping separate events.
            key = (_positive_id(row.get('workflow_id')) or item['name'], item['event'])
            rank = (identity, item['attempt'])
        else:
            app = row.get('app') if isinstance(row.get('app'), dict) else {}
            item['app'] = _text(app.get('name') or app.get('slug'), 'GitHub check')
            key = (_positive_id(app.get('id')) or item['app'], item['name'])
            rank = (identity,)
        if key not in selected or rank > selected[key][0]:
            selected[key] = (rank, item)
    return sorted((value[1] for value in selected.values()), key=lambda item: (item['name'].lower(), item['id'])), rejected


def _status(items, complete):
    if any(item['status'] == 'completed' and item['conclusion'] in FAILURES for item in items):
        return 'failed'
    if not complete:
        return 'unavailable'
    if any(item['status'] in PENDING for item in items):
        return 'pending'
    if not items:
        return 'none'
    if (all(item['status'] == 'completed' and item['conclusion'] in {'success'} | INFORMATIONAL for item in items)
            and any(item['conclusion'] == 'success' for item in items)):
        return 'passed'
    return 'unavailable'


def get_ci(store, identity):
    """Never resolve latest here: the caller must request its displayed scope."""
    if not identity:
        raise ValueError('Choose the pinned review scope before loading CI.')
    review = store._review(identity)
    if review.get('id') != identity or any(not re.fullmatch(r'[0-9a-f]{40,64}', str(review.get(key, '')))
                                           for key in ('baseSha', 'headSha')):
        raise ValueError('The saved review does not match a valid pinned commit scope.')
    repository = github_origin(store.root)
    result = {'reviewId': identity, 'baseSha': review['baseSha'], 'headSha': review['headSha'],
              'repository': repository, 'status': 'unavailable', 'checkedAt': None,
              'complete': False, 'runs': [], 'checks': [], 'limitations': list(LIMITATIONS)}
    if not repository or any(segment in {'.', '..'} for segment in repository.split('/')):
        result['limitations'].append('GitHub CI requires a github.com origin for this project.')
    else:
        # A changed project origin must not silently attach CI from another repo
        # to an already-pinned PR review.
        if review.get('url'):
            parse_pr(review['url'], repository)
        sha = review['headSha']
        requests = [('runs', f'repos/{repository}/actions/runs', 'workflow_runs', {'head_sha': sha}),
                    ('checks', f'repos/{repository}/commits/{sha}/check-runs', 'check_runs', {'filter': 'latest'})]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [(kind, pool.submit(_pages, endpoint, key, params)) for kind, endpoint, key, params in requests]
            complete = True
            for kind, future in futures:
                rows, fetched, limitations = future.result()
                items, rejected = _normalize(rows, repository, sha, kind)
                result[kind] = items
                complete &= fetched and not rejected
                result['limitations'].extend(limitations)
                if rejected:
                    result['limitations'].append('CI entries without a matching repository and exact head SHA were excluded.')
        result['complete'] = bool(complete)
        result['status'] = _status(result['runs'] + result['checks'], complete)
        if any(run['event'] == 'pull_request_target' for run in result['runs']):
            result['limitations'].append('PR-target workflows can validate metadata or base-branch logic; their success alone does not show that changed code executed.')
        if result['status'] == 'none':
            result['limitations'].append('No workflow runs or check runs were reported on this head commit. CI may run on a separate merge commit.')
        elif result['status'] == 'unavailable' and complete:
            result['limitations'].append('The available checks do not establish a successful run; skipped, neutral, or unknown results are not treated as passing.')
    result['limitations'] = list(dict.fromkeys(result['limitations']))
    result['checkedAt'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    return result
