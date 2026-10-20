"""A reproducible review guide from pinned source, diffs, and static references.

This deliberately does not execute project code or ask a model to invent intent.
Descriptions of call changes are syntax observations. Repository prose is always
identified as the author's explanation, not independently verified behavior.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import PurePosixPath

from .indexer import MAX_FILE_BYTES, safe_source

VERSION = 5
MAX_DEFINITIONS = 24
MAX_ASSERTIONS = 24
MAX_RELATED_TESTS = 12
MAX_FLOW_NODES = 16
MAX_SCENARIOS = 18


def compact(value, limit=220):
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or ''))
    value = re.sub(r'\s+', ' ', value).strip()
    return value if len(value) <= limit else value[:limit - 1].rsplit(' ', 1)[0] + '…'


def evidence(path, side, line, end=None):
    result = {'path': path, 'side': side, 'line': max(1, int(line))}
    if end is not None and end > result['line']:
        result['endLine'] = int(end)
    return result


def unique(items):
    seen, result = set(), []
    for item in items:
        key = json.dumps(item, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def is_test(path):
    name = PurePosixPath(path).name.lower()
    parts = {part.lower() for part in PurePosixPath(path).parts}
    return bool(parts & {'test', 'tests', '__tests__', 'testing', 'spec', 'specs'} or
                re.search(r'(^test[_-]|[_-]tests?\.|[.]test[.]|[.]spec[.]|_test[.]|_spec[.])', name) or
                name in {'tests.py', 'test.py', 'conftest.py'})


def role_for(path):
    if is_test(path):
        return 'tests'
    if PurePosixPath(path).suffix.lower() in {'.md', '.rst', '.txt'} or {'docs', 'doc'} & set(PurePosixPath(path).parts):
        return 'documentation'
    if PurePosixPath(path).suffix.lower() in {'.json', '.yaml', '.yml', '.toml', '.ini', '.cfg'}:
        return 'configuration'
    return 'implementation'


def call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        root = call_name(node.value)
        return f'{root}.{node.attr}' if root else node.attr
    if isinstance(node, ast.Call):
        return call_name(node.func) + '()'
    return ''


def own_walk(node):
    """Walk a definition without attributing nested definitions' behavior to it."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield from own_walk(child)


def assertion_description(node, segment):
    """Describe the written expectation, without claiming that it passes."""
    def expression(item, limit=200):
        value = ast.unparse(item)
        return value if len(value) <= limit else value[:limit - 1] + '…'

    if isinstance(node, ast.Assert):
        return 'Checks that ' + expression(node.test) + '.'
    if not isinstance(node, ast.Call):
        return ''
    name = call_name(node.func).rsplit('.', 1)[-1]
    args = [expression(arg, 100) for arg in node.args]
    comparisons = {
        'assertEqual': 'equals', 'assertNotEqual': 'does not equal',
        'assertIs': 'is', 'assertIsNot': 'is not',
        'assertIn': 'is in', 'assertNotIn': 'is not in',
        'assertGreater': 'is greater than', 'assertLess': 'is less than',
        'assertGreaterEqual': 'is at least', 'assertLessEqual': 'is at most',
    }
    unary = {'assertTrue': 'is truthy', 'assertFalse': 'is falsy',
             'assertIsNone': 'is None', 'assertIsNotNone': 'is not None'}
    if name in comparisons and len(args) >= 2:
        return f'Checks that {args[0]} {comparisons[name]} {args[1]}.'
    if name in unary and args:
        return f'Checks that {args[0]} {unary[name]}.'
    if name in {'assertRaises', 'assertRaisesRegex', 'raises'} and args:
        return f'Expects {args[0]} to be raised.'
    if name in {'assertWarns', 'assertWarnsRegex', 'warns'} and args:
        return f'Expects a {args[0]} warning.'
    return 'Contains the assertion ' + expression(node) + '.'


def assertion_expectation(node):
    """Keep the exact written equality, without evaluating its expressions."""
    if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
        comparison = node.test
        if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
            return None
        actual, expected = comparison.left, comparison.comparators[0]
    elif isinstance(node, ast.Call) and call_name(node.func).rsplit('.', 1)[-1] == 'assertEqual' and len(node.args) >= 2:
        actual, expected = node.args[:2]
    else:
        return None
    literal = all(isinstance(item, (ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Load, ast.UnaryOp, ast.USub, ast.UAdd))
                  for item in ast.walk(expected))
    actual, expected = ast.unparse(actual), ast.unparse(expected)
    if len(actual) > 180 or len(expected) > 160:
        return None
    return {'actual': actual, 'expected': expected, 'literal': literal}


def table_examples(node, path, side, segment):
    """Read literal table rows tied to an equality assertion; never evaluate code."""
    assignments = {}
    for item in own_walk(node):
        if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
            assignments.setdefault(item.targets[0].id, []).append(item)
    examples = []
    for loop in own_walk(node):
        if not isinstance(loop, ast.For) or not isinstance(loop.target, (ast.Tuple, ast.List)):
            continue
        if not all(isinstance(target, ast.Name) for target in loop.target.elts):
            continue
        names = [target.id for target in loop.target.elts]
        value = loop.iter
        if isinstance(value, ast.Name):
            preceding = [item for item in assignments.get(value.id, []) if item.lineno < loop.lineno]
            if len(preceding) != 1:
                continue
            value = preceding[0].value
        if not isinstance(value, (ast.List, ast.Tuple)):
            continue
        expected = None
        for check in own_walk(loop):
            if isinstance(check, ast.Call) and call_name(check.func).rsplit('.', 1)[-1] == 'assertEqual' and len(check.args) >= 2:
                candidate = check.args[1]
            elif isinstance(check, ast.Assert) and isinstance(check.test, ast.Compare) and len(check.test.ops) == 1 and isinstance(check.test.ops[0], ast.Eq):
                candidate = check.test.comparators[0]
            else:
                continue
            if isinstance(candidate, ast.Name) and candidate.id in names:
                expected = names.index(candidate.id)
                break
        if expected is None:
            continue
        if any(isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and
               any(isinstance(target, ast.Name) and target.id == names[expected]
                   for target in (item.targets if isinstance(item, ast.Assign) else [item.target]))
               for statement in loop.body for item in ast.walk(statement)):
            continue
        for row in value.elts:
            if not isinstance(row, (ast.List, ast.Tuple)) or len(row.elts) != len(names):
                continue
            # Allow containers of literals only. Calls, names and operators are
            # left as source context, never presented as resolved example data.
            if any(not isinstance(item, (ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Load))
                   for item in ast.walk(row)):
                continue
            examples.append({
                'input': ', '.join(f'{name}={ast.unparse(item)}' for index, (name, item) in enumerate(zip(names, row.elts)) if index != expected),
                'expected': ast.unparse(row.elts[expected]),
                'evidence': evidence(path, side, row.lineno, row.end_lineno),
            })
    return examples


