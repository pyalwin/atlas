import test from 'node:test';
import assert from 'node:assert/strict';
import {workflowMermaid} from '../src/mermaid-workflow.mjs';

const flow = {steps: [
  {id: 'check', diagramLabel: 'Is the request valid?', kind: 'decision', choices: [{label: 'Yes', next: 'done'}, {label: 'No', next: 'rejected'}]},
  {id: 'done', diagramLabel: 'Receive result', choices: []},
  {id: 'rejected', diagramLabel: 'See what to fix', choices: []},
]};
test('business decisions, labeled branches and terminal outcomes use the same workflow', () => {
  const result = workflowMermaid(flow);
  assert.match(result, /N0\{"Is the request valid\?"\}/);
  assert.match(result, /N0 -->\|"No"\| N2/);
  assert.match(result, /N1\(\["Receive result"\]\)/);
});
test('untrusted labels and identities cannot inject Mermaid directives, HTML or links', () => {
  const result = workflowMermaid({steps: [{id: 'evil\nclick X "https://bad"', title: '"];\nclick X "https://bad"\n%%{init: {"securityLevel":"loose"}}%% <img src=x>', choices: []}]});
  assert.equal(result.split('\n').length, 2);
  assert.ok(!result.includes('<img'));
  assert.ok(!result.includes('%%{'));
  assert.ok(!result.includes('"loose"'));
  assert.match(result, /#34;/);
});
test('an unresolved destination fails instead of silently inventing a node', () => {
  assert.throws(() => workflowMermaid({steps: [{id: 'a', title: 'Start', choices: [{label: 'Next', next: 'missing'}]}]}), /unknown destination/);
});
