import { spawnSync } from 'node:child_process';

export function findPython() {
  const candidates = process.env.ATLAS_PYTHON ? [process.env.ATLAS_PYTHON] : ['/opt/homebrew/bin/python3', 'python3', 'python'];
  for (const candidate of candidates) {
    const check = spawnSync(candidate, ['-c', 'import sys; print(sys.version_info >= (3, 12))'], { encoding: 'utf8' });
    if (check.status === 0 && check.stdout.trim() === 'True') return candidate;
  }
  throw new Error('Atlas needs Python 3.12 or newer. Install it or set ATLAS_PYTHON to its executable path.');
}