def parse_python(text, path, side):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return {}, 'Python could not be parsed; the guide uses visible diff lines only.'
    definitions = {}
    source_lines = text.splitlines()

    def segment(node):
        # AST columns are UTF-8 byte offsets. Slicing only the relevant lines
        # avoids ast.get_source_segment repeatedly splitting a large test file.
        start, end = node.lineno - 1, node.end_lineno - 1
        selected = source_lines[start:end + 1]
        if not selected:
            return ''
        if start == end:
            return selected[0].encode()[node.col_offset:node.end_col_offset].decode('utf-8')
        selected[0] = selected[0].encode()[node.col_offset:].decode('utf-8')
        selected[-1] = selected[-1].encode()[:node.end_col_offset].decode('utf-8')
        return '\n'.join(selected)

    def visit(parent, prefix=''):
        for node in ast.iter_child_nodes(parent):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + node.name
                start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
                calls, assertions, branches, returns = [], [], [], []
                for item in own_walk(node):
                    ref = evidence(path, side, getattr(item, 'lineno', start), getattr(item, 'end_lineno', None))
                    if isinstance(item, ast.Call):
                        label = compact(call_name(item.func), 120)
                        if label:
                            calls.append({'name': label, 'evidence': ref})
                        leaf = label.rsplit('.', 1)[-1]
                        if leaf.startswith('assert') or label in {'pytest.raises', 'pytest.warns'}:
                            assertions.append({'text': compact(segment(item), 240), 'description': assertion_description(item, segment),
                                               'expectation': assertion_expectation(item), 'evidence': ref})
                    if isinstance(item, ast.Assert):
                        assertions.append({'text': compact(segment(item), 240), 'description': assertion_description(item, segment),
                                           'expectation': assertion_expectation(item), 'evidence': ref})
                    if isinstance(item, (ast.If, ast.While, ast.IfExp)):
                        branches.append({'text': compact(segment(item.test), 160),
                                         'evidence': evidence(path, side, item.test.lineno, item.test.end_lineno)})
                    if isinstance(item, (ast.Return, ast.Yield, ast.YieldFrom)):
                        returns.append({'text': compact(segment(item), 180), 'evidence': ref})
                definitions[name] = {
                    'name': name, 'kind': 'class' if isinstance(node, ast.ClassDef) else 'function',
                    'line': start, 'endLine': node.end_lineno,
                    'doc': compact(ast.get_docstring(node), 260),
                    'docSentences': re.split(r'(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?])\s+(?=[A-Z])',
                                            compact(ast.get_docstring(node), 4000)),
                    'calls': unique(calls), 'assertions': assertions, 'branches': branches, 'returns': returns,
                    'examples': table_examples(node, path, side, segment) if is_test(path) and node.name.startswith('test') else [],
                    'evidence': evidence(path, side, start, node.end_lineno),
                    'signature': compact(ast.unparse(node.args), 180) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else '',
                    'signatureEvidence': evidence(path, side, node.lineno, max(node.lineno, node.body[0].lineno - 1)),
                }
                visit(node, name + '.')
            else:
                visit(node, prefix)
    visit(tree)
    return definitions, None


def intersects(ref, lines):
    return any(ref['line'] <= line <= ref.get('endLine', ref['line']) for line in lines)


def read_side(store, review, file, side):
    path = (file.get('oldPath') or file['path']) if side == 'base' else file['path']
    if file.get('excluded') or file.get('binary'):
        return '', path, None
    if (side == 'base' and file['status'] == 'added') or (side == 'head' and file['status'] == 'deleted'):
        return '', path, None
    root = store.directory / 'snapshots' / review[side + 'Sha'] / 'source'
    candidate = safe_source(root, path)
    if candidate is None:
        return '', path, 'Pinned source is unavailable for this side.'
    try:
        raw = candidate.read_bytes()
        if len(raw) > MAX_FILE_BYTES or b'\0' in raw:
            return '', path, 'Source cannot be analyzed because of size or binary content.'
        return raw.decode('utf-8'), path, None
    except (OSError, UnicodeError):
        return '', path, 'Pinned source could not be read as UTF-8.'


def prose_with_evidence(text, changed, path, side):
    # Author explanations are restricted to Python comments and actual docstrings.
    comments = {i for i, line in enumerate(text.splitlines(), 1) if line.lstrip().startswith('#')}
    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                    comments.update(range(body[0].lineno, body[0].end_lineno + 1))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        pass
    selected = changed & comments
    groups, current = [], []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith('#'):
            stripped = stripped[1:].strip()
        if stripped.startswith(('"""', "'''")):
            stripped = stripped[3:]
        if stripped.endswith(('"""', "'''")):
            stripped = stripped[:-3]
        if number in selected and stripped:
            current.append((number, stripped))
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    for group in groups:
        value = compact(' '.join(line for _, line in group), 220)
        if len(value) > 25:
            return {'text': value, 'evidence': [evidence(path, side, group[0][0], group[-1][0])]}
    return None


def list_names(names, limit=3):
    names = list(dict.fromkeys(names))
    shown = ', '.join(names[:limit])
    return shown + (f' and {len(names) - limit} more' if len(names) > limit else '')


def watch(identity, title, detail, refs=None, severity='review'):
    return {'id': identity, 'kind': 'reading-aid', 'severity': severity, 'title': title, 'detail': detail, 'evidence': refs or []}


