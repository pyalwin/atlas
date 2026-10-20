"""JavaScript/TypeScript syntax parsing via the bundled Babel parser."""
from __future__ import annotations

import json
import os
import posixpath
import shutil
import subprocess
from pathlib import Path

SUFFIXES = {'.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts'}
SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'scan-javascript.mjs'


def scan_batch(files):
    node = os.environ.get('ATLAS_NODE') or shutil.which('node')
    if not node:
        return [], ['JavaScript and TypeScript analysis needs Node.js; these files were indexed without symbols or import links.']
    try:
        result = subprocess.run([node, str(SCRIPT)], input=json.dumps(files), capture_output=True,
                                text=True, timeout=90, check=True)
        parsed = json.loads(result.stdout)
        return parsed, [item['error'] for item in parsed if item.get('error')]
    except (OSError, ValueError, subprocess.SubprocessError):
        return [], ['JavaScript and TypeScript analysis was unavailable. Run npm install in Atlas; these files were indexed without symbols or import links.']


def resolve_import(path, specifier, indexed_paths):
    if not isinstance(specifier, str) or not specifier.startswith(('./', '../')):
        return None
    target = posixpath.normpath(posixpath.join(posixpath.dirname(path), specifier))
    if target.startswith('../') or target == '..':
        return None
    candidates = [target]
    suffix = Path(target).suffix
    substitutes = {'.js': ('.ts', '.tsx'), '.jsx': ('.tsx', '.ts'), '.mjs': ('.mts',), '.cjs': ('.cts',)}
    if suffix in substitutes:
        candidates += [target[:-len(suffix)] + ext for ext in substitutes[suffix]]
    if not suffix:
        candidates += [target + ext for ext in ('.ts', '.tsx', '.js', '.jsx', '.mts', '.mjs', '.cts', '.cjs', '.json')]
        candidates += [target + '/index' + ext for ext in ('.ts', '.tsx', '.js', '.jsx', '.mts', '.mjs', '.cts', '.cjs')]
    return next((candidate for candidate in candidates if candidate in indexed_paths), None)
