import copy
import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from atlas_index.review_ci import CIUnavailable, NoRedirect, _github_json, _github_link, _pages, get_ci
from atlas_index.server import create_server

HEAD = 'a' * 40
BASE = 'b' * 40
IDENTITY = '0123456789abcdef'
REPOSITORY = 'example/project'


def workflow(identity=1, **changes):
    return {'id': identity, 'name': 'Tests', 'status': 'completed', 'conclusion': 'success',
            'head_sha': HEAD, 'repository': {'full_name': REPOSITORY},
            'workflow_id': 12, 'run_attempt': 1, 'event': 'pull_request',
            'html_url': f'https://github.com/{REPOSITORY}/actions/runs/{identity}', **changes}


def check(identity=2, **changes):
    return {'id': identity, 'name': 'Python 3.13', 'status': 'completed', 'conclusion': 'success',
            'head_sha': HEAD, 'app': {'id': 10, 'name': 'GitHub Actions'},
            'html_url': f'https://github.com/{REPOSITORY}/runs/{identity}', **changes}


class ReviewCITests(unittest.TestCase):
    def setUp(self):
        self.review = {'id': IDENTITY, 'baseSha': BASE, 'headSha': HEAD,
                       'url': 'https://github.com/example/project/pull/42'}
        self.store = SimpleNamespace(root='/unused/repository', _review=lambda identity: copy.deepcopy(self.review))
        self.origin = patch('atlas_index.review_ci.github_origin', return_value=REPOSITORY)
        self.origin.start()
        self.addCleanup(self.origin.stop)

    def load(self, runs=None, checks=None):
        def fetch(endpoint):
            key, rows = ('workflow_runs', runs or []) if '/actions/runs?' in endpoint else ('check_runs', checks or [])
            self.assertIn(HEAD, endpoint)
            self.assertNotIn('merge', endpoint)
            return {'total_count': len(rows), key: rows}
        with patch('atlas_index.review_ci._github_json', side_effect=fetch):
            return get_ci(self.store, IDENTITY)

    def test_pins_scope_and_reports_success_without_claiming_coverage(self):
        result = self.load([workflow()], [check()])
        self.assertEqual((result['reviewId'], result['baseSha'], result['headSha']), (IDENTITY, BASE, HEAD))
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['complete'])
        self.assertEqual(result['checks'][0]['sha'], HEAD)
        self.assertIn('sufficiently tested', ' '.join(result['limitations']))
        self.assertTrue(result['checkedAt'].endswith('Z'))

    def test_rejects_missing_and_mismatched_review_scope(self):
        for identity, field, value in [('', None, None), (IDENTITY, 'id', 'other'),
                                       (IDENTITY, 'headSha', 'HEAD'), (IDENTITY, 'baseSha', '../../branch')]:
            with self.subTest(identity=identity, field=field):
                review = copy.deepcopy(self.review)
                if field:
                    self.review[field] = value
                with patch('atlas_index.review_ci._github_json') as fetch, self.assertRaises(ValueError):
                    get_ci(self.store, identity)
                fetch.assert_not_called()
                self.review = review

    def test_changed_origin_or_untrusted_pr_url_cannot_attach_ci(self):
        for url in ['https://github.com/other/project/pull/42', 'https://evil.test/example/project/pull/42']:
            self.review['url'] = url
            with patch('atlas_index.review_ci._github_json') as fetch, self.assertRaises(ValueError):
                get_ci(self.store, IDENTITY)
            fetch.assert_not_called()

    def test_non_github_origin_does_not_make_network_request(self):
        with patch('atlas_index.review_ci.github_origin', return_value=None), patch('atlas_index.review_ci._github_json') as fetch:
            result = get_ci(self.store, IDENTITY)
        self.assertEqual(result['status'], 'unavailable')
        self.assertFalse(result['complete'])
        fetch.assert_not_called()

    def test_merge_commit_and_wrong_repository_results_are_excluded(self):
        result = self.load([workflow(), workflow(3, head_sha='c' * 40)],
                           [check(repository={'full_name': 'other/project'})])
        self.assertEqual(len(result['runs']), 1)
        self.assertEqual(result['checks'], [])
        self.assertEqual(result['status'], 'unavailable')
        self.assertFalse(result['complete'])
        self.assertIn('exact head SHA', ' '.join(result['limitations']))

    def test_failure_pending_none_and_skipped_are_distinct(self):
        for runs, expected in [([workflow(conclusion='failure')], 'failed'),
                               ([workflow(status='in_progress', conclusion=None)], 'pending'),
                               ([], 'none'), ([workflow(conclusion='skipped')], 'unavailable'),
                               ([workflow(conclusion='neutral')], 'unavailable'),
                               ([workflow(status='completed', conclusion=None)], 'unavailable')]:
            with self.subTest(expected=expected, runs=runs):
                self.assertEqual(self.load(runs)['status'], expected)

    def test_newest_workflow_and_check_attempts_replace_old_failures(self):
        result = self.load([workflow(1, conclusion='failure'), workflow(3)],
                           [check(2, conclusion='failure'), check(4)])
        self.assertEqual(result['status'], 'passed')
        self.assertEqual([row['id'] for row in result['runs']], [3])
        self.assertEqual([row['id'] for row in result['checks']], [4])

    def test_separate_events_and_providers_keep_their_results(self):
        result = self.load([workflow(1, conclusion='failure'), workflow(3, event='push')],
                           [check(2, conclusion='failure'), check(4, app={'id': 11, 'name': 'Other CI'})])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['runs']), 2)
        self.assertEqual(len(result['checks']), 2)

    def test_partial_transport_failure_does_not_convert_success_to_passed(self):
        def fetch(endpoint):
            if '/actions/runs?' in endpoint:
                raise CIUnavailable('GitHub rate limited this request.')
            return {'total_count': 1, 'check_runs': [check()]}
        with patch('atlas_index.review_ci._github_json', side_effect=fetch):
            result = get_ci(self.store, IDENTITY)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(len(result['checks']), 1)
        self.assertFalse(result['complete'])

    def test_known_failure_survives_incomplete_result(self):
        with patch('atlas_index.review_ci._pages', return_value=([workflow(conclusion='failure')], False, ['Incomplete'])):
            result = get_ci(self.store, IDENTITY)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['complete'])

    def test_pagination_constructs_each_request_and_caps_results(self):
        seen = []
        def fetch(endpoint):
            seen.append(endpoint)
            page = int(parse_qs(urlsplit(endpoint).query)['page'][0])
            return {'total_count': 350, 'workflow_runs': [workflow(page * 100 + i) for i in range(100)],
                    'next': 'https://evil.test/steal'}
        with patch('atlas_index.review_ci._github_json', side_effect=fetch):
            rows, complete, limitations = _pages('repos/example/project/actions/runs', 'workflow_runs', {'head_sha': HEAD})
        self.assertEqual(len(rows), 300)
        self.assertEqual(len(seen), 3)
        self.assertTrue(all(endpoint.startswith('repos/example/project/actions/runs?') for endpoint in seen))
        self.assertFalse(complete)
        self.assertTrue(limitations)

    def test_pagination_fetches_subsequent_page_and_keeps_earlier_rows_on_failure(self):
        with patch('atlas_index.review_ci._github_json', side_effect=[
            {'total_count': 2, 'workflow_runs': [workflow()]}, CIUnavailable('Unavailable')]):
            rows, complete, limitations = _pages('repos/example/project/actions/runs', 'workflow_runs', {'head_sha': HEAD})
        self.assertEqual(len(rows), 1)
        self.assertFalse(complete)
        self.assertEqual(limitations, ['Unavailable'])

    def test_unexpected_json_and_rows_do_not_falsely_report_no_ci(self):
        for data in [{}, {'total_count': True, 'workflow_runs': []},
                     {'total_count': 1, 'workflow_runs': ['bad']},
                     {'total_count': 2, 'workflow_runs': []}]:
            with self.subTest(data=data), patch('atlas_index.review_ci._github_json', return_value=data):
                _, complete, limitations = _pages('repos/example/project/actions/runs', 'workflow_runs', {})
            self.assertFalse(complete)
            self.assertTrue(limitations)

    def test_only_same_repository_https_links_survive(self):
        for link in ['javascript:alert(1)', 'https://github.com.evil.test/example/project/runs/1',
                     'https://github.com@evil.test/example/project/runs/1',
                     'https://github.com/other/project/runs/1', 'https://github.com:443/example/project/runs/1',
                     'https://github.com/example/project/runs/1\n']:
            self.assertIsNone(_github_link(link, REPOSITORY))
        result = self.load([workflow(html_url='https://evil.test/redirect')])
        self.assertIsNone(result['runs'][0]['url'])
        self.assertEqual(result['status'], 'passed')

    def test_public_request_is_fixed_host_get_and_redirects_are_disabled(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"total_count": 0, "workflow_runs": []}'
        opener = Mock()
        opener.open.return_value = response
        with patch('atlas_index.review_ci.urllib.request.build_opener', return_value=opener) as build:
            _github_json('repos/example/project/actions/runs?head_sha=' + HEAD)
        self.assertIs(build.call_args.args[0], NoRedirect)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(urlsplit(request.full_url).netloc, 'api.github.com')
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test'))

    def test_existing_gh_authentication_is_read_only_fallback(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError('network information must not be returned')
        def command(args, **kwargs):
            self.assertEqual(args[:7], ['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                                       'repos/example/project/actions/runs'])
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            self.assertEqual(kwargs['env']['GH_PROMPT_DISABLED'], '1')
            kwargs['stdout'].write(json.dumps({'total_count': 0, 'workflow_runs': []}).encode())
            return SimpleNamespace(returncode=0)
        with patch('atlas_index.review_ci.urllib.request.build_opener', return_value=opener), \
                patch('atlas_index.review_ci.shutil.which', return_value='/usr/bin/gh'), \
                patch('atlas_index.review_ci.subprocess.run', side_effect=command):
            self.assertEqual(_github_json('repos/example/project/actions/runs')['total_count'], 0)

    def test_missing_auth_rate_limit_error_is_safe(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('https://api.github.com', 403, 'secret detail', {}, None)
        with patch('atlas_index.review_ci.urllib.request.build_opener', return_value=opener), \
                patch('atlas_index.review_ci.shutil.which', return_value=None), self.assertRaises(CIUnavailable) as error:
            _github_json('repos/example/project/actions/runs')
        self.assertIn('rate-limited', str(error.exception))
        self.assertNotIn('secret detail', str(error.exception))

    def test_http_route_requires_and_returns_the_displayed_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'atlas.db'
            db.touch()
            registry = SimpleNamespace(active='project', close=lambda: None,
                project=lambda identity=None: {'id': 'project', 'path': str(root), 'indexedAt': 'now'},
                paths=lambda identity: {'db': db, 'map': root / 'map.json', 'cache': root / 'cache'})
            self.store.close = lambda: None
            with patch('atlas_index.server.GraphStore'), patch('atlas_index.server.ProductStore'), \
                    patch('atlas_index.server.ReviewStore', return_value=self.store):
                server = create_server(db, port=0, registry=registry)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                url = f'http://127.0.0.1:{server.server_port}/api/review/ci?project=project'
                try:
                    with patch('atlas_index.review_ci._pages', return_value=([], True, [])):
                        with urllib.request.urlopen(url + '&review=' + IDENTITY) as response:
                            result = json.load(response)
                    self.assertEqual(result['reviewId'], IDENTITY)
                    self.assertEqual(result['headSha'], HEAD)
                    self.assertEqual(result['status'], 'none')
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(url)
                    self.assertEqual(error.exception.code, 400)
                    error.exception.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()


if __name__ == '__main__':
    unittest.main()