def explain_hunk(hunk, index, pairs, sides, role, parse_failed, refs):
    """Describe this block's changed expressions, rather than the whole file."""
    changed = {
        side: {line[prefix + 'Line'] for line in hunk['lines'] if line['kind'] == kind}
        for side, prefix, kind in (('base', 'old', 'removed'), ('head', 'new', 'added'))
    }
    observations, checks = [], []
    for name, pair in pairs.items():
        before, after = pair['base'], pair['head']
        if not any(node and intersects(node['evidence'], changed[side]) for side, node in pair.items()):
            continue
        if parse_failed:
            continue
        if before and after and before['signature'] != after['signature'] and any(
                intersects(node['signatureEvidence'], changed[side]) for side, node in pair.items()):
            observations.append(f'Changes {name}() parameters from ({before["signature"]}) to ({after["signature"]}).')
            checks.append(watch(f'hunk-{index}-parameters-{name}', 'Check callers with these arguments',
                                'Confirm the supported call forms still match the changed signature.',
                                [before['signatureEvidence'], after['signatureEvidence']], 'attention'))
        old_calls = {item['name']: item for item in (before or {}).get('calls', [])}
        new_calls = {item['name']: item for item in (after or {}).get('calls', [])}
        added = [item for key, item in new_calls.items() if key not in old_calls and intersects(item['evidence'], changed['head'])]
        removed = [item for key, item in old_calls.items() if key not in new_calls and intersects(item['evidence'], changed['base'])]
        if role == 'implementation' and (added or removed):
            parts = []
            if added:
                parts.append('adds calls to ' + list_names([item['name'] + '()' for item in added], 3))
            if removed:
                parts.append('removes calls to ' + list_names([item['name'] + '()' for item in removed], 3))
            observations.append(f'In {name}(), this block ' + ' and '.join(parts) + '.')
        old_conditions = {item['text'] for item in (before or {}).get('branches', [])}
        added_conditions = [item for item in (after or {}).get('branches', [])
                            if item['text'] not in old_conditions and intersects(item['evidence'], changed['head'])]
        if role == 'implementation' and added_conditions:
            conditions = ', '.join('`' + item['text'] + '`' for item in added_conditions[:2])
            observations.append(f'Adds explicit conditions in {name}(): {conditions}.')
            checks.append(watch(f'hunk-{index}-conditions-{name}', 'Exercise both outcomes',
                                f'Check the inputs that make {conditions} true and false, including boundary values.',
                                [item['evidence'] for item in added_conditions[:2]]))
        old_returns = [item for item in (before or {}).get('returns', []) if intersects(item['evidence'], changed['base'])]
        new_returns = [item for item in (after or {}).get('returns', []) if intersects(item['evidence'], changed['head'])]
        if role == 'implementation' and old_returns and new_returns and old_returns[0]['text'] != new_returns[0]['text']:
            observations.append(f'In {name}(), `{old_returns[0]["text"]}` becomes `{new_returns[0]["text"]}`.')
        if role == 'tests':
            old_assertions = [item for item in (before or {}).get('assertions', []) if intersects(item['evidence'], changed['base'])]
            new_assertions = [item for item in (after or {}).get('assertions', []) if intersects(item['evidence'], changed['head'])]
            if new_assertions:
                observations.append(f'{name}(): ' + new_assertions[0]['description'])
            elif old_assertions:
                observations.append(f'Removes the written check `{old_assertions[0]["text"]}` from {name}().')
            if old_assertions:
                checks.append(watch(f'hunk-{index}-expectations-{name}', 'Check the changed expectation',
                                    'Confirm the old expectation is deliberately replaced or its behavior remains checked elsewhere.',
                                    [item['evidence'] for item in (old_assertions + new_assertions)[:2]]))
        if before and not after:
            observations.append(f'Removes the definition {name}().')
            if role == 'implementation':
                checks.append(watch(f'hunk-{index}-removal-{name}', 'Check remaining callers',
                                    'Confirm no supported caller still depends on this definition.',
                                    [before['evidence']], 'attention'))
    if not observations:
        # A comment can explain intent, but is attributed to its author.
        prose = prose_with_evidence(sides['head']['text'], changed['head'], sides['head']['path'], 'head') \
            if sides['head']['path'].endswith(('.py', '.pyi')) else None
        if prose:
            observations.append('Author explanation: ' + prose['text'])
        else:
            added = [line['text'].strip() for line in hunk['lines'] if line['kind'] == 'added' and line['text'].strip()]
            removed = [line['text'].strip() for line in hunk['lines'] if line['kind'] == 'removed' and line['text'].strip()]
            old, new = compact(' '.join(removed), 150), compact(' '.join(added), 150)
            if old and new:
                observations.append(f'Changes `{old}` to `{new}`.')
            elif new:
                observations.append(f'Adds {"written test data or setup" if role == "tests" else "source"}: `{new}`.')
            elif old:
                observations.append(f'Removes {"written test data or setup" if role == "tests" else "source"}: `{old}`.')
            else:
                observations.append('Changes whitespace in this block.')
    selected = unique(observations)[:2]
    summary = selected[0]
    if len(selected) > 1 and len(summary) + len(selected[1]) < 519:
        summary += ' ' + selected[1]
    return {'index': index, 'heading': hunk['heading'], 'evidence': refs,
            'summary': None, 'syntaxSummary': compact(summary, 520), 'watchpoints': []}


