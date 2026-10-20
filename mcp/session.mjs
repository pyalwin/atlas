import { spawnSync } from 'node:child_process';
import { realpath } from 'node:fs/promises';

// Resolve the checkout once at launch; tool arguments cannot switch repositories.
export async function repositoryRoot(path) {
  const result = spawnSync('git', ['-C', await realpath(path), 'rev-parse', '--show-toplevel'], {encoding: 'utf8', timeout: 10000});
  if (result.status !== 0) throw new Error('Atlas must be launched inside a Git checkout or with --repository /checkout.');
  return realpath(result.stdout.trim());
}

import { spawn } from 'node:child_process';
import { lstat, mkdir, readFile, readdir, open, access, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { findPython } from '../scripts/runtime.mjs';

export function localOrigin(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Atlas API must be a plain localhost HTTP origin.');
  }
  return url.origin;
}

async function localStorage(repository) {
  const directory = join(repository, '.atlas');
  try { if ((await lstat(directory)).isSymbolicLink()) throw new Error('Atlas storage must not be a symlink.'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  await mkdir(directory, {recursive: true});
  async function check(path) {
    for (const entry of await readdir(path, {withFileTypes: true})) {
      if (entry.isSymbolicLink()) throw new Error('Atlas storage must not contain symlinks.');
      if (entry.isDirectory()) await check(join(path, entry.name));
    }
  }
  await check(directory);
  // Ignore all generated data without editing the checkout's tracked .gitignore.
  await writeFile(join(directory, '.gitignore'), '*\n', {flag: 'wx'}).catch(error => { if (error.code !== 'EEXIST') throw error; });
  return directory;
}

export async function probeService(url, repository, expectedInstance) {
  try {
    const origin = localOrigin(url);
    const response = await fetch(`${origin}/api/service`, {redirect: 'error', signal: AbortSignal.timeout(1500)});
    if (!response.ok) return null;
    const info = await response.json();
    if (info.service !== 'atlas-repository' || info.version !== 1 || info.repository !== repository ||
        info.dataDirectory !== join(repository, '.atlas') || localOrigin(info.url) !== origin ||
        typeof info.instance !== 'string' || (expectedInstance && info.instance !== expectedInstance)) return null;
    return info;
  } catch { return null; }
}

export async function ensureRepositoryService(repository, explicitUrl) {
  const directory = await localStorage(repository);
  if (explicitUrl) {
    localOrigin(explicitUrl);
    const info = await probeService(explicitUrl, repository);
    if (!info) throw new Error('The requested Atlas service is unavailable or not bound to this repository with repository-local storage.');
    return info;
  }
  async function discover() {
    try {
      const saved = JSON.parse(await readFile(join(directory, 'service.json'), 'utf8'));
      return await probeService(saved.url, repository, saved.instance);
    } catch { return null; }
  }
  const existing = await discover();
  if (existing) return existing;
  const application = fileURLToPath(new URL('..', import.meta.url));
  try { await access(join(application, 'dist/index.html')); }
  catch {
    await new Promise((resolve, reject) => {
      const build = spawn('npm', ['run', 'build'], {cwd: application, stdio: ['ignore', 'pipe', 'pipe']});
      build.stdout.pipe(process.stderr, {end: false}); build.stderr.pipe(process.stderr, {end: false});
      build.once('error', reject);
      build.once('exit', code => code === 0 ? resolve() : reject(new Error('Atlas UI build failed. Run npm install and npm run build in the Atlas installation.')));
    });
  }
  const log = await open(join(directory, 'server.log'), 'a');
  let child;
  let spawnError;
  try {
    child = spawn(findPython(), ['-m', 'atlas_index', 'repository-serve', repository], {
      cwd: application, detached: true, stdio: ['ignore', log.fd, log.fd]
    });
    child.once('error', error => { spawnError = error; });
    child.unref();
  } finally { await log.close(); }
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (spawnError) throw spawnError;
    const info = await discover();
    if (info) return info;
    if (child.exitCode !== null && child.exitCode !== 0) throw new Error(`Atlas service failed to start. See ${join(directory, 'server.log')}`);
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  throw new Error(`Atlas service startup timed out. See ${join(directory, 'server.log')}`);
}
