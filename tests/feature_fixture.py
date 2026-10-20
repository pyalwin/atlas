def overview_fixture(path, what):
    proof = [{'path': path, 'line': 1}]
    return {'what': what,
            'usage': [{'name': 'Call the public function', 'description': 'A caller imports the public function and invokes it directly with its documented inputs.', 'evidence': proof}],
            'workflow': [{'title': 'Execute the operation', 'description': 'The implementation constructs a result and returns it to its caller without additional persistence.', 'optional': False, 'evidence': proof}],
            'tools': [{'name': 'Public function', 'description': 'The public function exposes the operation directly so callers can reuse its behavior.', 'evidence': proof}],
            'connections': []}