def file_dossier(store, review, file):
    path, role = file['path'], role_for(file['path'])
    detail = store.diff(path, review['id'])
    changed = {
        'base': {line['oldLine'] for h in detail['hunks'] for line in h['lines'] if line['kind'] == 'removed'},
        'head': {line['newLine'] for h in detail['hunks'] for line in h['lines'] if line['kind'] == 'added'},
    }
    sides, limits, reference_notes, parsed, parse_failed = {}, [], [], {}, set()
    for side in ('base', 'head'):
        text, side_path, error = read_side(store, review, file, side)
        sides[side] = {'text': text, 'path': side_path}
        if error:
            limits.append(f'{side.capitalize()}: {error}')
        if text and path.endswith(('.py', '.pyi')):
            parsed[side], error = parse_python(text, side_path, side)
            if error:
                parse_failed.add(side)
                limits.append(f'{side.capitalize()}: {error}')
        else:
            parsed[side] = {}
    hunks, refs = [], []
    for index, hunk in enumerate(detail['hunks']):
        hunk_refs = []
        for side, prefix in (('base', 'old'), ('head', 'new')):
            lines = [line[prefix + 'Line'] for line in hunk['lines'] if line['kind'] == ('removed' if side == 'base' else 'added')]
            if lines:
                hunk_refs.append(evidence(sides[side]['path'], side, min(lines), max(lines)))
        refs.extend(hunk_refs)
        hunks.append({'index': index, 'heading': hunk['heading'], 'evidence': hunk_refs})
    definitions, tests, assertions, facts, watchpoints = [], [], [], [], []
    pairs = {}
    for name in dict.fromkeys([*parsed['head'], *parsed['base']]):
        pair = {side: parsed[side].get(name) for side in ('base', 'head')}
        if not any(node and intersects(node['evidence'], changed[side]) for side, node in pair.items()):
            continue
        # Class changes contain their methods; prefer the method-level explanation.
        node = pair['head'] or pair['base']
        if node['kind'] == 'class' and any(key.startswith(name + '.') for key in parsed['head'] | parsed['base']):
            continue
        change = 'modified' if all(pair.values()) else 'added' if pair['head'] else 'deleted'
        uncertain_definition = any(pair[side] is None and side in parse_failed for side in ('base', 'head'))
        if uncertain_definition:
            # A failed parse is not evidence that a definition was added/deleted.
            change = 'modified'
        definitions.append({'name': name, 'kind': node['kind'], 'change': change, 'evidence': node['evidence']})
        pairs[name] = pair
        if role == 'tests' and node['kind'] == 'function' and name.rsplit('.', 1)[-1].startswith('test'):
            tests.append({'name': name, 'change': change, 'evidence': node['evidence']})
            if pair['head']:
                test_lines = {line for line in changed['head'] if node['line'] <= line <= node['endLine']}
                pair['head']['testExplanation'] = prose_with_evidence(sides['head']['text'], test_lines, path, 'head')
        for side, definition in pair.items():
            if not definition:
                continue
            for assertion in definition['assertions']:
                touched = intersects(assertion['evidence'], changed[side])
                if touched or (side == 'head' and role == 'tests'):
                    assertions.append({**assertion, 'change': ('added' if side == 'head' else 'removed') if touched else 'existing'})
        if role != 'implementation' or uncertain_definition:
            continue
        before_calls = {c['name']: c for c in (pair['base'] or {}).get('calls', [])}
        after_calls = {c['name']: c for c in (pair['head'] or {}).get('calls', [])}
        added = [key for key in after_calls if key not in before_calls]
        removed = [key for key in before_calls if key not in after_calls]
        if added or removed:
            text = f'{name}() '
            text += 'now calls ' + list_names(added) if added else 'no longer calls ' + list_names(removed)
            if added and removed:
                text += '; calls to ' + list_names(removed) + ' are removed'
            call_refs = [after_calls[key]['evidence'] for key in added[:3]] + [before_calls[key]['evidence'] for key in removed[:3]]
            facts.append({'text': text + '.', 'evidence': call_refs})
        elif change in {'added', 'deleted'}:
            facts.append({'text': f'{"Adds" if change == "added" else "Removes"} {name}().',
                          'evidence': [node['evidence']]})
        else:
            old_returns = {item['text']: item for item in (pair['base'] or {}).get('returns', [])}
            new_returns = {item['text']: item for item in (pair['head'] or {}).get('returns', [])}
            removed_returns = [old_returns[key] for key in old_returns if key not in new_returns]
            added_returns = [new_returns[key] for key in new_returns if key not in old_returns]
            if removed_returns and added_returns:
                facts.append({'text': f'{name}() changes `{removed_returns[0]["text"]}` to `{added_returns[0]["text"]}`.',
                              'evidence': [removed_returns[0]['evidence'], added_returns[0]['evidence']]})
        if all(pair.values()) and pair['base']['signature'] != pair['head']['signature']:
            watchpoints.append(watch('signature-' + name, f'Check callers of {name}()',
                                     f'The parameters change from ({pair["base"]["signature"]}) to ({pair["head"]["signature"]}). Confirm existing callers remain valid.',
                                     [pair['base']['evidence'], pair['head']['evidence']], 'attention'))
        if change == 'deleted':
            watchpoints.append(watch('deleted-' + name, f'Confirm removal of {name}()',
                                     'This definition is removed. Check callers and any public contract before accepting the removal.',
                                     [node['evidence']], 'attention'))
        before_branches = {b['text'] for b in (pair['base'] or {}).get('branches', [])}
        new_branches = [b for b in (pair['head'] or {}).get('branches', []) if b['text'] not in before_branches]
        if new_branches:
            watchpoints.append(watch('branches-' + name, f'Check the new cases in {name}()',
                                     'The changed conditions include ' + list_names([b['text'] for b in new_branches], 2) +
                                     '. Check both outcomes and the boundary inputs; static analysis does not establish that tests exercise them.',
                                     [b['evidence'] for b in new_branches[:2]]))
    # Non-Python indexed definitions still have trustworthy changed ranges, but
    # their method bodies are not interpreted by this guide.
    if not path.endswith(('.py', '.pyi')):
        definitions = [{k: n[k] for k in ('name', 'kind', 'change')} |
                       {'evidence': evidence((file.get('oldPath') or path) if n['side'] == 'base' else path,
                                             n['side'], n['line'], n['endLine'])}
                       for n in file.get('changedSymbols', [])]
    if len(definitions) > MAX_DEFINITIONS:
        limits.append(f'Definition details are capped at {MAX_DEFINITIONS}.')
    if len(assertions) > MAX_ASSERTIONS:
        limits.append(f'Assertion details are capped at {MAX_ASSERTIONS}.')
    if file.get('excluded') or file.get('binary'):
        limits.append(file.get('excluded') or 'Binary file; source and diff analysis are unavailable.')
    if detail['truncated'] or file.get('diffTruncated'):
        limits.append('The diff is truncated; some changed lines are omitted.')
    related = []
    for edge in review['graph']['edges']:
        source, target = edge['source'].removeprefix('file:'), edge['target'].removeprefix('file:')
        if target == path and is_test(source) and edge['kind'] in {'calls', 'imports', 'inherits'}:
            for ref in edge.get('evidence', []):
                related.append({'path': source, 'relationship': edge['kind'], 'change': edge['change'], 'evidence': ref})
    related = unique(related)
    if len(related) > MAX_RELATED_TESTS:
        reference_notes.append(f'Showing {MAX_RELATED_TESTS} related test references; additional call or import locations are omitted from this list.')
    explanations = {side: prose_with_evidence(value['text'], changed[side], value['path'], side)
                    if path.endswith(('.py', '.pyi')) else None for side, value in sides.items()}
    before = side_description(parsed['base'], pairs, 'base', explanations['base'], file)
    after = side_description(parsed['head'], pairs, 'head', explanations['head'], file)
    if 'base' in parse_failed:
        before = 'The base source could not be parsed; inspect the pinned diff for this side.'
    if 'head' in parse_failed:
        after = 'The head source could not be parsed; inspect the pinned diff for this side.'
    if file.get('excluded') or file.get('binary'):
        summary = f'This {file["status"]} file cannot be inspected in Atlas. ' + (file.get('excluded') or 'Binary source has no text preview.')
    elif file['status'] == 'renamed' and not detail['hunks']:
        summary = f'Moves {file.get("oldPath")} to {path}; the file contents are unchanged.'
    elif not detail['hunks'] and not limits:
        summary = 'No text change is visible in this file; inspect the rename or file metadata in the original Git comparison.'
    elif role == 'tests':
        added_tests = [t['name'].rsplit('.', 1)[-1].removeprefix('test_').replace('_', ' ') for t in tests if t['change'] == 'added']
        changed_tests = [t['name'].rsplit('.', 1)[-1].removeprefix('test_').replace('_', ' ') for t in tests if t['change'] == 'modified']
        removed_tests = [t['name'].rsplit('.', 1)[-1].removeprefix('test_').replace('_', ' ') for t in tests if t['change'] == 'deleted']
        sentences = []
        if added_tests:
            sentences.append('Adds test cases for ' + list_names(added_tests) + '.')
        if changed_tests:
            sentences.append('Updates test cases for ' + list_names(changed_tests) + '.')
        if removed_tests:
            sentences.append('Removes test cases for ' + list_names(removed_tests) + '.')
        summary = ' '.join(sentences) or 'Changes test setup or test data; no named test change was identified.'
        if removed_tests:
            watchpoints.append(watch('removed-tests', 'Check the removed test cases',
                                     'Confirm that the removed checks are obsolete or retained elsewhere.', [t['evidence'] for t in tests if t['change'] == 'deleted'], 'attention'))
        watchpoints.append(watch('test-intent', 'Check what these tests establish',
                                 'Review the inputs and expected results. Changed test code is evidence of intended checks; Atlas has not run it.', [t['evidence'] for t in tests[:2]] or refs[:1]))
    elif role == 'documentation':
        summary = document_summary(detail['hunks'], file)
        watchpoints.append(watch('docs-behavior', 'Check the documented contract',
                                 'Confirm that examples and behavior claims match the implementation.', refs[:2]))
    elif facts:
        explanation = explanations.get('head')
        summary = ('Author explanation: ' + explanation['text'] + ' ' + facts[-1]['text']) if explanation else ' '.join(f['text'] for f in facts[:2])
    elif definitions:
        summary = f'{"Removes" if file["status"] == "deleted" else "Changes"} ' + list_names([d['name'] for d in definitions]) + '. Review the changed expressions for their behavioral effect.'
    else:
        summary = f'Changes {role} content in {PurePosixPath(path).name}. The visible diff is the available evidence.'
    if role == 'implementation' and not related:
        watchpoints.append(watch('test-gap', 'Find the tests for this behavior',
                                 'No direct test call or import to this file is present in the displayed static graph. This is a gap in the available evidence, not proof that tests are missing.', refs[:1]))
    if role == 'implementation' and not watchpoints:
        watchpoints.append(watch('contract', 'Check the changed contract',
                                 'Confirm inputs, outputs, and error handling in the changed lines, then inspect the linked callers.', refs[:2]))
    if role == 'configuration':
        watchpoints.append(watch('config', 'Check configuration consumers',
                                 'Confirm that consumers accept these values and that defaults remain appropriate.', refs[:2]))
    analysis_kind = 'python-syntax' if path.endswith(('.py', '.pyi')) and any(parsed.values()) else 'diff-only'
    if analysis_kind == 'diff-only' and not limits and role == 'implementation':
        limits.append('Behavior is not interpreted for this language; explanations use indexed definitions and visible diff lines.')
    if limits:
        if all('capped at' in limit for limit in limits):
            watchpoints.append(watch('sampled', 'Some evidence is sampled', ' '.join(limits)))
        else:
            watchpoints.insert(0, watch('incomplete', 'Inspect the unavailable details', ' '.join(limits), [], 'attention'))
    hunks = [explain_hunk(raw, index, pairs, sides, role, parse_failed, hunks[index]['evidence'])
             for index, raw in enumerate(detail['hunks'])]
    return {
        'path': path, 'oldPath': file.get('oldPath'), 'status': file['status'], 'role': role,
        'summary': compact(summary, 480), 'before': before, 'after': after,
        'evidence': unique(refs)[:12], 'hunks': hunks,
        'changedDefinitions': definitions[:MAX_DEFINITIONS], 'tests': tests[:MAX_DEFINITIONS],
        'assertions': assertions[:MAX_ASSERTIONS], 'relatedTests': related[:MAX_RELATED_TESTS],
        'watchpoints': watchpoints[:6],
        'analysis': {'kind': analysis_kind, 'complete': not limits, 'limitations': limits + reference_notes,
                     'referenceNotes': reference_notes},
        '_pairs': pairs, '_facts': facts, '_explanations': explanations, '_changed': changed, '_parse_failed': parse_failed,
    }


