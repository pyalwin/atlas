import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, mkdir, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { configuration, mergeConfiguration, installConfiguration } from '../scripts/setup.mjs';
import { parse as parseToml } from 'smol-toml';

const config = client => configuration(client, '/work/project with spaces', '/usr/bin/python3', '/tools/atlas with spaces', '/usr/bin/node');

test('all clients bind the exact checkout and use explicit executables', () => {
  for (const client of ['codex', 'claude', 'vscode']) {
    const generated = config(client);
    assert.equal(generated.entry.command, '/usr/bin/node');
    assert.deepEqual(generated.entry.args, ['/tools/atlas with spaces/mcp/server.mjs', '--repository', '/work/project with spaces']);
    assert.equal(generated.entry.env.ATLAS_PYTHON, '/usr/bin/python3');
  }
  const decoded = JSON.parse(decodeURIComponent(config('vscode').installUrl.split('?')[1]));
  assert.equal(decoded.name, 'atlas');
  assert.equal(decoded.type, 'stdio');
  assert.deepEqual(decoded.args, config('vscode').entry.args);
});

test('Codex preserves comments and other MCP servers, then becomes idempotent', async () => {
  const original = '# existing\nmodel = "example"\n[mcp_servers.other]\ncommand = "other"\n';
  const result = await mergeConfiguration('codex', original, config('codex'));
  assert.ok(result.startsWith(original));
  assert.equal(parseToml(result).mcp_servers.other.command, 'other');
  assert.equal(await mergeConfiguration('codex', result, config('codex')), result);
});

test('JSONC comments and unrelated settings survive VS Code setup', async () => {
  const result = await mergeConfiguration('vscode', '{\n// keep this\n"inputs": [],\n"servers": {"other": {"command": "other"}},\n}', config('vscode'));
  assert.match(result, /keep this/);
  assert.match(result, /"other"/);
  assert.equal(await mergeConfiguration('vscode', result, config('vscode')), result);
});

test('Claude merge preserves other servers and rejects conflicting or malformed configuration', async () => {
  const result = await mergeConfiguration('claude', '{"mcpServers":{"other":{"command":"other"}},"custom":true}', config('claude'));
  assert.equal(JSON.parse(result).mcpServers.other.command, 'other');
  assert.equal(JSON.parse(result).custom, true);
  assert.equal(await mergeConfiguration('claude', result, config('claude')), result);
  await assert.rejects(mergeConfiguration('claude', '{"mcpServers":{"atlas":{"command":"other"}}}', config('claude')), /already exists/);
  await assert.rejects(mergeConfiguration('vscode', '{ invalid', config('vscode')), /invalid JSONC/);
  await assert.rejects(mergeConfiguration('codex', '[mcp_servers."atlas"]\ncommand="other"', config('codex')), /already exists/);
  await assert.rejects(mergeConfiguration('claude', '{"mcpServers":[]}', config('claude')), /must be an object/);
});

test('installer updates only its project config and refuses symlinked paths', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'atlas-install-'));
  try {
    const original = '{"mcpServers":{"existing":{"command":"keep"}}}\n';
    await writeFile(join(directory, '.mcp.json'), original);
    const installed = await installConfiguration('claude', directory, config('claude'));
    assert.equal(installed.changed, true);
    assert.equal(JSON.parse(await readFile(installed.target, 'utf8')).mcpServers.existing.command, 'keep');
    assert.equal((await installConfiguration('claude', directory, config('claude'))).changed, false);
    const outside = join(directory, 'outside');
    await mkdir(outside);
    await symlink(outside, join(directory, '.vscode'));
    await assert.rejects(installConfiguration('vscode', directory, config('vscode')), /symlink/);
  } finally { await rm(directory, {recursive: true, force: true}); }
});

test('one-command setup works in a fresh checkout and reruns without duplication', () => {
  // CLI behavior is tested in a disposable checkout, never the developer's client settings.
  const created = spawnSync(process.execPath, ['--input-type=module', '-e', "import {mkdtempSync} from 'node:fs'; import {tmpdir} from 'node:os'; console.log(mkdtempSync(tmpdir()+'/atlas-cli-'))"], {encoding: 'utf8'});
  const repository = created.stdout.trim();
  try {
    assert.equal(spawnSync('git', ['init', '-q', repository]).status, 0);
    for (let i = 0; i < 2; i++) {
      const result = spawnSync(process.execPath, ['scripts/setup.mjs', '--client', 'claude', '--repository', repository, '--json'], {encoding: 'utf8'});
      assert.equal(result.status, 0, result.stderr);
      const data = JSON.parse(result.stdout);
      assert.equal(data.changed, i === 0);
      assert.match(data.configurationPath, /\.mcp\.json$/);
      assert.ok(data.prompt.includes('Use Atlas'));
    }
    const bad = spawnSync(process.execPath, ['scripts/setup.mjs', '--client', 'unknown'], {encoding: 'utf8'});
    assert.equal(bad.status, 1);
  } finally { spawnSync(process.execPath, ['--input-type=module', '-e', 'import {rmSync} from "node:fs"; rmSync(process.argv[1], {recursive:true,force:true})', repository]); }
});
