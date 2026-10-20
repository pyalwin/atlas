import json
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from atlas_index.indexer import index_repository
from atlas_index.review import ReviewStore
from atlas_index.review_guide import VERSION, comprehension_question, parse_python, prose_with_evidence
from atlas_index.server import create_server


class ReviewGuideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name).resolve()
        self.repo = self.directory / 'repo'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.store = ReviewStore(self.repo, self.directory / 'reviews')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, text=True, check=True).stdout.strip()

    def write(self, name, value):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else value.encode())

    def commit(self, message):
        self.git('add', '-A')
        self.git('-c', 'user.name=Atlas Tests', '-c', 'user.email=test@example.invalid', 'commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def review(self, base, identity='0123456789abcdef'):
        self.store.state['job'] = {}
        result = self.store.build(identity, {'base': base, 'head': 'HEAD'})
        self.store.state['review'] = result
        return result

    def assert_pinned_evidence(self, guide):
        def check(item):
            if isinstance(item, dict):
                if {'path', 'side', 'line'} <= item.keys():
                    source = self.store.source(item['path'], item['side'], item['line'], guide['reviewId'])
                    self.assertEqual(source['sha'], guide[item['side'] + 'Sha'])
                    self.assertGreaterEqual(item['line'], 1)
                for value in item.values():
                    check(value)
            elif isinstance(item, list):
                for value in item:
                    check(value)
        check(guide)

    def test_behavior_tests_assertions_and_evidence_are_commit_pinned(self):
        self.write('service.py', 'def format_value(value):\n    return value.strip()\n')
        self.write('helper.py', 'def normalize(value):\n    return value.strip()\n')
        self.write('tests/test_service.py', 'from service import format_value\n\ndef test_spaces():\n    assert format_value(" a ") == "a"\n')
        base = self.commit('base')
        self.write('service.py', 'from helper import normalize\n\ndef format_value(value):\n    """Keep missing values empty."""\n    if value is None:\n        return ""\n    return normalize(value)\n')
        self.write('tests/test_service.py', 'from service import format_value\n\ndef test_spaces():\n    assert format_value(" a ") == "a"\n\ndef test_missing_value():\n    assert format_value(None) == ""\n')
        self.commit('change')
        review = self.review(base)
        # Dirty sources must never influence descriptions, evidence or diagrams.
        self.write('service.py', 'def poisoned():\n    return "dirty working tree"\n')
        guide = self.store.guide(review['id'])
        self.assertNotIn('dirty working tree', json.dumps(guide))
        file = guide['files'][0]
        self.assertEqual(file['path'], 'service.py')
        self.assertIn('normalize', file['summary'])
        self.assertIn('value.strip', file['summary'])
        self.assertTrue(any('new cases' in watch['title'] for watch in file['watchpoints']))
        self.assertEqual(guide['coverage']['execution'], 'not-run')
        self.assertEqual(guide['coverage']['testsChanged'], 1)
        self.assertEqual(guide['coverage']['changedTestNames'][0]['name'], 'test_missing_value')
        assertion = guide['coverage']['observedAssertions'][0]
        self.assertEqual(assertion['text'], 'assert format_value(None) == ""')
        self.assertEqual(assertion['change'], 'added')
        scenario = guide['coverage']['coveredScenarios'][0]
        self.assertEqual(scenario['test'], 'test_missing_value')
        self.assertIn('format_value(None)', scenario['detail'])
        self.assertEqual(scenario['evidence'][0]['side'], 'head')
        self.assertEqual(guide['coverage']['assessment']['status'], 'review-required')
        condition_check = next(check for check in guide['coverage']['reviewerChecks'] if check['id'].startswith('conditions:'))
        self.assertIn('value is None', condition_check['detail'])
        self.assertIn('do not establish branch coverage', condition_check['detail'])
        self.assertTrue(any(test['path'] == 'tests/test_service.py' for test in file['relatedTests']))
        self.assertIn('not runtime order', guide['impact']['note'])
        self.assertTrue(any(n['connections'] for n in guide['impact']['before']))
        self.assertTrue(any(n['connections'] for n in guide['impact']['after']))
        self.assert_pinned_evidence(guide)

    def test_scenarios_show_changed_literal_rows_and_do_not_execute_input_code(self):
        self.write('service.py', 'def normalize(value):\n    return value\n')
        self.write('tests/test_service.py', 'from service import normalize\n\ndef test_values():\n    cases = [("old", "old")]\n    for value, expected in cases:\n        assert normalize(value) == expected\n')
        base = self.commit('base')
        self.write('service.py', 'def normalize(value):\n    return value.strip()\n')
        self.write('tests/test_service.py', '''from service import normalize

def test_values():
    cases = [
        ("old", "old"),
        # Whitespace around the supplied value is removed.
        ("  new  ", "new"),
        (dangerous_function(), "never resolve this expression"),
    ]
    for value, expected in cases:
        assert normalize(value) == expected
''')
        self.commit('table')
        guide = self.store.guide(self.review(base)['id'])
        scenario = guide['coverage']['coveredScenarios'][0]
        self.assertEqual(scenario['title'], 'Whitespace around the supplied value is removed')
        self.assertIn('Test author: Whitespace', scenario['detail'])
        self.assertIn("'  new  '", scenario['examples'][0]['input'])
        self.assertEqual(scenario['examples'][0]['expected'], "'new'")
        self.assertEqual(scenario['examples'][0]['change'], 'added')
        self.assertEqual(scenario['examples'][1]['change'], 'existing')
        self.assertFalse(any('dangerous_function' in item['input'] for item in scenario['examples']))
        self.assert_pinned_evidence(guide)

    def test_test_names_without_assertions_are_not_reported_as_covered_scenarios(self):
        self.write('service.py', 'def number():\n    return 1\n')
        base = self.commit('base')
        self.write('service.py', 'def number():\n    return 2\n')
        self.write('tests/test_service.py', 'from service import number\n\ndef test_handles_all_edge_cases():\n    number()\n')
        self.commit('change')
        guide = self.store.guide(self.review(base)['id'])
        self.assertEqual(guide['coverage']['coveredScenarios'], [])
        self.assertEqual(guide['coverage']['assessment']['status'], 'limited-evidence')
        self.assertTrue(any('helper' in item['detail'] for item in guide['coverage']['reviewerChecks']))
        self.assertTrue(any('return 2' in item['detail'] for item in guide['coverage']['reviewerChecks']))
        self.assert_pinned_evidence(guide)

    def test_reassigned_expected_values_do_not_mislabel_literal_table_rows(self):
        definitions, error = parse_python('''def test_values():
    cases = [("input", "original")]
    for value, expected in cases:
        expected = "replacement"
        assert transform(value) == expected
''', 'tests/test_values.py', 'head')
        self.assertIsNone(error)
        self.assertEqual(definitions['test_values']['examples'], [])

    def test_each_hunk_explains_only_its_changed_expressions_and_local_checks(self):
        separator = '\n' + '\n'.join(f'# Unchanged separation line {number}.' for number in range(16)) + '\n\n'
        self.write('service.py', 'def convert(value):\n    return parseHTTP(value)\n' + separator +
                   'def accept(value):\n    if value > 0:\n        return True\n    return False\n')
        base = self.commit('base')
        self.write('service.py', 'def convert(value):\n    return decodeUTF8(value)\n' + separator +
                   'def accept(value):\n    if value >= 0:\n        return True\n    return False\n')
        self.commit('change two blocks')
        guide = self.store.guide(self.review(base)['id'])
        hunks = guide['files'][0]['hunks']
        self.assertEqual(len(hunks), 2)
        self.assertIn('decodeUTF8', hunks[0]['syntaxSummary'])
        self.assertIn('parseHTTP', hunks[0]['syntaxSummary'])
        self.assertNotIn('accept', hunks[0]['syntaxSummary'])
        self.assertNotIn('value >= 0', hunks[0]['syntaxSummary'])
        self.assertIn('value >= 0', hunks[1]['syntaxSummary'])
        self.assertNotIn('decodeUTF8', hunks[1]['syntaxSummary'])
        self.assertEqual(hunks[0]['watchpoints'], [])
        self.assertEqual(hunks[1]['watchpoints'], [])
        self.assertIsNone(hunks[1]['summary'])
        self.assertEqual(guide['agentReview']['status'], 'not-run')
        self.assert_pinned_evidence(guide)

    def test_questions_require_source_comprehension_and_fingerprint_the_expected_answer(self):
        self.write('service.py', 'def convert(value):\n    parseHTTP(value)\n    return value\n')
        self.write('tests/test_service.py', 'from service import convert\n\ndef test_value():\n    assert convert("x") == 1\n')
        base = self.commit('base')
        self.write('service.py', 'def convert(value):\n    decodeUTF8(value)\n    return value\n')
        self.write('tests/test_service.py', 'from service import convert\n\ndef test_value():\n    assert convert("x") == 2\n')
        self.commit('change')
        guide = self.store.guide(self.review(base)['id'])
        self.assertEqual(guide['version'], VERSION)
        questions = guide['questions']
        self.assertGreaterEqual(len(questions), 2)
        self.assertLessEqual(len(questions), 4)
        for question in questions:
            self.assertEqual(question['kind'], 'choice')
            self.assertTrue(question['id'].startswith('q-'))
            self.assertGreaterEqual(len(question['choices']), 2)
            self.assertEqual(len([item for item in question['choices'] if item['id'] == question['correctChoiceId']]), 1)
            self.assertTrue(question['explanation'])
            self.assertTrue(question['evidence'])
        call_question = next(item for item in questions if 'call-site' in item['prompt'])
        correct = next(item['text'] for item in call_question['choices'] if item['id'] == call_question['correctChoiceId'])
        self.assertIn('decodeUTF8()', correct)
        self.assertIn('parseHTTP()', correct)
        self.assertNotIn('decodeutf8', correct)
        result_question = next(item for item in questions if 'must convert' in item['prompt'])
        self.assertEqual(next(item['text'] for item in result_question['choices'] if item['id'] == result_question['correctChoiceId']), '2')
        self.assertIn('does not establish that the test passes', result_question['explanation'])
        self.assert_pinned_evidence(guide)
        self.write('tests/test_service.py', 'from service import convert\n\ndef test_value():\n    assert convert("x") == 3\n')
        self.commit('change the expectation')
        next_guide = self.store.guide(self.review(base, 'fedcba9876543210')['id'])
        next_call = next(item for item in next_guide['questions'] if 'call-site' in item['prompt'])
        next_result = next(item for item in next_guide['questions'] if 'must convert' in item['prompt'])
        self.assertEqual(call_question, next_call)
        self.assertNotEqual(result_question['id'], next_result['id'])
        self.assertNotEqual(result_question['correctChoiceId'], next_result['correctChoiceId'])

    def test_table_question_asks_the_expected_result_for_an_actual_changed_input(self):
        self.write('tests/test_values.py', '''def test_values():
    cases = [("unchanged", "unchanged")]
    for value, expected in cases:
        assert normalize(value) == expected
''')
        base = self.commit('base')
        self.write('tests/test_values.py', '''def test_values():
    cases = [("unchanged", "unchanged"), (" padded ", "padded")]
    for value, expected in cases:
        assert normalize(value) == expected
''')
        self.commit('new example')
        guide = self.store.guide(self.review(base)['id'])
        question = guide['questions'][0]
        self.assertIn("value=' padded '", question['prompt'])
        answer = next(item['text'] for item in question['choices'] if item['id'] == question['correctChoiceId'])
        self.assertEqual(answer, "'padded'")
        self.assertIn('equality assertion', question['explanation'])
        self.assert_pinned_evidence(guide)

    def test_question_distractors_do_not_offer_equivalent_literal_values_as_wrong_answers(self):
        question = comprehension_question('What set is expected?', "{'a', 'b'}",
                                            ["{'b', 'a'}", "{'c'}"], 'A written expectation.', [], '')
        self.assertEqual(len(question['choices']), 2)
        self.assertEqual({item['text'] for item in question['choices']}, {"{'a', 'b'}", "{'c'}"})

    def test_docstring_question_uses_the_changed_sentence_instead_of_the_whole_contract(self):
        self.write('service.py', '''def split_value(value):
    """Return separated values. Quoted separators are split (e.g. A, B). Leading whitespace is removed."""
    return value.split(",")
''')
        base = self.commit('base')
        self.write('service.py', '''def split_value(value):
    """Return separated values. Quoted separators stay together. Leading whitespace is removed."""
    return parse(value)
''')
        self.commit('update the contract')
        guide = self.store.guide(self.review(base)['id'])
        question = guide['questions'][0]
        self.assertIn('docstring', question['prompt'])
        correct = next(item['text'] for item in question['choices'] if item['id'] == question['correctChoiceId'])
        self.assertEqual(correct, 'Quoted separators stay together.')
        self.assertIn('Quoted separators are split (e.g. A, B).', [item['text'] for item in question['choices']])
        self.assertNotIn('Leading whitespace', json.dumps(question))
        self.assertIn('written contract', question['explanation'])
        self.assert_pinned_evidence(guide)

    def test_uninterpreted_changes_use_explicit_reflection_instead_of_inventing_correctness(self):
        self.write('service.js', 'export function convert(value) { return value; }\n')
        base = self.commit('base')
        self.write('service.js', 'export function convert(value) { return normalize(value); }\n')
        self.commit('change')
        guide = self.store.guide(self.review(base)['id'])
        question = guide['questions'][0]
        self.assertEqual(question['kind'], 'reflection')
        self.assertEqual(question['choices'], [])
        self.assertIsNone(question['correctChoiceId'])
        self.assertIn('cannot derive a reliable answer', question['explanation'])
        self.assertIn('service.js', question['prompt'])
        self.assertIn('normalize(value)', guide['files'][0]['hunks'][0]['syntaxSummary'])
        self.assert_pinned_evidence(guide)

    def test_doc_only_and_test_only_scope_are_not_described_as_product_behavior(self):
        self.write('README.md', '# Usage\nCall the function.\n')
        self.write('tests/test_one.py', 'def test_one():\n    assert 1 == 1\n')
        base = self.commit('base')
        self.write('README.md', '# Usage\nPass an explicit value to the function.\n')
        self.commit('docs')
        guide = self.store.guide(self.review(base)['id'])
        self.assertEqual(guide['files'][0]['role'], 'documentation')
        self.assertIn('Pass an explicit value', guide['files'][0]['summary'])
        self.assertEqual(guide['brief']['behaviorChanges'], [])
        self.assertFalse(guide['coverage']['gaps'])
        self.assertFalse(any(node['connections'] for node in guide['impact']['after']))
        self.assert_pinned_evidence(guide)
        second_base = self.git('rev-parse', 'HEAD')
        self.write('tests/test_one.py', 'def test_one():\n    assert 2 == 2\n')
        self.commit('tests')
        test_guide = self.store.guide(self.review(second_base, 'fedcba9876543210')['id'])
        self.assertEqual(test_guide['files'][0]['role'], 'tests')
        self.assertIn('test cases', test_guide['files'][0]['summary'])
        self.assertIn('without an identified implementation change', test_guide['coverage']['summary'])
        self.assertFalse(test_guide['coverage']['gaps'])

    def test_deleted_code_and_tests_keep_base_evidence_and_raise_watchpoints(self):
        self.write('service.py', 'def obsolete():\n    return 1\n')
        self.write('tests/test_service.py', 'from service import obsolete\n\ndef test_old():\n    assert obsolete() == 1\n')
        self.write('README.md', '# Project\n')
        base = self.commit('base')
        self.git('rm', '-q', 'service.py', 'tests/test_service.py')
        self.commit('remove')
        guide = self.store.guide(self.review(base)['id'])
        self.assertEqual(guide['coverage']['changedTestNames'][0]['change'], 'deleted')
        self.assertTrue(any('removed' in gap for gap in guide['coverage']['gaps']))
        self.assertEqual(guide['coverage']['coveredScenarios'], [])
        for file in guide['files']:
            self.assertEqual(file['after'], 'This file is absent from the pinned snapshot.')
            self.assertTrue(all(ref['side'] == 'base' for ref in file['evidence']))
        self.assertEqual(guide['impact']['after'], [])
        self.assert_pinned_evidence(guide)

    def test_no_changed_tests_is_an_evidence_gap_not_a_failure_claim(self):
        self.write('service.py', 'def number():\n    return 1\n')
        base = self.commit('base')
        self.write('service.py', 'def number():\n    return 2\n')
        self.commit('change')
        guide = self.store.guide(self.review(base)['id'])
        self.assertEqual(guide['coverage']['testsChanged'], 0)
        self.assertEqual(guide['coverage']['execution'], 'not-run')
        self.assertTrue(any('Existing tests may still cover it' in gap for gap in guide['coverage']['gaps']))
        self.assertIn('return 1', guide['files'][0]['summary'])
        self.assertIn('return 2', guide['files'][0]['summary'])
        self.assertTrue(any('not proof that tests are missing' in w['detail'] for w in guide['files'][0]['watchpoints']))

    def test_parse_failure_exclusion_binary_and_diff_cap_remain_visible(self):
        self.write('broken.py', 'def working():\n    return 1\n')
        self.write('service.py', 'value = 0\n' * 20)
        base = self.commit('base')
        self.write('broken.py', 'def broken(:\n')
        self.write('service.py', 'value = 100\n' * 20)
        self.write('secrets.json', '{"secret": "do-not-include-this"}')
        self.write('binary.py', b'\x00binary-private')
        self.commit('change')
        with patch('atlas_index.review.MAX_DIFF_BYTES', 130):
            guide = self.store.guide(self.review(base)['id'])
        files = {file['path']: file for file in guide['files']}
        self.assertFalse(files['broken.py']['analysis']['complete'])
        self.assertTrue(any('could not be parsed' in limit for limit in files['broken.py']['analysis']['limitations']))
        self.assertFalse(any(item['change'] == 'deleted' for item in files['broken.py']['changedDefinitions']))
        self.assertIn('could not be parsed', files['broken.py']['after'])
        self.assertTrue(any('truncated' in limit for limit in files['service.py']['analysis']['limitations']))
        self.assertFalse(files['binary.py']['analysis']['complete'])
        self.assertFalse(files['secrets.json']['analysis']['complete'])
        self.assertEqual(files['secrets.json']['evidence'], [])
        self.assertNotIn('do-not-include-this', json.dumps(guide))
        self.assertNotIn('binary-private', json.dumps(guide))
        self.assertTrue(any(w['id'] == 'analysis-limits' for w in guide['watchpoints']))

    def test_rename_links_to_old_base_and_new_head_paths(self):
        self.write('before.py', 'def number():\n    return 1\n')
        base = self.commit('base')
        self.git('mv', 'before.py', 'after.py')
        self.write('after.py', 'def number():\n    return 2\n')
        self.commit('rename')
        guide = self.store.guide(self.review(base)['id'])
        file = guide['files'][0]
        self.assertEqual(file['oldPath'], 'before.py')
        self.assertTrue(any(ref['path'] == 'before.py' and ref['side'] == 'base' for ref in file['evidence']))
        self.assertTrue(any(ref['path'] == 'after.py' and ref['side'] == 'head' for ref in file['evidence']))
        self.assert_pinned_evidence(guide)

    def test_pure_test_and_doc_renames_do_not_claim_content_changes(self):
        self.write('tests/test_before.py', 'def test_one():\n    assert 1 == 1\n')
        self.write('before.md', '# Usage\nCall the function with a value.\n')
        base = self.commit('base')
        self.git('mv', 'tests/test_before.py', 'tests/test_after.py')
        self.git('mv', 'before.md', 'after.md')
        self.write('tests/test_binary.py', b'\x00unreadable')
        self.commit('rename')
        guide = self.store.guide(self.review(base)['id'])
        files = {file['path']: file for file in guide['files']}
        self.assertIn('contents are unchanged', files['tests/test_after.py']['summary'])
        self.assertIn('contents are unchanged', files['after.md']['summary'])
        self.assertIn('cannot be inspected', files['tests/test_binary.py']['summary'])
        self.assertEqual(guide['coverage']['testsChanged'], 0)

    def test_file_cap_reports_missing_scope_before_assessing_tests(self):
        self.write('a.py', 'def value():\n    return 1\n')
        self.write('tests/test_value.py', 'def test_value():\n    assert 1 == 1\n')
        base = self.commit('base')
        self.write('a.py', 'def value():\n    return 2\n')
        self.write('tests/test_value.py', 'def test_value():\n    assert 2 == 2\n')
        self.commit('change')
        with patch('atlas_index.review.MAX_FILES', 1):
            guide = self.store.guide(self.review(base)['id'])
        self.assertEqual(len(guide['files']), 1)
        self.assertTrue(any('files' in limit and 'capped' in limit for limit in guide['limitations']))
        self.assertTrue(any('available review data' in gap for gap in guide['coverage']['gaps']))
        self.assertTrue(any('may omit tests' in gap for gap in guide['coverage']['gaps']))

    def test_sampled_graph_references_do_not_imply_uninspected_source_or_tests(self):
        self.write('service.py', 'def value():\n    return 1\n')
        tests = 'from service import value\n\n' + '\n'.join(
            f'def test_value_{number}():\n    assert value() == 1\n' for number in range(16))
        self.write('tests/test_service.py', tests)
        base = self.commit('base')
        self.write('service.py', 'def value():\n    return 2\n')
        self.write('tests/test_service.py', tests.replace('== 1', '== 2', 1))
        self.commit('change')
        review = self.review(base)
        self.assertTrue(review['truncated']['evidence'])
        guide = self.store.guide(review['id'])
        service = next(file for file in guide['files'] if file['path'] == 'service.py')
        self.assertTrue(service['analysis']['complete'])
        self.assertTrue(service['analysis']['referenceNotes'])
        self.assertEqual(guide['coverage']['gaps'], [])
        self.assertFalse(any(w['id'] in {'coverage-gaps', 'analysis-limits'} for w in guide['watchpoints']))
        self.assertFalse(any(w['id'] in {'incomplete', 'sampled'} for w in service['watchpoints']))
        self.assertTrue(any('does not limit the changed-file diffs' in note for note in guide['coverage']['limitations']))

    def test_cache_version_scope_and_commits_are_checked_without_fetch_or_index(self):
        self.write('one.py', 'x = 1\n')
        base = self.commit('base')
        self.write('one.py', 'x = 2\n')
        self.commit('change')
        review = self.review(base)
        first = self.store.guide(review['id'])
        with patch('atlas_index.review_guide.build_guide', side_effect=AssertionError('should use cache')), \
                patch.object(self.store, 'snapshot', side_effect=AssertionError('no reindex')), \
                patch.object(self.store, '_resolve', side_effect=AssertionError('no fetch')):
            self.assertEqual(first, self.store.guide(review['id']))
        cache = next(self.store.directory.glob(f'guide-v{VERSION}-*.json'))
        cached = json.loads(cache.read_text())
        cached['headSha'] = '0' * 40
        cache.write_text(json.dumps(cached))
        with patch('atlas_index.review_guide.build_guide', wraps=__import__('atlas_index.review_guide', fromlist=['build_guide']).build_guide) as rebuild:
            self.assertEqual(self.store.guide(review['id'])['headSha'], review['headSha'])
            rebuild.assert_called_once()
        with self.assertRaises(ValueError):
            self.store.guide('../bad')
        with self.assertRaises(KeyError):
            self.store.guide('0000000000000000')

    def test_title_is_attributed_and_control_characters_removed(self):
        self.write('one.py', 'x = 1\n')
        base = self.commit('base')
        self.write('one.py', 'x = 2\n')
        self.commit('change')
        review = self.review(base)
        review.update(title='Fixed #123 -- Handle missing values.\n\x00', url='https://github.com/example/repo/pull/4')
        (self.store.directory / (review['id'] + '.json')).write_text(json.dumps(review))
        guide = self.store.guide(review['id'])
        self.assertEqual(guide['brief']['summary'], 'Handle missing values.')
        self.assertEqual(guide['brief']['intentSource'], 'pr-title')
        self.assertIn('PR title', guide['brief']['intentNote'])
        self.assertFalse(any('missing values' in fact['text'] for fact in guide['brief']['behaviorChanges']))

    def test_exact_prose_ranges_and_unicode_assertion_columns(self):
        source = 'def f(value):\n    """A comma inside quotes remains part of the value.\n    This also accepts semicolons.\n    """\n    # Unrelated later explanation.\n    return value\n'
        prose = prose_with_evidence(source, {2, 3, 5}, 'one.py', 'head')
        self.assertEqual(prose['evidence'], [{'path': 'one.py', 'side': 'head', 'line': 2, 'endLine': 3}])
        self.assertNotIn('Unrelated', prose['text'])
        defs, error = parse_python('def test_unicode():\n    label = "é"; assert label == "é"\n', 'test_one.py', 'head')
        self.assertIsNone(error)
        self.assertEqual(defs['test_unicode']['assertions'][0]['text'], 'assert label == "é"')

    def test_guide_http_endpoint_and_empty_review(self):
        self.write('one.py', 'x = 1\n')
        self.commit('base')
        db = self.directory / 'project.db'
        index_repository(self.repo, db)
        server = create_server(db, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            request = urllib.request.Request(url + '/api/review', data=json.dumps({'base': 'HEAD', 'head': 'HEAD'}).encode(),
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as response:
                result = json.load(response)
            deadline = time.monotonic() + 10
            while result['job']['state'] not in {'complete', 'error'} and time.monotonic() < deadline:
                time.sleep(.01)
                with urllib.request.urlopen(url + '/api/review') as response:
                    result = json.load(response)
            self.assertEqual(result['job']['state'], 'complete')
            with urllib.request.urlopen(url + '/api/review/guide?review=' + result['review']['id']) as response:
                guide = json.load(response)
            self.assertEqual(guide['files'], [])
            self.assertEqual(guide['brief']['title'], 'No changes to review')
            self.assertEqual(guide['watchpoints'], [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