def side_description(parsed, pairs, side, explanation, file):
    if (side == 'base' and file['status'] == 'added') or (side == 'head' and file['status'] == 'deleted'):
        return 'This file is absent from the pinned snapshot.'
    if explanation:
        return 'Author explanation: ' + explanation['text']
    names = [name for name in pairs if pairs[name].get(side)]
    if names:
        first = parsed[names[0]]
        calls = [c['name'] for c in first['calls']]
        return first['name'] + ('() calls ' + list_names(calls, 4) + '.' if calls else '() is defined here.')
    return 'See the pinned ' + side + ' diff lines for this file.'


def document_summary(hunks, file):
    added = [line['text'].lstrip('# ').strip() for h in hunks for line in h['lines'] if line['kind'] == 'added' and line['text'].strip()]
    removed = [line['text'].lstrip('# ').strip() for h in hunks for line in h['lines'] if line['kind'] == 'removed' and line['text'].strip()]
    content = compact(' '.join(added or removed), 180)
    action = 'Removes' if file['status'] == 'deleted' else 'Updates'
    return f'{action} documentation' + (f': “{content}”' if content else '.')


def flow(dossiers, side):
    nodes, omitted = [], False
    other = 'base' if side == 'head' else 'head'
    for file in dossiers:
        if file['role'] != 'implementation':
            continue
        for name, pair in file['_pairs'].items():
            definition = pair[side]
            if not definition:
                continue
            if len(nodes) >= MAX_FLOW_NODES:
                omitted = True
                continue
            identity = f'def:{file["path"]}:{name}'
            connections = []
            node = {'id': identity, 'label': name + '()', 'path': definition['evidence']['path'],
                    'side': side, 'line': definition['line'], 'kind': definition['kind'],
                    'connections': connections, 'evidence': [definition['evidence']]}
            nodes.append(node)
            other_calls = {call['name'] for call in (pair[other] or {}).get('calls', [])}
            calls = list({call['name']: call for call in definition['calls']}.values())
            calls.sort(key=lambda call: call['name'] in other_calls)
            for call in calls[:3]:
                if len(nodes) >= MAX_FLOW_NODES:
                    omitted = True
                    break
                target = identity + ':call:' + call['name']
                change = 'unchanged' if call['name'] in other_calls else 'added' if side == 'head' else 'removed'
                connections.append({'target': target, 'label': 'calls', 'change': change, 'evidence': [call['evidence']]})
                nodes.append({'id': target, 'label': call['name'] + '()', 'path': call['evidence']['path'],
                              'side': side, 'line': call['evidence']['line'], 'kind': 'call-expression',
                              'connections': [], 'evidence': [call['evidence']]})
            if len(calls) > 3:
                omitted = True
    if not nodes:
        for file in dossiers[:8]:
            ref = next((ref for ref in file['evidence'] if ref['side'] == side), None)
            if ref:
                nodes.append({'id': 'file:' + file['path'], 'label': file['path'], 'path': ref['path'],
                              'side': side, 'line': ref['line'], 'kind': file['role'],
                              'connections': [], 'evidence': [ref]})
    return nodes, omitted


def test_title(name):
    parts = name.split('.')
    scenario = parts[-1].removeprefix('test_').replace('_', ' ')
    if len(parts) > 1:
        context = re.sub(r'(?:Tests?|TestCase)$', '', parts[-2])
        context = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', context).replace('_', ' ')
        scenario = f'{context}: {scenario}'
    return scenario[:1].upper() + scenario[1:]


