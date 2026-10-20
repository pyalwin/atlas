import test from 'node:test';
import assert from 'node:assert/strict';
import {primaryPath, workflowLayout} from '../src/workflow-layout.mjs';

const journey = {start: 'index', mainPath: ['index','search','read','answer'], steps: [
 {id:'index', choices:[{next:'search'}]}, {id:'search',choices:[{next:'read'},{next:'missing'}]},
 {id:'read',choices:[{next:'answer'},{next:'stale'}]}, {id:'answer',choices:[]},
 {id:'missing',choices:[{next:'alternative'}]}, {id:'alternative',choices:[]}, {id:'stale',choices:[]}
]};

test('the initial diagram shows the core lifecycle without extra tutorials', () => {
 const layout = workflowLayout(journey);
 assert.deepEqual(primaryPath(journey), journey.mainPath);
 assert.equal(layout.nodes.length,4);
 for (const edge of layout.links) assert.ok(layout.positions.get(edge.to).x > layout.positions.get(edge.from).x);
});
test('expansion reveals alternatives while keeping their parent and main route', () => {
 const layout = workflowLayout(journey,['search']);
 assert.ok(layout.positions.has('missing'));
 assert.equal(layout.positions.has('stale'),false);
 assert.ok(layout.links.some(link=>link.from==='search'&&link.to==='missing'));
});
test('collapsing a parent hides deeper expanded branches without orphan nodes', () => {
 assert.ok(workflowLayout(journey,['search','missing']).positions.has('alternative'));
 const collapsed=workflowLayout(journey,['missing']);
 assert.equal(collapsed.positions.has('missing'),false);
 assert.equal(collapsed.positions.has('alternative'),false);
});
test('legacy walkthroughs derive a bounded first route', () => {
 assert.deepEqual(primaryPath({...journey,mainPath:undefined}),journey.mainPath);
 assert.deepEqual(primaryPath({start:'a',steps:[{id:'a',choices:[{next:'a'}]}]}),['a']);
});
