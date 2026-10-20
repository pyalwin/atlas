import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { findPython } from './runtime.mjs';

process.chdir(fileURLToPath(new URL('..', import.meta.url)));
const children = [
  spawn(findPython(), ['-m', 'atlas_index', 'serve', '--port', '4318'], { stdio: 'inherit' }),
  spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '4317', '--strictPort'], { stdio: 'inherit' }),
];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  children.forEach(child => child.kill('SIGTERM'));
  process.exitCode = code;
}
children.forEach(child => {
  child.on('error', error => { console.error(error.message); stop(1); });
  child.on('exit', code => stop(code ?? 0));
});
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