def coverage_details(files, gaps, source_caps):
    """Turn written test expectations into reviewable scenarios and open checks."""
    scenarios, checks = [], []
    implementation = [file for file in files if file['role'] in {'implementation', 'configuration'}]
    for file in files:
        if file['role'] != 'tests':
            continue
        for test in file['tests']:
            if test['change'] == 'deleted':
                continue
            pair = file['_pairs'].get(test['name'], {})
            definition = pair.get('head')
            if not definition:
                continue
            assertions = sorted(definition['assertions'], key=lambda item: not intersects(item['evidence'], file['_changed']['head']))
            if not assertions:
                checks.append({
                    'id': f'no-expectation:{file["path"]}:{test["name"]}',
                    'title': f'Find the expectation in {test["name"]}()',
                    'detail': 'No direct assertion was identified in this changed test. Check whether a helper supplies it; the test name alone does not establish the behavior checked.',
                    'evidence': [test['evidence']],
                })
                continue
            old_examples = {(item['input'], item['expected']) for item in (pair.get('base') or {}).get('examples', [])}
            examples = [{**item, 'change': 'unverified' if 'base' in file['_parse_failed'] else
                         'existing' if (item['input'], item['expected']) in old_examples else 'added'}
                        for item in definition.get('examples', [])]
            examples.sort(key=lambda item: item['change'] != 'added')
            explanation = definition.get('testExplanation')
            detail = ('Test author: ' + explanation['text']) if explanation else assertions[0]['description']
            refs = ([*explanation['evidence']] if explanation else []) + [assertions[0]['evidence']]
            title = test_title(test['name'])
            # Generic test names such as "basic" say little. In that case an
            # explicitly attributed author explanation is a better visible label.
            if explanation and test['name'].rsplit('.', 1)[-1] in {'test_basic', 'test_parsing', 'test_values'}:
                title = compact(explanation['text'], 100).rstrip('.')
            scenarios.append({
                'id': f'{file["path"]}:{test["name"]}', 'title': title,
                'detail': detail, 'test': test['name'], 'path': file['path'], 'change': test['change'],
                'checks': [{'text': item['description'], 'evidence': item['evidence']} for item in assertions[:6]],
                'examples': [{**item, 'input': item['input'][:240] + ('…' if len(item['input']) > 240 else ''),
                             'expected': item['expected'][:200] + ('…' if len(item['expected']) > 200 else '')} for item in examples[:4]],
                'evidence': unique(refs),
                'limits': ([f'Showing 6 of {len(assertions)} written assertions.'] if len(assertions) > 6 else []) +
                          ([f'Showing 4 of {len(examples)} literal table examples; changed rows are shown first.'] if len(examples) > 4 else []),
            })
    for file in implementation:
        if file['_parse_failed']:
            continue
        for name, pair in file['_pairs'].items():
            before, after = pair['base'], pair['head']
            if not after:
                if before and file['analysis']['complete']:
                    checks.append({
                        'id': f'removed-definition:{file["path"]}:{name}', 'title': f'Check the removal of {name}()',
                        'detail': 'This definition is removed. Confirm remaining tests check the replacement behavior or that the old contract is deliberately retired.',
                        'evidence': [before['evidence']],
                    })
                continue
            before_conditions = {item['text'] for item in (before or {}).get('branches', [])}
            new_conditions = [item for item in after['branches'] if item['text'] not in before_conditions]
            if new_conditions:
                checks.append({
                    'id': f'conditions:{file["path"]}:{name}', 'title': f'Check both outcomes in {name}()',
                    'detail': 'New conditions: ' + '; '.join('`' + item['text'] + '`' for item in new_conditions[:5]) +
                              '. Confirm the test inputs exercise both outcomes of each condition. Written assertions do not establish branch coverage.',
                    'evidence': [item['evidence'] for item in new_conditions[:5]],
                })
            if before and before['signature'] != after['signature']:
                checks.append({
                    'id': f'parameters:{file["path"]}:{name}', 'title': f'Check caller compatibility for {name}()',
                    'detail': f'Parameters change from ({before["signature"]}) to ({after["signature"]}). Find test cases for the supported call forms and any deliberately rejected forms.',
                    'evidence': [before['evidence'], after['evidence']],
                })
            if not new_conditions:
                before_calls = {item['name'] for item in (before or {}).get('calls', [])}
                added_calls = [item for item in after['calls'] if item['name'] not in before_calls]
                removed_calls = [item for item in (before or {}).get('calls', []) if item['name'] not in {call['name'] for call in after['calls']}]
                if added_calls or removed_calls:
                    changes = []
                    if added_calls:
                        changes.append('now calls ' + list_names([item['name'] for item in added_calls], 3))
                    if removed_calls:
                        changes.append('no longer calls ' + list_names([item['name'] for item in removed_calls], 3))
                    checks.append({
                        'id': f'call-behavior:{file["path"]}:{name}', 'title': f'Check the observable result of {name}()',
                        'detail': f'{name}() ' + '; '.join(changes) + '. Confirm the linked tests assert the resulting behavior, including inputs whose result should stay the same.',
                        'evidence': [item['evidence'] for item in (added_calls + removed_calls)[:5]],
                    })
                else:
                    old_returns = {item['text'] for item in (before or {}).get('returns', [])}
                    new_returns = [item for item in after['returns'] if item['text'] not in old_returns]
                    if new_returns:
                        checks.append({
                            'id': f'result:{file["path"]}:{name}', 'title': f'Check the result of {name}()',
                            'detail': 'The changed result includes `' + new_returns[0]['text'] + '`. Confirm a test asserts this outcome and the relevant existing behavior.',
                            'evidence': [new_returns[0]['evidence']],
                        })
    limited = bool(gaps or source_caps or any(not file['analysis']['complete'] for file in files))
    if not implementation:
        assessment = {
            'status': 'no-implementation-change',
            'summary': 'No implementation change was identified in this comparison.',
            'detail': 'Review whether the changed expectations preserve the intended contract. Test execution remains unverified.',
        }
    elif limited or not scenarios:
        assessment = {
            'status': 'limited-evidence', 'summary': 'The available test evidence is not enough to establish sufficiency.',
            'detail': ' '.join(gaps[:2]) if gaps else 'Some source is uninspected or no direct assertions were identified in the changed tests. Existing tests or helper assertions may supply the missing evidence.',
        }
    else:
        assessment = {
            'status': 'review-required', 'summary': 'Concrete test expectations are present; sufficiency still needs review.',
            'detail': 'Check the changed implementation against the scenarios and specific checks below. Passing results and branch coverage have not been established.',
        }
    return scenarios[:MAX_SCENARIOS], assessment, checks, len(scenarios) > MAX_SCENARIOS


def comprehension_question(prompt, correct, alternatives, explanation, refs, context):
    """Shuffle source-grounded alternatives deterministically; fingerprint content."""
    texts, literals = [], []
    for value in dict.fromkeys([correct, *alternatives]):
        try:
            literal = ast.literal_eval(value) if len(value) <= 320 else None
            has_literal = len(value) <= 320
        except (SyntaxError, ValueError, TypeError, RecursionError, MemoryError):
            literal, has_literal = None, False
        if has_literal and any(literal == previous for previous in literals):
            continue
        texts.append(value)
        if has_literal:
            literals.append(literal)
        if len(texts) == 3:
            break
    if len(texts) < 2:
        return None
    choices = [{'id': 'option-' + hashlib.sha256(value.encode()).hexdigest()[:12], 'text': value} for value in texts]
    question = {
        'kind': 'choice', 'prompt': prompt, 'context': context, 'choices': choices,
        'correctChoiceId': choices[0]['id'], 'explanation': explanation, 'evidence': unique(refs),
    }
    fingerprint = hashlib.sha256(json.dumps(question, sort_keys=True).encode()).hexdigest()
    question['id'] = 'q-' + fingerprint[:16]
    question['choices'].sort(key=lambda choice: hashlib.sha256((fingerprint + choice['id']).encode()).hexdigest())
    return question


