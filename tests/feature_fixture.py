def overview_fixture(path, what):
    proof = [{'path': path, 'line': 1}]
    return {'what': what,
            'usage': [{'name': 'Call the public function', 'description': 'A caller imports the public function and invokes it directly with its documented inputs.', 'evidence': proof}],
            'workflow': [{'title': 'Execute the operation', 'description': 'The implementation constructs a result and returns it to its caller without additional persistence.', 'optional': False, 'evidence': proof}],
            'tools': [{'name': 'Public function', 'description': 'The public function exposes the operation directly so callers can reuse its behavior.', 'evidence': proof}],
            'connections': []}


def reader_guide_fixture(path='service.py'):
    proof = [{'path': path, 'line': 1, 'reason': 'The public operation constructs and returns the result.'}]
    return {'audience': 'People who need to understand the result of this operation.',
            'outcome': {'text': 'Receive the result created by the public operation.', 'evidence': proof},
            'prerequisites': [], 'rules': [], 'glossary': [],
            'workflows': [{'id': 'receive-result', 'title': 'Receive a result',
                           'goal': 'Understand what happens when someone requests a result.',
                           'trigger': 'A caller invokes the public operation.', 'start': 'request', 'mainPath': ['request', 'result'],
                           'steps': [{'id': 'request', 'title': 'Request a result', 'diagramLabel': 'Request a result', 'kind': 'action',
                                      'action': 'Invoke the public operation.', 'result': 'The operation creates a result for the caller.',
                                      'choices': [{'label': 'Result created', 'next': 'result'}], 'evidence': proof},
                                     {'id': 'result', 'title': 'Receive the result', 'diagramLabel': 'Receive the result', 'kind': 'outcome',
                                      'action': 'Read the returned result.', 'result': 'The caller receives the constructed result.',
                                      'choices': [], 'evidence': proof}]}]}
