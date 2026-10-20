"""Agent-authored review conclusions, scoped to immutable snapshots.

Atlas validates provenance and scope. Semantic correctness still requires review.
"""
import copy
import hashlib
import json
import re
from datetime import datetime, timezone

from .review import relative_path

VERSION = 1
GENERIC = re.compile(r'^(?:check (?:the )?inputs\b|exercise both outcomes\b|check (?:both outcomes|remaining callers)\b|confirm existing callers remain valid\b)', re.I)


def analysis_path(store, review):
    stamp = hashlib.sha256((review['baseSha'] + ':' + review['headSha']).encode()).hexdigest()[:16]
    return store.directory / f'analysis-{review["id"]}-{stamp}.json'


def empty_analysis(review):
    return {'version': VERSION, 'reviewId': review['id'], 'baseSha': review['baseSha'], 'headSha': review['headSha'],
            'status': 'not-run', 'blocks': [], 'findings': [], 'reviewedPaths': [], 'limitations': [], 'coverageComplete': False}


def validate_analysis(store, review, raw):
    if not isinstance(raw, dict):
        raise ValueError('Submit a structured review analysis.')
    for key in ('reviewId', 'baseSha', 'headSha'):
        if raw.get(key) != review[key if key != 'reviewId' else 'id']:
            raise ValueError('Review analysis belongs to different pinned commits or review scope.')
    if len(json.dumps(raw, allow_nan=False).encode()) > 512 * 1024:
        raise ValueError('Review analysis exceeds 512 KB.')

    def text(item, key, maximum=2000):
        value = item.get(key) if isinstance(item, dict) else None
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum or any(ord(c) < 32 and c != '\n' for c in value):
            raise ValueError(f'Review analysis {key} must contain bounded explanatory text.')
        if GENERIC.match(value.strip()):
            raise ValueError('Generic checklist prompts are not review conclusions. Describe the actual behavior or defect.')

    text(raw, 'agent', 160)
    text(raw, 'summary', 600)
    files = {item['path']: item for item in review['files']}
    diffs = {}
    def diff(path):
        if path not in files:
            raise ValueError('Analysis must refer to a changed file in this review.')
        if path not in diffs:
            diffs[path] = store.diff(path, review['id'])
        return diffs[path]

    def proof(item):
        if not isinstance(item, dict) or item.get('side') not in ('base', 'head'):
            raise ValueError('Review evidence needs a path, side and source line.')
        path = relative_path(item.get('path'))
        line = item.get('line')
        end = item.get('endLine', line)
        if any(isinstance(n, bool) or not isinstance(n, int) for n in (line, end)) or line < 1 or end < line or end - line > 30:
            raise ValueError('Review evidence needs a short positive source range.')
        store.source(path, item['side'], line, review['id'])
        if end != line:
            store.source(path, item['side'], end, review['id'])
        return path

    def evidence(item):
        refs = item.get('evidence')
        if not isinstance(refs, list) or not 1 <= len(refs) <= 12:
            raise ValueError('Every conclusion needs pinned source evidence.')
        for ref in refs:
            proof(ref)

    def changed_anchor(path, anchor, hunk=None):
        proof(anchor)
        source_path = files[path].get('oldPath') or path if anchor['side'] == 'base' else path
        if anchor['path'] != source_path:
            raise ValueError('The anchor must use the changed file path on the selected side.')
        hunks = diff(path)['hunks']
        if hunk is not None:
            if isinstance(hunk, bool) or not isinstance(hunk, int) or not 0 <= hunk < len(hunks):
                raise ValueError('Block index is outside the pinned diff.')
            hunks = [hunks[hunk]]
        field, kind = ('oldLine', 'removed') if anchor['side'] == 'base' else ('newLine', 'added')
        if not any(line['kind'] == kind and line[field] == anchor['line'] for block in hunks for line in block['lines']):
            raise ValueError('Anchor the conclusion to a changed line, not an arbitrary context line.')

    paths = raw.get('reviewedPaths')
    if not isinstance(paths, list) or len(paths) > len(files) or any(not isinstance(path, str) or path not in files for path in paths) or len(set(paths)) != len(paths):
        raise ValueError('Reviewed paths must be unique changed files from this scope.')
    limitations = raw.get('limitations')
    if not isinstance(limitations, list) or len(limitations) > 30 or any(not isinstance(value, str) or not 1 <= len(value.strip()) <= 2000 for value in limitations):
        raise ValueError('Disclose uninspected source and other analysis limitations as a bounded list.')
    blocks = raw.get('blocks')
    findings = raw.get('findings')
    if not isinstance(blocks, list) or len(blocks) > 500 or not isinstance(findings, list) or len(findings) > 50:
        raise ValueError('Review analysis needs bounded block summaries and findings lists.')
    seen = set()
    for block in blocks:
        text(block, 'path', 1000)
        text(block, 'summary', 500)
        text(block, 'impact', 800)
        path, index = block['path'], block.get('hunkIndex')
        diff(path)
        if path not in paths:
            raise ValueError('Only reviewed files can have summaries or findings.')
        if isinstance(index, bool) or not isinstance(index, int) or (path, index) in seen:
            raise ValueError('Block summaries need unique integer hunk indices.')
        seen.add((path, index))
        evidence(block)
        # At least one supporting location must be a changed line in this block.
        valid = False
        for ref in block['evidence']:
            try:
                changed_anchor(path, ref, index)
                valid = True
            except (ValueError, KeyError):
                continue
        if not valid:
            raise ValueError('Block summary needs evidence on a changed line in that block.')
    expected_blocks = {(path, index) for path in paths for index in range(len(diff(path)['hunks']))}
    if seen != expected_blocks:
        raise ValueError('Every reviewed changed block needs a concise TLDR; disclose uninspected files instead of marking them reviewed.')
    ids = set()
    for finding in findings:
        for key, maximum in [('id', 100), ('path', 1000), ('title', 140), ('trigger', 1200), ('cause', 2000), ('consequence', 1200), ('fix', 1200)]:
            text(finding, key, maximum)
        if finding.get('priority') not in ('P0', 'P1', 'P2', 'P3') or finding['id'] in ids:
            raise ValueError('Findings need unique IDs and a priority from P0 to P3.')
        ids.add(finding['id'])
        path = finding['path']
        diff(path)
        if path not in paths:
            raise ValueError('Only reviewed files can have summaries or findings.')
        anchor = finding.get('anchor')
        changed_anchor(path, anchor)
        evidence(finding)
        if not any(ref['path'] == anchor['path'] and ref['side'] == anchor['side'] and ref['line'] == anchor['line'] for ref in finding['evidence']):
            raise ValueError('Finding evidence must include its changed-line anchor.')
    result = copy.deepcopy(raw)
    for finding in result['findings']:
        content = {key: value for key, value in finding.items() if key != 'fingerprint'}
        finding['fingerprint'] = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:20]
    result.update(version=VERSION, status='agent-reviewed', coverageComplete=set(paths) == set(files) and not any(
        review.get('truncated', {}).get(key) for key in ('files', 'diffs')) and not limitations and not any(file.get('excluded') or file.get('binary') or file.get('diffTruncated') for file in files.values()))
    return result