def comprehension_questions(files, scenarios):
    """Ask about observable source changes, without grading invented behavior."""
    implementation, test_questions = [], []
    for file in files:
        if file['role'] != 'implementation' or file['_parse_failed']:
            continue
        for name, pair in file['_pairs'].items():
            before, after = pair['base'], pair['head']
            if not before or not after:
                continue
            old_doc = [item for item in before['docSentences'] if item and item not in after['docSentences']]
            new_doc = [item for item in after['docSentences'] if item and item not in before['docSentences']]
            context = file['path'] + ' · Compare the pinned base and head source.'
            prompt, correct, alternatives, explanation, refs = '', '', [], '', []
            if before['signature'] != after['signature']:
                prompt = f'Which parameter list must callers of {name}() now satisfy?'
                correct, alternatives = f'({after["signature"]})', [f'({before["signature"]})']
                explanation = f'The signature changes from ({before["signature"]}) to ({after["signature"]}). Call sites using the old form need inspection.'
                refs = [before['signatureEvidence'], after['signatureEvidence']]
            elif old_doc and new_doc and len(old_doc[0]) <= 320 and len(new_doc[0]) <= 320:
                prompt = f'Which contract statement is introduced in the docstring for {name}()?'
                correct, alternatives = new_doc[0], [old_doc[0]]
                explanation = 'The head docstring states: ' + new_doc[0] + ' This is the written contract; the implementation and tests still need to establish it.'
                refs = [before['evidence'], after['evidence']]
            else:
                old_returns = {item['text']: item for item in before['returns']}
                new_returns = {item['text']: item for item in after['returns']}
                removed_returns = [item for text, item in old_returns.items() if text not in new_returns]
                added_returns = [item for text, item in new_returns.items() if text not in old_returns]
                old_calls = {item['name']: item for item in before['calls']}
                new_calls = {item['name']: item for item in after['calls']}
                added_calls = [item for key, item in new_calls.items() if key not in old_calls]
                removed_calls = [item for key, item in old_calls.items() if key not in new_calls]
                if len(removed_returns) == 1 and len(added_returns) == 1:
                    prompt = f'Which result expression is now written in {name}() at the changed return site?'
                    correct, alternatives = added_returns[0]['text'], [removed_returns[0]['text']]
                    explanation = f'The base uses `{removed_returns[0]["text"]}`; the head uses `{added_returns[0]["text"]}`. Follow the cited expressions to assess the observable result.'
                    refs = [removed_returns[0]['evidence'], added_returns[0]['evidence']]
                elif added_calls or removed_calls:
                    def call_change(added, removed):
                        parts = []
                        if added:
                            parts.append('Introduces ' + list_names([item['name'] + '()' for item in added], 3))
                        if removed:
                            parts.append('removes ' + list_names([item['name'] + '()' for item in removed], 3))
                        sentence = '; '.join(parts)
                        return sentence[:1].upper() + sentence[1:] + '.'
                    prompt = f'Which call-site change occurs inside {name}()?'
                    correct = call_change(added_calls, removed_calls)
                    alternatives = [call_change(removed_calls, added_calls), 'The set of call expressions in this definition is unchanged.']
                    explanation = correct + ' These are source call expressions, not proof of runtime execution or resolved targets.'
                    refs = [item['evidence'] for item in (added_calls + removed_calls)[:6]]
                else:
                    old_conditions = {item['text']: item for item in before['branches']}
                    new_conditions = [item for item in after['branches'] if item['text'] not in old_conditions]
                    removed_conditions = [item for item in before['branches'] if item['text'] not in {new['text'] for new in after['branches']}]
                    if new_conditions:
                        prompt = f'Which condition now appears explicitly in {name}()?'
                        correct = new_conditions[0]['text']
                        alternatives = [item['text'] for item in removed_conditions[:2]] or ['This definition has no conditional branch.']
                        explanation = 'The changed source explicitly tests `' + correct + '`. Check both outcomes against the intended behavior.'
                        refs = [new_conditions[0]['evidence'], *[item['evidence'] for item in removed_conditions[:2]]]
            if prompt:
                question = comprehension_question(prompt, correct, alternatives, explanation, refs, context)
                if question:
                    implementation.append(question)
    for scenario in scenarios:
        file = next(item for item in files if item['path'] == scenario['path'])
        pair = file['_pairs'].get(scenario['test'], {})
        before, after = pair.get('base'), pair.get('head')
        if not after or file['_parse_failed']:
            continue
        examples = after.get('examples', [])
        old_examples = (before or {}).get('examples', [])
        old_values = {(item['input'], item['expected']) for item in old_examples}
        changed_examples = [item for item in examples if (item['input'], item['expected']) not in old_values]
        context = file['path'] + ' · Answer from the written test expectation; execution is unverified.'
        question = None
        if changed_examples:
            example = changed_examples[0]
            if len(example['input']) <= 180 and len(example['expected']) <= 160:
                alternatives = [item['expected'] for item in old_examples if item['input'] == example['input']] + [
                    item['expected'] for item in examples if item['expected'] != example['expected']]
                alternatives.append('No expected result is written for this input.')
                question = comprehension_question(
                    f'In {scenario["test"]}(), what result is expected for {example["input"]}?',
                    example['expected'], alternatives,
                    f'This table row pairs {example["input"]} with the expected value {example["expected"]}. The equality assertion in the test checks the pair.',
                    [example['evidence'], *scenario['evidence'][:1]], context)
        if not question:
            checks = sorted(after['assertions'], key=lambda item: not intersects(item['evidence'], file['_changed']['head']))
            for check in checks:
                expectation = check.get('expectation')
                if not expectation or not expectation['literal']:
                    continue
                alternatives = [item['expectation']['expected'] for item in (before or {}).get('assertions', [])
                                if item.get('expectation') and item['expectation']['actual'] == expectation['actual']]
                alternatives += [item['expectation']['expected'] for item in checks
                                 if item.get('expectation') and item['expectation']['literal']]
                alternatives.append('No expected result is written for this expression.')
                question = comprehension_question(
                    f'In {scenario["test"]}(), what result must {expectation["actual"]} equal?',
                    expectation['expected'], alternatives,
                    check['description'] + ' This describes the assertion in source; it does not establish that the test passes.',
                    [check['evidence']], context)
                if question:
                    break
        if question:
            test_questions.append(question)
    # Keep both implementation impact and a concrete expected result represented.
    selected = implementation[:2] + test_questions[:2]
    if len(selected) < 4:
        selected += (implementation[2:] + test_questions[2:])[:4 - len(selected)]
    if not selected:
        file = next((item for item in files if item['evidence']), None)
        if file:
            prompt = f'At the cited change in {file["path"]}, what changes for a consumer, and what behavior should stay the same?'
            explanation = 'Atlas cannot derive a reliable answer for this change. Explain your reading of the cited diff and identify the evidence or missing context that supports it.'
            question = {
                'kind': 'reflection', 'prompt': prompt, 'context': 'This response needs human judgment; it is not automatically graded.',
                'choices': [], 'correctChoiceId': None, 'explanation': explanation, 'evidence': file['evidence'][:2],
            }
            question['id'] = 'q-' + hashlib.sha256(json.dumps(question, sort_keys=True).encode()).hexdigest()[:16]
            selected.append(question)
    return selected[:4]


