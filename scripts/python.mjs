import { spawn } from 'node:child_process';
import { findPython } from './runtime.mjs';

try {
  const child = spawn(findPython(), process.argv.slice(2), { stdio: 'inherit' });
  child.on('error', error => { console.error(error.message); process.exitCode = 1; });
  child.on('exit', code => { process.exitCode = code ?? 1; });
  process.on('SIGINT', () => child.kill('SIGINT'));
  process.on('SIGTERM', () => child.kill('SIGTERM'));
} catch (error) { console.error(error.message); process.exitCode = 1; }
