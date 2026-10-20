#!/usr/bin/env node
import { spawn, spawnSync } from 'node:child_process';
import { access, lstat, mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { repositoryRoot } from '../mcp/session.mjs';
import { findPython } from './runtime.mjs';

const application = fileURLToPath(new URL('..', import.meta.url));
export const clients = {codex: '.codex/config.toml', claude: '.mcp.json', vscode: '.vscode/mcp.json'};
export const firstPrompt = 'Use Atlas to open this repository, inspect its code and documentation, and generate an evidence-backed feature map for review. Return the Atlas UI link.';

export function configuration(client, repository, python, installation = application, node = process.execPath) {
  if (!Object.hasOwn(clients, client)) throw new Error('Choose --client codex, claude, or vscode.');
  const entry = {command: node, args: [join(installation, 'mcp/server.mjs'), '--repository', repository], env: {ATLAS_PYTHON: python}};
  const toml = `[mcp_servers.atlas]\ncommand = ${JSON.stringify(entry.command)}\nargs = ${JSON.stringify(entry.args)}\n\n[mcp_servers.atlas.env]\nATLAS_PYTHON = ${JSON.stringify(python)}\n`;
  return {entry, text: client === 'codex' ? toml : JSON.stringify({[client === 'vscode' ? 'servers' : 'mcpServers']: {atlas: {type: 'stdio', ...entry}}}, null, 2) + '\n',
    installUrl: client === 'vscode' ? `vscode:mcp/install?${encodeURIComponent(JSON.stringify({name: 'atlas', type: 'stdio', ...entry}))}` : null};
}

export async function mergeConfiguration(client, existing, config) {
  const {parse: parseToml} = await import('smol-toml');
  const {parse, modify, applyEdits} = await import('jsonc-parser');
  const section = client === 'codex' ? 'mcp_servers' : client === 'vscode' ? 'servers' : 'mcpServers';
  let data;
  if (client === 'codex') data = parseToml(existing || '');
  else if (!existing.trim()) data = {};
  else if (client === 'vscode') {
    const errors = [];
    data = parse(existing, errors, {allowTrailingComma: true});
    if (errors.length) throw new Error('Existing VS Code configuration contains invalid JSONC.');
  } else data = JSON.parse(existing);
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Existing configuration must be an object.');
  if (data[section] !== undefined && (!data[section] || typeof data[section] !== 'object' || Array.isArray(data[section]))) throw new Error(`Existing ${section} must be an object.`);
  const old = data[section]?.atlas;
  const wanted = client === 'codex' ? config.entry : {type: 'stdio', ...config.entry};
  if (old !== undefined) {
    // Preserve client-specific timeout, approval and other settings when this binding matches.
    if (old?.command === wanted.command && isDeepStrictEqual(old.args, wanted.args) && old.env?.ATLAS_PYTHON === wanted.env.ATLAS_PYTHON && (!old.type || old.type === 'stdio')) return existing;
    throw new Error('An Atlas entry already exists with different settings. Update or remove it explicitly before running setup again.');
  }
  if (client === 'codex') {
    const result = `${existing}${existing.endsWith('\n') || !existing ? '' : '\n'}\n${config.text}`;
    parseToml(result); // Refuse incompatible inline/dotted TOML table structures before writing.
    return result;
  }
  if (client === 'vscode') return applyEdits(existing.trim() ? existing : '{}', modify(existing.trim() ? existing : '{}', [section, 'atlas'], wanted, {formattingOptions: {insertSpaces: true, tabSize: 2}})) + '\n';
  data[section] = {...data[section], atlas: wanted};
  return JSON.stringify(data, null, 2) + '\n';
}

async function safeTarget(repository, target) {
  const parts = relative(repository, target).split('/');
  if (parts.includes('..')) throw new Error('Setup configuration must stay within the checkout.');
  let cursor = repository;
  for (const part of parts) {
    cursor = join(cursor, part);
    try { if ((await lstat(cursor)).isSymbolicLink()) throw new Error(`Refusing symlinked configuration path: ${cursor}`); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
}

export async function installConfiguration(client, repository, config) {
  const target = join(repository, clients[client]);
  await safeTarget(repository, target);
  let original = '';
  try { original = await readFile(target, 'utf8'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  const merged = await mergeConfiguration(client, original, config);
  if (merged === original) return {target, changed: false};
  await mkdir(dirname(target), {recursive: true});
  await safeTarget(repository, target);
  const temporary = `${target}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, merged, {flag: 'wx', mode: 0o600});
    let current = '';
    try { current = await readFile(target, 'utf8'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (current !== original) throw new Error('Configuration changed during setup. Run setup again.');
    await rename(temporary, target);
  } finally { await unlink(temporary).catch(() => {}); }
  return {target, changed: true};
}

async function run(command, args) {
  await new Promise((resolve, reject) => {
    const child = spawn(command, args, {cwd: application, stdio: ['ignore', 'pipe', 'pipe']});
    child.stdout.pipe(process.stderr, {end: false}); child.stderr.pipe(process.stderr, {end: false});
    child.once('error', reject); child.once('exit', code => code === 0 ? resolve() : reject(new Error(`${command} ${args.join(' ')} failed (${code}).`)));
  });
}

async function main(args) {
  const options = {};
  const flags = new Set();
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    if (!['--client', '--repository', '--dry-run', '--check', '--json', '--help'].includes(flag) || flags.has(flag)) throw new Error(`Unknown or repeated option: ${flag}`);
    flags.add(flag);
    if (['--client', '--repository'].includes(flag)) {
      const value = args[++i]; if (!value || value.startsWith('--')) throw new Error(`${flag} requires a value.`);
      options[flag.slice(2)] = value;
    } else options[flag.slice(2)] = true;
  }
  if (options.help) {
    console.log('Usage: node /path/to/atlas/scripts/setup.mjs --client codex|claude|vscode [--repository /checkout] [--dry-run|--check] [--json]\nDefault: prepare Atlas and install project-local MCP configuration. --dry-run prints the configuration; --check verifies prerequisites. Neither preview mode writes files.');
    return;
  }
  if (options['dry-run'] && options.check) throw new Error('Choose either --dry-run or --check.');
  const client = options.client;
  if (!Object.hasOwn(clients, client)) throw new Error('Choose --client codex, claude, or vscode. Use --help for examples.');
  if (process.platform === 'win32') throw new Error('Atlas service startup currently requires macOS or Linux. Run setup inside WSL or your Linux cloud workspace.');
  const major = Number(process.versions.node.split('.')[0]);
  if (major < 22 || (major === 22 && Number(process.versions.node.split('.')[1]) < 12)) throw new Error('Use Node 22.12+ or 24+.');
  const repository = await repositoryRoot(options.repository || process.cwd());
  const pythonCommand = findPython();
  const resolved = spawnSync(pythonCommand, ['-c', 'import sys; print(sys.executable)'], {encoding: 'utf8'});
  if (resolved.status !== 0) throw new Error('Could not resolve Python executable.');
  const python = resolved.stdout.trim();
  const missing = [];
  for (const [label, path] of [['dependencies', 'node_modules/@modelcontextprotocol/sdk/package.json'], ['setup parser', 'node_modules/smol-toml/package.json'], ['JSONC parser', 'node_modules/jsonc-parser/package.json'], ['built UI', 'dist/index.html']]) {
    try { await access(join(application, path)); } catch { missing.push(label); }
  }
  const config = configuration(client, repository, python);
  let installation;
  if (!options['dry-run'] && !options.check) {
    if (missing.some(label => label !== 'built UI')) await run('npm', ['ci']);
    if (missing.includes('built UI')) await run('npm', ['run', 'build']);
    installation = await installConfiguration(client, repository, config);
  }
  const result = {client, repository, node: process.execPath, python, configurationPath: join(repository, clients[client]),
    ...(installation || {}), configuration: config.text, installUrl: config.installUrl, missing: installation ? [] : missing,
    next: 'Reload the client MCP configuration or start a new session in this checkout. Accept the client’s normal workspace/server trust prompt, then use the starter prompt.', prompt: firstPrompt};
  if (options.json) console.log(JSON.stringify(result, null, 2));
  else if (options.check) console.log(`Repository: ${repository}\nNode: ${process.execPath}\nPython: ${python}\n${missing.length ? `Missing: ${missing.join(', ')}. Run setup without --check to prepare Atlas.` : 'Atlas prerequisites are ready.'}\nConfiguration: ${result.configurationPath}`);
  else if (options['dry-run']) console.log(`Configuration for ${result.configurationPath}:\n\n${config.text}${config.installUrl ? `\nInstall in VS Code: ${config.installUrl}\n` : ''}`);
  else console.log(`${installation.changed ? 'Configured' : 'Already configured'} Atlas for ${client} in ${repository}.\n${result.configurationPath}\n\n${result.next}\n\n${firstPrompt}`);
  if (options.check && missing.length) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).catch(error => { console.error(`Atlas setup: ${error.message}`); process.exitCode = 1; });
}
