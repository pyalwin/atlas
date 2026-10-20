"""Validate source-grounded, plain-language behavior walkthroughs."""
from .product import clean_prefix


def validate_journeys(journeys, citation=None):
    if not isinstance(journeys, list) or not 1 <= len(journeys) <= 20:
        raise ValueError('Journeys must contain between 1 and 20 workflows.')

    def text(item, keys):
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not 1 <= len(item[key].strip()) <= 8000 for key in keys):
            raise ValueError('Journey descriptions must contain readable text fields.')

    def evidence(item):
        proofs = item.get('evidence')
        if not isinstance(proofs, list) or not 1 <= len(proofs) <= 20:
            raise ValueError('Every behavior explanation needs source evidence.')
        for proof in proofs:
            text(proof, ('path',))
            if not isinstance(proof.get('line'), int) or isinstance(proof['line'], bool) or proof['line'] < 1:
                raise ValueError('Journey evidence needs a positive source line.')
            clean_prefix(proof['path'])
            if citation:
                citation(proof)

    detail_count = 0
    def details(items, depth=0):
        nonlocal detail_count
        if not isinstance(items, list) or len(items) > 20 or depth > 4:
            raise ValueError('Journey drill-down depth or size exceeds the limit.')
        for item in items:
            detail_count += 1
            if detail_count > 300:
                raise ValueError('Too many journey details.')
            text(item, ('title', 'description'))
            evidence(item)
            if 'details' in item:
                details(item['details'], depth + 1)

    ids = set()
    for journey in journeys:
        text(journey, ('id', 'title', 'goal', 'start'))
        if journey['id'] in ids:
            raise ValueError('Journey IDs must be unique.')
        ids.add(journey['id'])
        steps = journey.get('steps')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
            raise ValueError('Journeys need between 1 and 100 steps.')
        by_id = {}
        for step in steps:
            text(step, ('id', 'title', 'action', 'result'))
            if step['id'] in by_id:
                raise ValueError('Journey step IDs must be unique.')
            by_id[step['id']] = step
            for field in ('diagramLabel', 'benefit'):
                if field in step:
                    text(step, (field,))
            evidence(step)
            details(step.get('details', []))
            choices = step.get('choices', [])
            if not isinstance(choices, list) or len(choices) > 10:
                raise ValueError('A journey step may have at most 10 choices.')
            for choice in choices:
                text(choice, ('label', 'next'))
        if journey['start'] not in by_id:
            raise ValueError('Journey start must identify a real step.')
        visited, active = set(), set()
        def visit(identity):
            if identity not in by_id:
                raise ValueError('Journey choices must identify real steps.')
            if identity in active:
                raise ValueError('Walkthroughs must end; use distinct steps to describe retry outcomes.')
            if identity in visited:
                return
            active.add(identity)
            for choice in by_id[identity].get('choices', []):
                visit(choice['next'])
            active.remove(identity)
            visited.add(identity)
        visit(journey['start'])
        if visited != set(by_id):
            raise ValueError('Journey contains unreachable steps.')
        if 'mainPath' in journey:
            path = journey['mainPath']
            if not isinstance(path, list) or not 1 <= len(path) <= 100 or any(not isinstance(identity, str) or identity not in by_id for identity in path):
                raise ValueError('Main workflow path must reference real steps.')
            if path[0] != journey['start'] or by_id[path[-1]].get('choices'):
                raise ValueError('Main workflow path must run from start to an outcome.')
            if any(after not in {choice['next'] for choice in by_id[before].get('choices', [])} for before, after in zip(path, path[1:])):
                raise ValueError('Main workflow path must follow declared choices.')
        scenarios = journey.get('scenarios', [])
        if not isinstance(scenarios, list) or len(scenarios) > 30:
            raise ValueError('Too many journey scenarios.')
        for scenario in scenarios:
            text(scenario, ('title', 'given', 'when', 'then'))
            evidence(scenario)
            path = scenario.get('steps')
            if not isinstance(path, list) or not 1 <= len(path) <= 100 or any(not isinstance(identity, str) or identity not in by_id for identity in path):
                raise ValueError('Scenario steps must reference real workflow steps.')
            if path[0] != journey['start'] or by_id[path[-1]].get('choices'):
                raise ValueError('Scenario walkthroughs must run from the start to an outcome.')
            for before, after in zip(path, path[1:]):
                if after not in {choice['next'] for choice in by_id[before].get('choices', [])}:
                    raise ValueError('Scenario path must follow the declared choices.')
