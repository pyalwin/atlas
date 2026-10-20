import json
import sqlite3
import subprocess
import tempfile
import unittest
from collections import Counter
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from atlas_index.discovery import discover_product_map
from atlas_index.indexer import index_repository
from atlas_index.product import Taxonomy, matches
from atlas_index.projects import validate_map


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'example'
        self.root.mkdir()
        self.db = self.base / 'index.db'
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)

    def index(self, files):
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        index_repository(self.root, self.db)
        return discover_product_map(self.db, self.root)

    def assert_valid(self, raw):
        validate_map(raw)
        taxonomy = Taxonomy(raw)
        owners = {}
        for feature in raw['features']:
            self.assertLessEqual(len(feature['capabilities']), 8)
            peer_paths = []
            for capability in feature['capabilities']:
                self.assertTrue(capability['paths'])
                self.assertLessEqual(len(capability['paths']), 200)
                for path in capability['paths']:
                    self.assertTrue(any(matches(path, parent) for parent in feature['paths']))
                    self.assertFalse(any(matches(path, other) or matches(other, path) for other in peer_paths))
                peer_paths.extend(capability['paths'])
        for item in [*raw['features'], *raw['foundations']]:
            self.assertTrue(item['paths'])
            self.assertLessEqual(len(item['paths']), 200)
            self.assertEqual(item['status'], 'suggested')
            self.assertFalse(item['reviewed'])
            self.assertIn(item['confidence'], ('medium', 'low'))
            for path in item['paths']:
                self.assertNotIn(path, owners)
                owners[path] = item['id']
        with closing(sqlite3.connect(self.db)) as db:
            counts = Counter()
            for (path,) in db.execute("SELECT path FROM nodes WHERE kind='file'"):
                state, owner, _ = taxonomy.classify(path)
                counts['foundationFiles' if owner and owner.startswith('foundation:')
                       else 'productFiles' if owner else state + 'Files'] += 1
        for key in ('productFiles', 'foundationFiles', 'excludedFiles', 'unmappedFiles'):
            self.assertEqual(raw['analysis'][key], counts[key], key)
        self.assertLessEqual(len(raw['features']), 30)
        self.assertLessEqual(len(raw['foundations']), 8)
        return taxonomy

    def test_python_cross_layer_features_and_shared_foundations(self):
        raw = self.index({
            'src/app/features/billing/__init__.py': '"""Track invoices for each customer."""\n',
            'src/app/features/billing/invoices/send.py': 'def send(): pass\n',
            'src/app/features/billing/refunds/refund.py': 'def refund(): pass\n',
            'services/billing/invoices/save.py': 'def save(): pass\n',
            'routes/billing/route.py': 'def route(): pass\n',
            'src/app/features/orders/create.py': 'def create(): pass\n',
            'src/app/features/billing/models/invoice.py': 'class Invoice: pass\n',
            'src/auth/session.py': 'def login(): pass\n',
            'src/common/format.py': 'def format_value(): pass\n',
            'src/db/connection.py': 'def connect(): pass\n',
            'src/app/features/billing/tests/test_send.py': 'def test_send(): pass\n',
            'deploy/start.py': 'print("deployment")\n',
            'tools/rebuild.py': 'print("tooling")\n',
        })
        taxonomy = self.assert_valid(raw)
        self.assertEqual({item['id'] for item in raw['features']}, {'billing', 'orders'})
        for path in ('routes/billing/route.py', 'services/billing/invoices/save.py',
                     'src/app/features/billing/invoices/send.py'):
            self.assertEqual(taxonomy.classify(path)[1], 'product:billing')
        self.assertEqual(taxonomy.classify('src/auth/session.py')[1], 'foundation:identity')
        self.assertEqual(taxonomy.classify('src/db/connection.py')[1], 'foundation:storage')
        self.assertEqual(taxonomy.classify('src/app/features/billing/models/invoice.py')[1], 'foundation:storage')
        self.assertEqual(taxonomy.classify('src/common/format.py')[1], 'foundation:shared')
        self.assertEqual(taxonomy.classify('src/app/features/billing/tests/test_send.py')[0], 'excluded')
        self.assertEqual(taxonomy.classify('deploy/start.py')[0], 'excluded')
        self.assertEqual(taxonomy.classify('tools/rebuild.py')[0], 'excluded')
        self.assertEqual(raw['analysis']['relationshipsCreated'], 0)
        billing = next(item for item in raw['features'] if item['id'] == 'billing')
        invoices = next(item for item in billing['capabilities'] if item['id'] == 'invoices')
        self.assertIn('services/billing/invoices', invoices['paths'])
        self.assertIn('src/app/features/billing/invoices', invoices['paths'])

    def test_typescript_packages_wrappers_and_file_only_coverage(self):
        raw = self.index({
            'apps/web/src/features/cart/actions/add.ts': 'export function add() { return 1; }\n',
            'apps/web/src/features/cart/page.tsx': 'export default function Cart() { return null; }\n',
            'packages/@shop/cart/src/checkout/submit.ts': 'export const submit = () => true;\n',
            'routes/billing_routes.ts': 'export const bills = [];\n',
            'services/billing_service.ts': 'export const pay = () => true;\n',
            'apps/web/src/features/cart/components/Card.tsx': 'export const Card = () => null;\n',
            'apps/web/src/features/cart/cart.spec.ts': 'throw new Error("test only");\n',
            'package.json': '{"name":"example","scripts":{"postinstall":"never run"}}\n',
        })
        taxonomy = self.assert_valid(raw)
        self.assertEqual({item['id'] for item in raw['features']}, {'cart', 'billing'})
        self.assertEqual(taxonomy.classify('packages/@shop/cart/src/checkout/submit.ts')[1], 'product:cart')
        self.assertEqual(taxonomy.classify('routes/billing_routes.ts')[1], 'product:billing')
        self.assertEqual(taxonomy.classify('services/billing_service.ts')[1], 'product:billing')
        self.assertEqual(taxonomy.classify('apps/web/src/features/cart/cart.spec.ts')[0], 'excluded')
        self.assertEqual(taxonomy.classify('apps/web/src/features/cart/components/Card.tsx')[1], 'foundation:presentation')
        ts = next(item for item in raw['analysis']['languages'] if item['name'] == 'TypeScript')
        with closing(sqlite3.connect(self.db)) as db:
            status = json.loads(db.execute("SELECT value FROM metadata WHERE key='status'").fetchone()[0])
        reported = next((item for item in status.get('languageAnalysis', []) if item['name'] == 'TypeScript'), {})
        self.assertEqual(ts['mode'], reported.get('mode', 'file-only'))
        self.assertEqual(ts['parsedFiles'], reported.get('parsedFiles'))
        self.assertNotIn('edges', raw)

    def test_repository_namespace_and_generic_wrappers_do_not_become_features(self):
        raw = self.index({
            'src/example/app/domain/inventory/items.py': 'def items(): pass\n',
            'apps/backend/src/modules/shipping/labels.py': 'def labels(): pass\n',
            'src/example/__init__.py': '',
        })
        self.assert_valid(raw)
        self.assertEqual({item['id'] for item in raw['features']}, {'inventory', 'shipping'})
        self.assertFalse(any(item['name'] in {'Src', 'App', 'Example', 'Modules'} for item in raw['features']))

    def test_flat_repository_fallback_and_exact_readme_evidence(self):
        raw = self.index({
            'README.md': '# Receipt tool\n\nCollect receipts in a local folder.\n\n## Install\n\nRun the tool.\n',
            'main.py': '#!/usr/bin/env python\n"""Read receipts from disk."""\nraise RuntimeError("must not execute")\n',
            'parse.py': 'def parse(value): return value\n',
        })
        self.assert_valid(raw)
        self.assertEqual(len(raw['features']), 1)
        feature = raw['features'][0]
        self.assertEqual(feature['name'], 'Application Code')
        self.assertEqual(feature['confidence'], 'low')
        self.assertIn('README.md says:', feature['summary'])
        proof = next(item for item in feature['evidence'] if item['kind'] == 'readme')
        self.assertEqual((proof['path'], proof['line'], proof['endLine']),
                         ('README.md', 3, 3))
        self.assertEqual(proof['excerpt'], 'Collect receipts in a local folder.')
        module = next(item for item in feature['evidence'] if item['kind'] == 'module-docstring')
        self.assertEqual((module['path'], module['line']), ('main.py', 2))
        with patch('subprocess.run', side_effect=AssertionError('Discovery must not run commands')):
            again = discover_product_map(self.db, self.root)
        self.assertEqual(raw, again)

    def test_only_generic_folders_still_have_a_useful_area(self):
        raw = self.index({'src/app/main.py': 'print("hello")\n', 'src/app/__init__.py': ''})
        taxonomy = self.assert_valid(raw)
        self.assertEqual(len(raw['features']), 1)
        self.assertEqual(taxonomy.classify('src/app/main.py')[1], 'product:repository-code')
        self.assertNotIn('src', raw['features'][0]['paths'])

    def test_feature_and_capability_limits_keep_ownership_accurate(self):
        files = {f'features/area_{number}/main.py': 'def run(): pass\n' for number in range(35)}
        files.update({f'features/large/part_{number}/run.py': 'def run(): pass\n' for number in range(12)})
        raw = self.index(files)
        self.assert_valid(raw)
        self.assertEqual(len(raw['features']), 30)
        self.assertTrue(raw['discovery']['featureLimitReached'])
        self.assertEqual(raw['analysis']['unmappedFiles'], 0)
        self.assertTrue(any(item['name'] == 'Other Application Code' for item in raw['features']))
        large = next(item for item in raw['features'] if item['id'] == 'large')
        self.assertEqual(len(large['capabilities']), 8)

    def test_path_limits_leave_files_unmapped_instead_of_misattributing_them(self):
        files = {f'routes/feature_{number}/main.py': 'def run(): pass\n' for number in range(240)}
        files.update({f'routes/feature_{number}/config.py': 'CONFIG = True\n' for number in range(240)})
        raw = self.index(files)
        self.assert_valid(raw)
        self.assertTrue(raw['discovery']['pathLimitReached'])
        self.assertGreater(raw['analysis']['unmappedFiles'], 0)
        self.assertTrue(all(len(item['paths']) <= 200 for item in raw['features'] + raw['foundations']))

    def test_stale_symlink_evidence_cannot_read_outside_repository(self):
        self.index({'billing/README.md': '# Billing\n\nOriginal description.\n',
                    'billing/main.py': 'def run(): pass\n'})
        outside = self.base / 'private.md'
        outside.write_text('DO NOT EXPOSE THIS OUTSIDE TEXT')
        readme = self.root / 'billing' / 'README.md'
        readme.unlink()
        readme.symlink_to(outside)
        raw = discover_product_map(self.db, self.root)
        self.assertNotIn('DO NOT EXPOSE', json.dumps(raw))
        self.assertFalse(any(item['path'] == 'billing/README.md'
                             for feature in raw['features'] for item in feature['evidence']))
        self.assert_valid(raw)

    def test_unrelated_index_and_unsafe_stored_paths_are_rejected_or_skipped(self):
        self.index({'main.py': 'print("hello")\n'})
        other = self.base / 'other'
        other.mkdir()
        with self.assertRaisesRegex(ValueError, 'different repository'):
            discover_product_map(self.db, other)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
                       ('file:escape', 'root', 'escape', 'file', '../outside.py', 0,
                        json.dumps({'language': 'Python'})))
            db.commit()
        raw = discover_product_map(self.db, self.root)
        self.assertEqual(raw['analysis']['skippedUnsafePaths'], 1)
        self.assertEqual(raw['analysis']['unmappedFiles'], 1)
        self.assertNotIn('../outside.py', json.dumps(raw))

    def test_support_only_and_empty_repositories_do_not_invent_features(self):
        raw = self.index({'auth.py': 'def authenticate(): pass\n', 'utils.py': 'def helper(): pass\n'})
        self.assert_valid(raw)
        self.assertEqual(raw['features'], [])
        self.assertEqual(len(raw['foundations']), 2)
        self.assertTrue(any('No product feature boundary' in note for note in raw['notes']))

    def test_non_ascii_and_long_names_keep_distinct_valid_ids(self):
        first, second = '付款', '账户'
        long_name = 'payment-' * 25
        raw = self.index({f'features/{first}/main.py': 'pass\n',
                          f'features/{second}/main.py': 'pass\n',
                          f'features/{long_name}/main.py': 'pass\n'})
        self.assert_valid(raw)
        self.assertEqual(len(raw['features']), 3)
        self.assertEqual(len({feature['id'] for feature in raw['features']}), 3)
        self.assertTrue(all(len(feature['id']) <= 100 for feature in raw['features']))
        self.assertTrue(all(len(feature['name']) <= 160 for feature in raw['features']))

    def test_language_analysis_preserves_partial_support_and_file_only_languages(self):
        self.index({'main.py': 'pass\n', 'billing/view.ts': 'export const view = 1;\n',
                    'billing/native.go': 'package billing\n'})
        with closing(sqlite3.connect(self.db)) as db:
            status = json.loads(db.execute("SELECT value FROM metadata WHERE key='status'").fetchone()[0])
            status['languageAnalysis'] = [
                {'name': 'Python', 'files': 1, 'parsedFiles': 1, 'mode': 'semantic'},
                {'name': 'TypeScript', 'files': 1, 'parsedFiles': 1, 'mode': 'imports-and-symbols'},
                {'name': 'Go', 'files': 1, 'parsedFiles': 0, 'mode': 'file-only'},
            ]
            db.execute("UPDATE metadata SET value=? WHERE key='status'", (json.dumps(status),))
            db.commit()
        raw = discover_product_map(self.db, self.root)
        self.assertEqual(raw['analysis']['semanticLanguages'], ['Python'])
        self.assertEqual(raw['analysis']['importsAndSymbolsLanguages'], ['TypeScript'])
        self.assertEqual(raw['analysis']['fileOnlyLanguages'], ['Go'])
        self.assertTrue(any('Files only: Go.' in note for note in raw['notes']))


if __name__ == '__main__':
    unittest.main()
