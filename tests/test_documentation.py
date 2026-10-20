import copy
import tempfile
import subprocess
import unittest
from pathlib import Path

from feature_fixture import overview_fixture, reader_guide_fixture

from atlas_index.documentation import validate_candidate_documentation
from atlas_index.indexer import index_repository
from atlas_index.product import ProductStore
from atlas_index.projects import validate_map
from atlas_index.server import GraphStore


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'service.py').write_text('def greet(name):\n    return "Hello " + name\n')
        (self.root / 'test_service.py').write_text('from service import greet\ndef test_greeting():\n    assert greet("Ada") == "Hello Ada"\n')
        self.db = self.root / 'graph.db'
        self.status = index_repository(self.root, self.db)
        docs = {
            'purpose': 'Expose a greeting operation for callers that already have a display name. Formatting greetings is its only responsibility.',
            'behavior': 'The caller supplies a name and receives the greeting text. The implementation concatenates strings without validating the supplied name.',
            'implementation': 'Start at greet in service.py. The function constructs the response directly and returns it without using a persistence layer.',
            'dependencies': 'The implementation has no indexed outgoing code dependencies. The greeting test imports greet directly from the service module.',
            'testing': 'test_service.py asserts the greeting for Ada. No tests were run during documentation generation, and malformed inputs are not covered.',
            'limitations': 'The source establishes string concatenation only. Caller input validation, localization and production integration are not established.'
        }
        self.map = {'version': 1, 'project': {'name': 'Greeting service', 'summary': docs['purpose'], 'documentation': docs},
                    'features': [{'id': 'greetings', 'name': 'Create greetings', 'summary': docs['purpose'], 'paths': ['service.py'],
                                  'documentation': docs, 'evidence': [{'path': 'service.py', 'line': 1}],
                                  'entryPoints': [{'path': 'service.py', 'line': 1, 'reason': 'Read the public greeting function to understand the entire behavior.'}]}],
                    'foundations': [], 'generation': {'indexedAt': self.status['indexedAt']}}

        self.map['project']['readerGuide'] = reader_guide_fixture()
        self.map['features'][0]['readerGuide'] = reader_guide_fixture()
        self.map['project']['overview'] = overview_fixture('service.py', docs['purpose'])

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, raw):
        validate_map(raw)
        validate_candidate_documentation(raw, self.db, self.root)

    def test_documented_map_projects_hierarchy_and_guide(self):
        self.validate(self.map)
        path = self.root / 'map.json'
        import json
        path.write_text(json.dumps(self.map))
        products = ProductStore(GraphStore(self.db), taxonomy_path=path, cache_path=self.root / 'cache.json')
        galaxy = products.graph()
        self.assertEqual(galaxy['parent']['name'], 'Greeting service')
        self.assertEqual(galaxy['parent']['celestialRole'], 'galaxy')
        feature = galaxy['nodes'][0]
        self.assertEqual(feature['celestialRole'], 'sun')
        self.assertEqual(feature['documentation'], self.map['features'][0]['documentation'])
        self.assertEqual(feature['entryPoints'][0]['path'], 'service.py')
        self.assertEqual(feature['readerGuide'], self.map['features'][0]['readerGuide'])
        self.assertEqual(feature['documentationFreshness'], 'current')

    def test_rejects_one_line_reference_instead_of_explanation(self):
        raw = copy.deepcopy(self.map)
        raw['features'][0]['summary'] = 'See service.py:1'
        with self.assertRaisesRegex(ValueError, '80 characters'):
            self.validate(raw)
        raw = copy.deepcopy(self.map)
        del raw['features'][0]['documentation']['behavior']
        with self.assertRaisesRegex(ValueError, 'behavior'):
            self.validate(raw)

    def test_rejects_invented_paths_and_invalid_citations(self):
        for field, value in [('paths', ['missing']), ('evidence', [{'path': 'outside.py', 'line': 1}]),
                             ('evidence', [{'path': 'service.py', 'line': 999}]), ('entryPoints', [])]:
            with self.subTest(field=field, value=value):
                raw = copy.deepcopy(self.map)
                raw['features'][0][field] = value
                with self.assertRaises(ValueError):
                    self.validate(raw)

    def test_rejects_malformed_manual_documentation(self):
        raw = copy.deepcopy(self.map)
        raw['features'][0]['entryPoints'] = [None]
        with self.assertRaises(ValueError):
            validate_map(raw)

    def test_inventory_pages_cover_every_file_without_duplicates(self):
        store = GraphStore(self.db)
        first = store.inventory(0, 1)
        second = store.inventory(first['nextOffset'], 1)
        self.assertIsNone(second['nextOffset'])
        self.assertNotEqual(first['files'][0]['path'], second['files'][0]['path'])
        self.assertEqual(first['indexedAt'], second['indexedAt'])

    def test_overview_requires_interfaces_workflow_tools_and_evidence(self):
        for field in ['what', 'usage', 'workflow', 'tools']:
            raw = copy.deepcopy(self.map)
            del raw['project']['overview'][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(raw)
        raw = copy.deepcopy(self.map)
        raw['project']['overview']['workflow'][0]['evidence'][0]['line'] = 1000
        with self.assertRaises(ValueError):
            self.validate(raw)
        raw = copy.deepcopy(self.map)
        raw['project']['overview']['connections'] = [{'from': 'missing', 'to': 'greetings', 'description': 'A source-grounded explanation between two responsibilities.', 'evidence': [{'path':'service.py','line':1}]}]
        with self.assertRaises(ValueError):
            self.validate(raw)

    def test_stale_documentation_is_disclosed(self):
        self.map['generation']['indexedAt'] = 'earlier-index'
        import json
        path = self.root / 'map.json'
        path.write_text(json.dumps(self.map))
        products = ProductStore(GraphStore(self.db), taxonomy_path=path, cache_path=self.root / 'cache.json')
        self.assertEqual(products.graph()['nodes'][0]['documentationFreshness'], 'needs-refresh')

    def test_journeys_validate_reachable_choices_and_complete_scenarios(self):
        proof = [{'path': 'service.py', 'line': 1}]
        journey = {'id': 'greet', 'title': 'Get a greeting', 'goal': 'Understand how to receive a greeting.', 'start': 'ask',
                   'steps': [{'id': 'ask', 'title': 'Give a name', 'action': 'Supply a name.', 'result': 'The product uses the name to prepare a greeting.', 'choices': [{'label': 'Continue', 'next': 'done'}], 'evidence': proof},
                             {'id': 'done', 'title': 'Receive the greeting', 'action': 'Read the result.', 'result': 'The greeting contains the supplied name.', 'choices': [], 'evidence': proof}],
                   'scenarios': [{'title': 'Greeting a person', 'given': 'A name is available.', 'when': 'The name is supplied.', 'then': 'A greeting is returned.', 'steps': ['ask', 'done'], 'evidence': proof}]}
        raw = copy.deepcopy(self.map)
        journey['mainPath'] = ['ask', 'done']
        raw['project']['overview']['journeys'] = [journey]
        journey['steps'][0]['details'] = [{'title':'How does this work?', 'description':'The product constructs a greeting from the supplied name.', 'evidence':proof, 'details':[{'title':'What is returned?', 'description':'The returned value contains the greeting text.', 'evidence':proof}]}]
        self.validate(raw)
        for mutate in [lambda j: j.update(mainPath=['done']),
                       lambda j: j['steps'][0]['choices'][0].update(next='missing'),
                       lambda j: j['steps'][1].update(choices=[{'label':'Again','next':'ask'}]),
                       lambda j: j['scenarios'][0].update(steps=['done']),
                       lambda j: j['steps'][0]['evidence'][0].update(line=9999)]:
            changed = copy.deepcopy(raw)
            mutate(changed['project']['overview']['journeys'][0])
            with self.assertRaises(ValueError):
                self.validate(changed)

    def test_reader_guide_requires_source_backed_business_explanations(self):
        for mutate in [lambda g: g.update(outcome={'text': 'A useful result.'}),
                       lambda g: g.update(glossary=[{'term': 'Result', 'meaning': 'Returned information.', 'evidence': [{'path': 'service.py', 'line': 999}]}]),
                       lambda g: g['workflows'][0].pop('mainPath'),
                       lambda g: g['workflows'][0].pop('trigger'),
                       lambda g: g['workflows'][0]['steps'][0].update(kind='decision'),
                       lambda g: g['workflows'][0]['steps'][0].update(kind='outcome'),
                       lambda g: g['workflows'][0]['steps'][0].update(diagramLabel='x' * 81),
                       lambda g: g.update(rules=[{'text': 'Always succeeds.', 'evidence': []}])]:
            raw = copy.deepcopy(self.map)
            mutate(raw['features'][0]['readerGuide'])
            with self.subTest(guide=raw['features'][0]['readerGuide']), self.assertRaises(ValueError):
                self.validate(raw)

    def test_legacy_saved_maps_remain_readable_but_new_candidates_need_reader_guides(self):
        raw = copy.deepcopy(self.map)
        del raw['project']['readerGuide']
        del raw['features'][0]['readerGuide']
        validate_map(raw)
        with self.assertRaisesRegex(ValueError, 'Reader guide'):
            validate_candidate_documentation(raw, self.db, self.root)

    def test_duplicate_glossary_terms_are_rejected(self):
        raw = copy.deepcopy(self.map)
        term = {'term': 'Greeting', 'meaning': 'The text returned to the caller.', 'evidence': [{'path': 'service.py', 'line': 1}]}
        raw['features'][0]['readerGuide']['glossary'] = [term, {**term, 'term': ' greeting '}]
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.validate(raw)
