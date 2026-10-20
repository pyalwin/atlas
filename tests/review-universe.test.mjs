import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { Universe, buildReviewCallGraph } from '../src/Universe.js';

const ref = (side, line, path = 'src/parse.py') => ({ path, side, line });
const definition = (side, connections, path = 'src/parse.py') => ({
  id: 'def:src/parse.py:parse', label: 'parse()', path, kind: 'function',
  side, evidence: [ref(side, side === 'base' ? 10 : 20, path)], connections,
});
const call = (name, side, line) => ({
  id: `def:src/parse.py:parse:call:${name}`, label: `${name}()`, path: 'src/parse.py',
  kind: 'call-expression', side, evidence: [ref(side, line)], connections: [],
});
const connection = (name, change, side, line) => ({
  target: `def:src/parse.py:parse:call:${name}`, label: 'calls', change, evidence: [ref(side, line)],
});
function fixture() {
  return { impact: {
    before: [
      definition('base', [connection('split', 'removed', 'base', 11), connection('strip', 'unchanged', 'base', 12), connection('cached', 'unchanged', 'base', 13)]),
      call('split', 'base', 11), call('strip', 'base', 12), call('cached', 'base', 13),
    ],
    after: [
      definition('head', [connection('scan', 'added', 'head', 21), connection('strip', 'unchanged', 'head', 22), connection('normalize', 'unchanged', 'head', 23)]),
      call('scan', 'head', 21), call('strip', 'head', 22), call('normalize', 'head', 23),
    ],
  } };
}

test('call union retains one node and connection per identity with both exact snapshot locations', () => {
  const map = buildReviewCallGraph(fixture());
  assert.equal(map.nodes.length, 6);
  assert.equal(map.edges.length, 5);
  assert.equal(new Set(map.nodes.map(node => node.id)).size, 6);
  const strip = map.nodes.find(node => node.name === 'strip()');
  assert.deepEqual(strip.evidence, [ref('base', 12), ref('head', 22)]);
  assert.deepEqual(strip.reviewSides, { base: true, head: true });
  assert.deepEqual(map.edges.find(edge => edge.target === strip.id).evidence, strip.evidence);
  assert.ok(map.nodes.every(node => node.id.startsWith('review-call:')));
});

test('a missing sampled call on either side does not imply addition or removal', () => {
  const map = buildReviewCallGraph(fixture());
  assert.equal(map.nodes.find(node => node.name === 'cached()').status, 'unchanged');
  assert.equal(map.nodes.find(node => node.name === 'normalize()').status, 'unchanged');
  assert.equal(map.nodes.find(node => node.name === 'split()').status, 'removed');
  assert.equal(map.nodes.find(node => node.name === 'scan()').status, 'added');
});

test('missing targets and invalid source locations are not invented', () => {
  const guide = fixture();
  guide.impact.after[0].connections.push(connection('missing', 'added', 'head', 100));
  guide.impact.after[1].evidence.push({ path: 'src/parse.py', side: 'head', line: -1 });
  const map = buildReviewCallGraph(guide);
  assert.equal(map.edges.some(edge => edge.target.endsWith(':missing')), false);
  assert.equal(map.nodes.some(node => node.evidence.some(location => location.line < 1)), false);
});

test('fallback file descriptions do not become invented definition or call planets', () => {
  const guide = { impact: { before: [], after: [{ id: 'file:README.md', path: 'README.md', label: 'README.md', kind: 'documentation', evidence: [{ path: 'README.md', side: 'head', line: 1 }], connections: [] }] } };
  assert.deepEqual(buildReviewCallGraph(guide), { nodes: [], edges: [] });
});

test('renamed definition retains base source path and selects head path for its display', () => {
  const guide = fixture();
  guide.impact.before[0].path = 'src/old_parse.py';
  guide.impact.before[0].evidence = [ref('base', 10, 'src/old_parse.py')];
  const node = buildReviewCallGraph(guide).nodes.find(item => item.reviewAnchor);
  assert.equal(node.path, 'src/parse.py');
  assert.deepEqual(node.evidence, [ref('base', 10, 'src/old_parse.py'), ref('head', 20)]);
});