def build_guide(store, review):
    files = [file_dossier(store, review, file) for file in review['files']]
    priority = {'implementation': 0, 'configuration': 1, 'tests': 2, 'documentation': 3}
    files.sort(key=lambda f: (priority[f['role']], -len(f['_facts']), f['path']))
    limitations = ['Generated from pinned diffs and static syntax; no model or runtime analysis is used.']
    truncated = [key for key, value in review.get('truncated', {}).items() if value]
    source_caps = [key for key in truncated if key in {'files', 'diffs', 'symbols'}]
    reference_caps = [key for key in truncated if key in {'nodes', 'edges', 'evidence'}]
    reference_notes = []
    if source_caps:
        limitations.append('Changed source details are capped (' + ', '.join(source_caps) + '); inspect omitted changes outside this guide.')
    if reference_caps:
        reference_notes.append('The impact graph samples ' + ', '.join(reference_caps) +
                               '. Additional reference locations or connections may be omitted; this does not limit the changed-file diffs or test assertions shown here.')
        limitations.extend(reference_notes)
    if any(not file['analysis']['complete'] for file in files):
        limitations.append('Some files have incomplete analysis. Their file-level limitations must be checked.')
    limitations.extend(w for w in review.get('warnings', []) if w.startswith('Parser warnings:'))
    facts = [fact for file in files for fact in file['_facts']]
    for file in files:
        explanation = file['_explanations'].get('head')
        if explanation and file['role'] == 'implementation':
            facts.insert(0, {'text': 'Author explanation: ' + explanation['text'], 'evidence': explanation['evidence']})
    title = compact(review.get('title'), 180)
    title = re.sub(r'^(?:Fixed|Fixes|Refs|Closes)\s+#\d+\s*(?:--|[-:–—])?\s*', '', title, flags=re.I).strip() or 'Review the proposed change'
    if review.get('url'):
        summary, source = title.rstrip('. ') + '.', 'pr-title'
        intent_note = 'Purpose comes from the PR title. Code observations below are checked against the pinned diff.'
    elif files:
        title, summary, source = 'Review the proposed change', files[0]['summary'], 'diff'
        intent_note = 'This local comparison has no author-provided PR purpose. The summary describes visible changes.'
    else:
        title, summary, source = 'No changes to review', 'The pinned comparison contains no changed files.', 'diff'
        intent_note = 'The base and head comparison contains no diff.'
    test_files = [file for file in files if file['role'] == 'tests']
    implementation = [file for file in files if file['role'] in {'implementation', 'configuration'}]
    changed_tests = [test for file in test_files for test in file['tests']]
    assertions = [assertion for file in test_files for assertion in file['assertions']]
    related = unique([test for file in files for test in file['relatedTests']])
    gaps = []
    if implementation and not test_files:
        gaps.append('No changed test files were found in the available review data. Existing tests may still cover it.')
    for file in implementation:
        if not file['relatedTests']:
            gaps.append(f'{file["path"]}: no direct test reference is present in the displayed graph.')
    if any(test['change'] == 'deleted' for test in changed_tests):
        gaps.append('Some named tests are removed. Confirm equivalent checks remain or explain why they are obsolete.')
    if 'files' in source_caps:
        gaps.append('The changed-file list is capped and may omit tests in additional files.')
    if 'diffs' in source_caps:
        gaps.append('Truncated diffs can omit changed test cases or assertion edits.')
    if 'symbols' in source_caps or any(
            limit.startswith(('Definition details', 'Assertion details'))
            for file in files for limit in file['analysis']['limitations']):
        gaps.append('Some definition or assertion details are capped; inspect the full test diffs for omitted checks.')
    coverage_summary = (f'{len(test_files)} changed test file{"s" if len(test_files) != 1 else ""}; '
                        f'{len(changed_tests)} named test change{"s" if len(changed_tests) != 1 else ""} identified. '
                        'Test execution and measured coverage are unverified.')
    if not implementation and files:
        coverage_summary = ('This comparison changes tests or documentation without an identified implementation change. '
                            'Test execution and measured coverage are unverified.')
    scenarios, assessment, reviewer_checks, scenarios_limited = coverage_details(files, gaps, source_caps)
    scenario_limits = [f'Showing {MAX_SCENARIOS} test scenarios; inspect the remaining changed test definitions.'] if scenarios_limited else []
    before, before_limited = flow(files, 'base')
    after, after_limited = flow(files, 'head')
    if before_limited or after_limited:
        limitations.append('The diagram shows selected definitions and up to three call expressions per definition.')
    watchpoints = [
        watch('test-execution', 'Verify tests at this commit',
              'Atlas has not run tests or imported CI results. Review test results for the displayed head commit before deciding.', severity='attention'),
    ] if files else []
    if gaps and implementation:
        watchpoints.append(watch('coverage-gaps', 'Close the test evidence gaps', ' '.join(gaps[:3]), severity='attention'))
    removed = [definition for file in files for definition in file['changedDefinitions'] if definition['change'] == 'deleted']
    if removed:
        watchpoints.append(watch('removed-contracts', 'Check removed definitions',
                                 'Confirm that callers and users no longer depend on the removed definitions.',
                                 [item['evidence'] for item in removed[:3]], 'attention'))
    if source_caps or any(not file['analysis']['complete'] for file in files):
        watchpoints.append(watch('analysis-limits', 'Review what Atlas could not inspect',
                                 'Some changed source, diff, definition, or assertion details are unavailable or capped. Check the affected files and their stated limitations.', severity='attention'))
    questions = comprehension_questions(files, scenarios)
    for file in files:
        for key in ('_pairs', '_facts', '_explanations', '_changed', '_parse_failed'):
            file.pop(key)
    return {
        'version': VERSION, 'reviewId': review['id'], 'baseSha': review['baseSha'], 'headSha': review['headSha'],
        'brief': {'title': title, 'summary': compact(summary, 420), 'intentSource': source, 'intentNote': intent_note,
                  'behaviorChanges': facts[:6], 'limitations': [] if review.get('url') else [intent_note]},
        'impact': {'label': 'Before and after: static call sites' if any(n['connections'] for n in before + after) else 'Before and after: changed source',
                   'before': before, 'after': after, 'changes': facts[:6],
                   'note': 'Arrows show call expressions present in source, not runtime order, resolved targets, or proof that a path executes. Open an item to inspect its pinned source.'},
        'files': files,
        'coverage': {'testsChanged': len(changed_tests), 'testFiles': [file['path'] for file in test_files],
                     'changedTestNames': changed_tests, 'observedAssertions': assertions,
                     'coveredScenarios': scenarios, 'assessment': assessment, 'reviewerChecks': reviewer_checks,
                     'relatedTests': related, 'execution': 'not-run', 'summary': coverage_summary,
                     'gaps': gaps, 'limitations': ['Changed tests and static references are evidence to inspect, not measured execution coverage or passing results.',
                                                  'Test-related files are identified by conventional names and paths. Static references are to files, not proof of coverage for a changed function.',
                                                  'Scenarios describe written expectations and literal test data, not test execution. Source comments remain the test author’s explanation.',
                                                  *reference_notes, *scenario_limits]},
        'watchpoints': watchpoints, 'questions': questions, 'limitations': limitations,
    }


def get_guide(store, identity=None):
    """Versioned cache is tied to both immutable commits; no fetch or reindex."""
    review = store._review(identity)
    fingerprint = hashlib.sha256((review['baseSha'] + ':' + review['headSha']).encode()).hexdigest()[:16]
    path = store.directory / f'guide-v{VERSION}-{review["id"]}-{fingerprint}.json'
    with store.lock:
        if path.is_file():
            try:
                saved = json.loads(path.read_text())
                if (saved.get('version') == VERSION and saved.get('reviewId') == review['id'] and
                        saved.get('baseSha') == review['baseSha'] and saved.get('headSha') == review['headSha']):
                    return saved
            except (OSError, ValueError):
                pass
        guide = build_guide(store, review)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(guide))
        temporary.replace(path)
        return guide
