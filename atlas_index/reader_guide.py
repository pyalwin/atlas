"""Source-backed product documentation, distinct from implementation notes."""
from .journeys import validate_journeys
from .product import clean_prefix


def validate_reader_guide(guide, citation=None, require_workflows=True):
    if not isinstance(guide, dict):
        raise ValueError('Reader guide must describe the audience, outcome and workflows.')

    def text(item, key, limit=2000):
        value = item.get(key) if isinstance(item, dict) else None
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
            raise ValueError(f'Reader guide {key} needs clear, bounded text.')

    def evidence(item):
        proofs = item.get('evidence')
        if not isinstance(proofs, list) or not 1 <= len(proofs) <= 20:
            raise ValueError('Every reader guide claim needs source evidence.')
        for proof in proofs:
            text(proof, 'path', 1000)
            clean_prefix(proof['path'])
            if isinstance(proof.get('line'), bool) or not isinstance(proof.get('line'), int) or proof['line'] < 1:
                raise ValueError('Reader guide evidence needs a positive source line.')
            if citation:
                citation(proof)

    text(guide, 'audience', 500)
    outcome = guide.get('outcome')
    text(outcome, 'text')
    evidence(outcome)
    for key, fields, limit in [('prerequisites', ('text',), 12), ('rules', ('text',), 20), ('glossary', ('term', 'meaning'), 30)]:
        items = guide.get(key)
        if not isinstance(items, list) or len(items) > limit:
            raise ValueError(f'Reader guide {key} must be a bounded list; use an empty list when none are established.')
        terms = set()
        for item in items:
            for field in fields:
                text(item, field, 100 if field == 'term' else 2000)
            evidence(item)
            if key == 'glossary':
                term = item['term'].strip().casefold()
                if term in terms:
                    raise ValueError('Reader guide glossary terms must be unique.')
                terms.add(term)
    workflows = guide.get('workflows')
    if require_workflows or workflows:
        validate_journeys(workflows, citation)
        if len(workflows) > 8:
            raise ValueError('Reader guides should focus on at most eight principal workflows.')
        for workflow in workflows:
            text(workflow, 'trigger', 500)
            if 'mainPath' not in workflow:
                raise ValueError('Reader guide workflows need an explicit mainPath.')
            if len(workflow['steps']) > 30:
                raise ValueError('Break long reader guide workflows into smaller product jobs (at most 30 steps).')
            for step in workflow['steps']:
                text(step, 'diagramLabel', 80)
                if step.get('kind', 'action') not in ('action', 'decision', 'outcome'):
                    raise ValueError('Workflow steps must be actions, decisions or outcomes.')
                choices = step.get('choices', [])
                if step.get('kind') == 'decision' and len(choices) < 2:
                    raise ValueError('A workflow decision needs at least two supported outcomes.')
                if step.get('kind') == 'outcome' and choices:
                    raise ValueError('A workflow outcome must end its route.')