function lensScene() {
  const scene = Object.create(Universe.prototype);
  const implementation = { id: 'file:src/parse.py', path: 'src/parse.py', reviewChanged: true, reviewNode: true, reviewTest: false, name: 'parse.py' };
  const changedTest = { id: 'file:tests/test_parse.py', path: 'tests/test_parse.py', reviewChanged: true, reviewNode: true, reviewTest: true, name: 'test_parse.py' };
  const contextTest = { id: 'file:tests/test_caller.py', path: 'tests/test_caller.py', reviewNode: true, reviewTest: true, name: 'test_caller.py' };
  const map = buildReviewCallGraph(fixture());
  scene.nodes = [implementation, changedTest, contextTest, ...map.nodes];
  scene.visuals = new Map(scene.nodes.map(node => [node.id, { node }]));
  scene.reviewHasCalls = true;
  scene.reviewMode = true;
  scene.reviewRevision = 'changes';
  scene.selectedId = changedTest.id;
  return { scene, implementation, changedTest, contextTest, map };
}

test('tests remain supplemental evidence in every lens, including a selected test file', () => {
  const { scene, changedTest, contextTest } = lensScene();
  for (const step of ['brief', 'impact', 'files', 'tests', 'questions', 'verdict']) {
    scene.reviewStep = step;
    assert.equal(scene.reviewNodeVisible(changedTest), false, step);
    assert.equal(scene.reviewNodeVisible(contextTest), false, step);
    assert.equal(scene.reviewEdgeVisible({ source: changedTest.id, target: 'file:src/parse.py', change: 'added' }), false, step);
  }
  scene.reviewTestOnly = true;
  assert.equal(scene.reviewNodeVisible(scene.nodes[0]), false);
});

test('Impact reveals the call layer and each revision uses the explicit connection change', () => {
  const { scene, implementation, map } = lensScene();
  scene.reviewStep = 'impact';
  assert.equal(scene.reviewNodeVisible(implementation), false);
  assert.equal(scene.reviewNodeVisible(map.nodes[0]), true);
  const added = map.edges.find(edge => edge.change === 'added');
  const removed = map.edges.find(edge => edge.change === 'removed');
  const sampled = map.edges.find(edge => edge.target.endsWith(':cached'));
  scene.reviewRevision = 'base';
  assert.equal(scene.reviewEdgeVisible(added), false);
  assert.equal(scene.reviewEdgeVisible(removed), true);
  scene.reviewRevision = 'head';
  assert.equal(scene.reviewEdgeVisible(added), true);
  assert.equal(scene.reviewEdgeVisible(removed), false);
  assert.equal(scene.reviewEdgeVisible(sampled), true, 'Known unchanged call stays unchanged despite one sampled location');
  scene.reviewStep = 'files';
  assert.equal(scene.reviewNodeVisible(implementation), true);
  assert.equal(scene.reviewNodeVisible(map.nodes[0]), false);
});

test('changing lenses and revisions preserves scene positions and camera', () => {
  const { scene } = lensScene();
  scene.renderer = {};
  scene.camera = { position: new THREE.Vector3(0, 20, 900) };
  scene.controls = { target: new THREE.Vector3(20, 30, 0) };
  scene.positions = new Map(scene.nodes.map((node, index) => [node.id, new THREE.Vector3(index * 10, index * 5, 0)]));
  const positions = [...scene.positions].map(([id, position]) => [id, position.clone()]);
  const camera = scene.camera.position.clone(), target = scene.controls.target.clone();
  for (const visual of scene.visuals.values()) {
    Object.assign(visual, {
      group: { visible: true }, color: new THREE.Color('#ffffff'),
      body: { material: { color: new THREE.Color('#ffffff') } },
      glow: { material: { color: new THREE.Color('#ffffff') } },
      count: { textContent: '', style: {} }, label: { style: {}, setAttribute() {} },
    });
  }
  scene.requestFrame = () => {};
  scene.select = id => { scene.selectedId = id; };
  const testPaths = new Set(['tests/test_parse.py']);
  for (const step of ['brief', 'impact', 'files', 'tests']) {
    for (const revision of ['base', 'changes', 'head']) scene.setReviewState({ step, revision, selectedId: 'file:src/parse.py', testPaths });
  }
  assert.deepEqual(scene.camera.position, camera);
  assert.deepEqual(scene.controls.target, target);
  assert.deepEqual([...scene.positions], positions);
});

test('a feature system keeps even one planet away from its central sun', async () => {
  const { productPositionsFor } = await import('../src/Universe.js');
  const node = {id: 'capability:greetings:format'};
  assert.ok(productPositionsFor([node], true).get(node.id).length() > 100);
  assert.equal(productPositionsFor([node], false).get(node.id).length(), 0);
});
