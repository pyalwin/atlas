import { test } from 'node:test';
import assert from 'node:assert/strict';
import { realpath } from 'node:fs/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { createAtlasMcp } from '../mcp/server.mjs';

test('MCP negotiation, scoping, evidence and candidate submission', async () => {
  const root = await realpath('.');
  const requests = [];
  const project = { id: 'allowed', path: root, state: 'ready', indexedAt: 'v1' };
  const server = await createAtlasMcp({ repositories: [root], fetcher: async (url, options) => {
    requests.push({url, options});
    let data = {};
    if (url.pathname === '/api/projects') data = options.method === 'POST' ? {project} : { projects: [project, {id: 'hidden', path: '/tmp'}] };
    if (url.pathname.endsWith('/map')) data = {map: {version: 1}, suggestion: null};
    if (url.pathname.endsWith('/candidate')) data = {suggestion: JSON.parse(options.body).map};
    return {ok: true, json: async () => data};
  }});
  const client = new Client({name: 'test', version: '1'});
  const [a, b] = InMemoryTransport.createLinkedPair();
  await server.connect(a); await client.connect(b);
  try {
    const {tools} = await client.listTools();
    assert.equal(tools.length, 10);
    assert.equal(tools.find(t => t.name === 'save_map_candidate').annotations.readOnlyHint, false);
    const listed = await client.callTool({name: 'list_projects', arguments: {}});
    assert.equal(JSON.parse(listed.content[0].text).projects.length, 1);
    const denied = await client.callTool({name: 'get_source', arguments: {project: 'hidden', id: 'file:x'}});
    assert.equal(denied.isError, true);
    assert.equal(requests.some(r => r.url.pathname === '/api/source'), false);
    const context = await client.callTool({name: 'get_summary_context', arguments: {project: 'allowed'}});
    assert.equal(JSON.parse(context.content[0].text).project.indexedAt, 'v1');
    const saved = await client.callTool({name: 'save_map_candidate', arguments: {project: 'allowed', indexedAt: 'v1', agent: 'test', map: {version: 1}}});
    assert.equal(saved.isError, undefined);
    assert.equal(requests.at(-1).options.method, 'PUT');
    assert.deepEqual(tools.find(t => t.name === 'open_project').inputSchema.properties, {});
    const opened = await client.callTool({name: 'open_project', arguments: {}});
    assert.equal(JSON.parse(opened.content[0].text).uiUrl, 'http://127.0.0.1:4318/?project=allowed');
    assert.equal(JSON.parse(requests.at(-1).options.body).path, root);
  } finally { await client.close(); await server.close(); }
});

test('requires explicit roots and rejects remote API origins', async () => {
  await assert.rejects(createAtlasMcp({repositories: []}), /exactly one/);
  await assert.rejects(createAtlasMcp({repositories: ['.', '/tmp']}), /exactly one/);
  await assert.rejects(createAtlasMcp({repositories: ['.'], baseUrl: 'https://example.com'}), /localhost/);
});

test('stdio entry point negotiates without stdout pollution', async () => {
  const { StdioClientTransport } = await import('@modelcontextprotocol/sdk/client/stdio.js');
  const transport = new StdioClientTransport({command: process.execPath,
    args: ['mcp/server.mjs', '--repository', process.cwd()], stderr: 'pipe'});
  const client = new Client({name: 'stdio-test', version: '1'});
  try {
    await client.connect(transport);
    const {tools} = await client.listTools();
    assert.equal(tools.length, 10);
  } finally { await client.close(); }
});

test('launch binding resolves the Git root and rejects non-checkouts', async () => {
  const { repositoryRoot } = await import('../mcp/session.mjs');
  const { mkdtemp, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  assert.equal(await repositoryRoot('mcp'), await realpath('.'));
  const temporary = await mkdtemp(join(tmpdir(), 'atlas-no-git-'));
  try { await assert.rejects(repositoryRoot(temporary), /Git checkout/); }
  finally { await rm(temporary, {recursive: true}); }
});

test('repository service starts once, serves UI, survives MCP disconnect, and recovers stale discovery', {timeout: 60000}, async () => {
  const { ensureRepositoryService, probeService } = await import('../mcp/session.mjs');
  const { mkdtemp, rm, readFile, writeFile } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { spawnSync } = await import('node:child_process');
  const { StdioClientTransport } = await import('@modelcontextprotocol/sdk/client/stdio.js');
  const root = await realpath(await mkdtemp(join(tmpdir(), 'atlas-service-')));
  const outside = await realpath(await mkdtemp(join(tmpdir(), 'atlas-other-')));
  spawnSync('git', ['init', '-q', root]);
  spawnSync('git', ['init', '-q', outside]);
  await writeFile(join(root, 'app.py'), 'def hello():\n    return 1\n');
  let info;
  async function stop(service) {
    if (!service) return;
    try { process.kill(service.pid, 'SIGTERM'); } catch {}
    for (let i = 0; i < 100; i++) {
      if (!(await probeService(service.url, root, service.instance))) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error('Test service did not stop.');
  }
  try {
    const [first, second] = await Promise.all([ensureRepositoryService(root), ensureRepositoryService(root)]);
    info = first;
    assert.equal(first.instance, second.instance);
    assert.equal(info.dataDirectory, join(root, '.atlas'));
    const ui = await fetch(info.url);
    assert.equal(ui.status, 200);
    assert.match(await ui.text(), /<html/);
    assert.equal(await probeService(info.url, outside), null);
    await assert.rejects(ensureRepositoryService(outside, info.url), /not bound/);
    const rejected = await fetch(`${info.url}/api/projects`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: outside})});
    assert.equal(rejected.status, 400);
    const client = new Client({name: 'repository-test', version: '1'});
    try {
      await client.connect(new StdioClientTransport({command: process.execPath,
        args: ['mcp/server.mjs', '--repository', root], stderr: 'pipe'}));
      const opened = await client.callTool({name: 'open_project', arguments: {}});
      assert.equal(opened.isError, undefined, JSON.stringify(opened));
      assert.equal(JSON.parse(opened.content[0].text).uiUrl, `${info.url}/?project=${info.project}`);
    } finally { await client.close(); }
    assert.equal((await ensureRepositoryService(root)).instance, info.instance);
    await stop(info);
    // Simulate a descriptor left by an unclean shutdown.
    await writeFile(join(root, '.atlas/service.json'), JSON.stringify(info));
    const restarted = await ensureRepositoryService(root);
    assert.notEqual(restarted.instance, info.instance);
    info = restarted;
    const registry = JSON.parse(await readFile(join(root, '.atlas/projects.json'), 'utf8'));
    assert.equal(registry.projects[0]._db, join(root, '.atlas/atlas.db'));
  } finally {
    await stop(info);
    await rm(root, {recursive: true, force: true});
    await rm(outside, {recursive: true, force: true});
  }
});
