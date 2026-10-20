"""Agent-authored map documentation contract; heuristics remain a fallback."""
import sqlite3
from contextlib import closing
from pathlib import Path

from .indexer import MAX_FILE_BYTES, safe_source
from .product import clean_prefix, matches

SECTIONS = ('purpose', 'behavior', 'implementation', 'dependencies', 'testing', 'limitations')


def validate_documentation(area):
    documentation = area.get('documentation')
    if not isinstance(documentation, dict):
        raise ValueError('Generated areas need structured documentation, not just references.')
    for section in SECTIONS:
        value = documentation.get(section)
        if not isinstance(value, str) or not 40 <= len(value.strip()) <= 8000:
            raise ValueError(f'Documentation {section} must explain the area in 40–8000 characters.')
    if len(area.get('summary', '').strip()) < 80:
        raise ValueError('Generated summaries need at least 80 characters of explanatory prose.')


def validate_candidate_documentation(raw, db_path, root):
    project = raw.get('project')
    if not isinstance(project, dict) or not isinstance(project.get('name'), str) or not 1 <= len(project['name'].strip()) <= 160:
        raise ValueError('Generated maps need a project name, overview and documentation.')
    validate_documentation(project)
    with closing(sqlite3.connect(db_path)) as db:
        files = {row[0] for row in db.execute("SELECT path FROM nodes WHERE kind='file'")}
    line_counts = {}

    def citation(item):
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise ValueError('Generated evidence needs repository-relative path and line objects.')
        path = clean_prefix(item['path'])
        line = item.get('line')
        if path not in files or isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise ValueError('Generated evidence must cite an indexed file and positive line.')
        if path not in line_counts:
            source = safe_source(Path(root), path)
            if source is None:
                raise ValueError('Generated evidence source is no longer available.')
            if source.stat().st_size > MAX_FILE_BYTES:
                raise ValueError('Evidence source exceeds the supported source size limit.')
            line_counts[path] = len(source.read_text().splitlines())
        if line > line_counts[path]:
            raise ValueError('Generated evidence line is outside the current source file.')
        return path

    validate_overview(project.get('overview'), {feature['id'] for feature in raw.get('features', [])}, citation)
    groups = [*raw.get('features', []), *raw.get('foundations', [])]
    if not groups:
        raise ValueError('Generated maps need at least one feature or foundation.')
    for group in groups:
        for area in [group, *group.get('capabilities', [])]:
            validate_documentation(area)
            for prefix in area['paths']:
                if not any(matches(path, clean_prefix(prefix)) for path in files):
                    raise ValueError(f'Assigned path has no indexed files: {prefix}')
            evidence = area.get('evidence')
            if not isinstance(evidence, list) or not evidence:
                raise ValueError('Every generated area needs source evidence with line numbers.')
            for proof in evidence:
                citation(proof)
            entries = area.get('entryPoints', [])
            if area in raw.get('features', []) and not entries:
                raise ValueError('Each generated feature needs an implementation entry point.')
            if not isinstance(entries, list) or len(entries) > 20:
                raise ValueError('Entry points must be a list with at most 20 source locations.')
            for entry in entries:
                path = citation(entry)
                if not any(matches(path, clean_prefix(prefix)) for prefix in area['paths']):
                    raise ValueError('Feature entry points must belong to their assigned paths.')
                if not isinstance(entry.get('reason'), str) or len(entry['reason'].strip()) < 20:
                    raise ValueError('Explain why each entry point is a useful place to start.')


def validate_overview(overview, feature_ids, citation=None):
    if not isinstance(overview, dict) or not isinstance(overview.get('what'), str) or not 80 <= len(overview['what'].strip()) <= 8000:
        raise ValueError('Product overview must explain what the product is in 80–8000 characters.')
    for key, fields in [('usage', ('name', 'description')), ('workflow', ('title', 'description')), ('tools', ('name', 'description')), ('connections', ('from', 'to', 'description'))]:
        items = overview.get(key)
        if not isinstance(items, list) or len(items) > 100 or (not items and key != 'connections'):
            raise ValueError(f'Product overview needs a bounded {key} list.')
        for item in items:
            if not isinstance(item, dict) or any(not isinstance(item.get(field), str) or not item[field].strip() or len(item[field]) > 8000 for field in fields):
                raise ValueError(f'Product overview {key} entries need explanatory text fields.')
            if len(item['description'].strip()) < 40:
                raise ValueError(f'Product overview {key} must explain behavior, not list references.')
            if key == 'connections' and any(item[field] not in feature_ids for field in ('from', 'to')):
                raise ValueError('Product overview connections must reference declared feature IDs.')
            if 'optional' in item and not isinstance(item['optional'], bool):
                raise ValueError('Workflow optional flags must be booleans.')
            commands = item.get('commands', [])
            if not isinstance(commands, list) or len(commands) > 20 or any(not isinstance(command, str) for command in commands):
                raise ValueError('Usage commands must be a bounded list of strings.')
            evidence = item.get('evidence')
            if not isinstance(evidence, list) or not evidence:
                raise ValueError('Product overview explanations need supporting source evidence.')
            for proof in evidence:
                if not isinstance(proof, dict) or not isinstance(proof.get('path'), str) or not isinstance(proof.get('line'), int) or isinstance(proof['line'], bool) or proof['line'] < 1:
                    raise ValueError('Overview citations need a relative path and positive line.')
                clean_prefix(proof['path'])
                if citation:
                    citation(proof)

    if 'journeys' in overview:
        from .journeys import validate_journeys
        validate_journeys(overview['journeys'], citation)
