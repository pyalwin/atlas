import copy
import json
import subprocess
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from atlas_index.indexer import index_repository
from atlas_index.projects import project_id
from atlas_index.review import ReviewStore
from atlas_index.server import create_server


class ReviewAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        (self.repo / 'quota.py').write_text('def remaining(limit, used):\n    return max(0, limit - used)\n')
        (self.repo / 'caller.py').write_text('from quota import remaining\nallowed = remaining(10, 12) > 0\n')
        self.base = self.commit('base')
        (self.repo / 'quota.py').write_text('def remaining(limit, used):\n    return abs(limit - used)\n')
        self.head = self.commit('head')
        self.store = ReviewStore(self.repo, self.root / 'reviews')
        self.store.state['job'] = {}
        self.review = self.store.build('0123456789abcdef', {'base': self.base, 'head': self.head})
        self.store.state['review'] = self.review
        self.identity = self.review['id']
        self.proof = {'path': 'quota.py', 'side': 'head', 'line': 2}
        self.analysis = {
            'reviewId': self.identity, 'baseSha': self.review['baseSha'], 'headSha': self.review['headSha'],
            'agent': 'Test reviewer', 'summary': 'Over-quota usage now produces a positive allowance instead of zero.',
            'reviewedPaths': ['quota.py'], 'limitations': ['Tests were not executed.'],
            'blocks': [{'path': 'quota.py', 'hunkIndex': 0,
                        'summary': 'Usage above the limit now becomes a positive allowance instead of remaining blocked.',
                        'impact': 'Callers that treat a positive allowance as permission can accept requests after the quota is exhausted.',
                        'evidence': [self.proof]}],
            'findings': [{'id': 'quota-overrun', 'path': 'quota.py', 'priority': 'P1',
                          'title': 'Exhausted quotas become positive allowances',
                          'trigger': 'Usage exceeds its limit, such as 12 units used against a limit of 10.',
                          'cause': 'The replacement computes an absolute difference, turning the negative remainder into a positive number.',
                          'consequence': 'The caller accepts an over-quota request because the returned allowance is positive.',
                          'fix': 'Clamp negative remainders to zero instead of taking the absolute value.',
                          'anchor': self.proof, 'evidence': [self.proof, {'path': 'caller.py', 'side': 'head', 'line': 2}]}]}

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, message):
        self.git('add', '-A')
        self.git('-c', 'user.name=Atlas Tests', '-c', 'user.email=test@example.invalid', 'commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_tldr_and_concrete_findings_replace_templates_and_survive_reload(self):
        initial = self.store.guide(self.identity)
        self.assertEqual(initial['agentReview']['status'], 'not-run')
        self.assertIsNone(initial['files'][0]['hunks'][0]['summary'])
        self.assertEqual(initial['files'][0]['hunks'][0]['watchpoints'], [])
        self.store.save_analysis(self.identity, self.analysis)
        (self.repo / 'quota.py').write_text('raise RuntimeError("working copy must not be executed or used")\n')
        guide = self.store.guide(self.identity)
        self.assertEqual(guide['files'][0]['hunks'][0]['summary'], self.analysis['blocks'][0]['summary'])
        self.assertEqual(guide['findings'][0]['trigger'], self.analysis['findings'][0]['trigger'])
        self.assertTrue(guide['findings'][0]['fingerprint'])
        self.assertFalse(guide['agentReview']['coverageComplete'])
        self.assertEqual(guide['brief']['summary'], self.analysis['summary'])
        reloaded = ReviewStore(self.repo, self.store.directory)
        try:
            self.assertEqual(reloaded.guide(self.identity)['findings'], guide['findings'])
        finally:
            reloaded.close()

    def test_rejects_wrong_scope_missing_evidence_and_unchanged_anchors(self):
        for mutate in [lambda a: a.update(headSha='f' * 40),
                       lambda a: a.update(reviewId='fedcba9876543210'),
                       lambda a: a['findings'][0].update(anchor={'path': 'quota.py', 'side': 'head', 'line': 1}),
                       lambda a: a['findings'][0].update(evidence=[]),
                       lambda a: a['findings'][0].update(trigger='Check the inputs and consider boundary values.'),
                       lambda a: a['findings'][0].update(cause=''),
                       lambda a: a['findings'][0].update(priority='maybe'),
                       lambda a: a['blocks'][0].update(hunkIndex=99),
                       lambda a: a['blocks'][0].update(evidence=[{'path': 'quota.py', 'side': 'head', 'line': 999}]),
                       lambda a: a.update(reviewedPaths=[]),
                       lambda a: a.update(blocks=[]),
                       lambda a: a['findings'][0].update(anchor=None),
                       lambda a: a['blocks'][0].update(path='../outside.py')]:
            bad = copy.deepcopy(self.analysis)
            mutate(bad)
            with self.subTest(analysis=bad), self.assertRaises((ValueError, FileNotFoundError, KeyError)):
                self.store.save_analysis(self.identity, bad)
        self.assertEqual(self.store.analysis(self.identity)['status'], 'not-run')

    def test_empty_findings_are_distinct_from_no_review_and_partial_scope_is_explicit(self):
        raw = copy.deepcopy(self.analysis)
        raw.update(findings=[], limitations=[])
        saved = self.store.save_analysis(self.identity, raw)
        self.assertEqual(saved['status'], 'agent-reviewed')
        self.assertTrue(saved['coverageComplete'])
        self.assertEqual(self.store.guide(self.identity)['findings'], [])
        raw.update(blocks=[], reviewedPaths=[])
        saved = self.store.save_analysis(self.identity, raw)
        self.assertFalse(saved['coverageComplete'])
        self.assertIsNone(self.store.guide(self.identity)['files'][0]['hunks'][0]['summary'])

    def test_duplicate_blocks_or_findings_do_not_create_repeated_comments(self):
        for key in ('blocks', 'findings'):
            raw = copy.deepcopy(self.analysis)
            raw[key] += copy.deepcopy(raw[key])
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.save_analysis(self.identity, raw)

    def test_http_analysis_requires_explicit_scope_and_preserves_snapshot_evidence(self):
        data = self.root / 'http'
        data.mkdir()
        db = data / 'index.db'
        index_repository(self.repo, db)
        shutil.copytree(self.store.directory, data / 'reviews' / project_id(self.repo))
        server = create_server(db, port=0, data_dir=data)
        # Standalone server discovers this checkout from the index; use the same review artifacts.
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            body = json.dumps(self.analysis).encode()
            request = urllib.request.Request(base + '/api/review/analysis?review=' + self.identity, data=body,
                                             headers={'Content-Type': 'application/json'}, method='PUT')
            with urllib.request.urlopen(request) as response:
                saved = json.load(response)
            self.assertEqual(saved['headSha'], self.head)
            with urllib.request.urlopen(base + '/api/review/guide?review=' + self.identity) as response:
                self.assertEqual(json.load(response)['findings'][0]['trigger'], self.analysis['findings'][0]['trigger'])
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + '/api/review/analysis')
            self.assertEqual(error.exception.code, 400)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)
