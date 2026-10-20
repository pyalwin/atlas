import test from 'node:test';
import assert from 'node:assert/strict';
import {findingComment, reviewRequest} from '../src/review-findings.mjs';
import {findingCommentAnchor} from '../src/inline-review.mjs';

test('an actionable comment preserves the failure, trigger, effect and correction', () => {
  const finding = {priority: 'P1', title: 'Exhausted quotas become allowances', cause: 'The clamp is replaced by an absolute difference.', trigger: 'Usage exceeds the limit.', consequence: 'Further requests are accepted.', fix: 'Clamp the remainder to zero.'};
  const body = findingComment(finding);
  for (const text of Object.values(finding)) assert.ok(body.includes(text));
  assert.ok(!body.includes('Check the inputs'));
});
test('the authoring request pins the comparison and asks for behavioral TLDRs', () => {
  const body = reviewRequest('repo-a', {id: '0123456789abcdef', baseSha: 'base', headSha: 'head'});
  for (const text of ['repo-a', '0123456789abcdef', 'base', 'head', 'get_review_context', 'save_review_analysis', 'Zero findings is valid']) assert.ok(body.includes(text));
});
test('supporting callers cannot move a finding away from its changed-line anchor', () => {
  const diff = {path: 'new.py', oldPath: 'old.py', hunks: [{lines: [{kind: 'removed', oldLine: 2}, {kind: 'added', newLine: 3}]}]};
  assert.deepEqual(findingCommentAnchor(diff, {evidence: [{path: 'old.py', side: 'base', line: 2}]}, 0), {path: 'new.py', sourcePath: 'old.py', side: 'base', line: 2, hunkIndex: 0});
});