def save_analysis(store, identity, raw):
    review = store._review(identity)
    result = validate_analysis(store, review, raw)
    result['createdAt'] = datetime.now(timezone.utc).isoformat()
    path = analysis_path(store, review)
    with store.lock:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(result))
        temporary.replace(path)
    return result


def load_analysis(store, review):
    path = analysis_path(store, review)
    with store.lock:
        if path.is_file():
            try:
                raw = json.loads(path.read_text())
                if raw.get('version') == VERSION:
                    result = validate_analysis(store, review, raw)
                    return result
            except (ValueError, KeyError, OSError, TypeError):
                pass
    return empty_analysis(review)


def attach_analysis(guide, analysis):
    guide = copy.deepcopy(guide)
    guide['agentReview'] = {key: value for key, value in analysis.items() if key not in ('blocks', 'findings')}
    guide['agentReview']['summarizedBlocks'] = len(analysis['blocks'])
    guide['agentReview']['totalBlocks'] = sum(len(file['hunks']) for file in guide['files'])
    guide['findings'] = analysis['findings']
    by_block = {(block['path'], block['hunkIndex']): block for block in analysis['blocks']}
    for file in guide['files']:
        file['findings'] = [finding for finding in analysis['findings'] if finding['path'] == file['path']]
        summaries = []
        for hunk in file['hunks']:
            block = by_block.get((file['path'], hunk['index']))
            hunk['summary'] = block['summary'] if block else None
            hunk['impact'] = block['impact'] if block else None
            hunk['summarySource'] = 'agent' if block else 'not-reviewed'
            if block:
                summaries.append(block['summary'])
        file['summarySource'] = 'agent' if summaries else 'not-reviewed'
        if summaries:
            file['summary'] = summaries[0]
    if analysis['status'] != 'not-run':
        guide['brief']['summary'] = analysis['summary']
        guide['brief']['intentSource'] = 'agent-review'
        guide['brief']['intentNote'] = 'Agent-authored explanation checked against the pinned comparison; conclusions still need reviewer assessment.'
    return guide
