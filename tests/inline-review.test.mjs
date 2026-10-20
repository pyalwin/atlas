import test from 'node:test';
import assert from 'node:assert/strict';
import { findingCommentAnchor, hunkCommentAnchor, locateInlineComment } from '../src/inline-review.mjs';

const diff = {
  path: 'new/name.py', oldPath: 'old/name.py',
  hunks: [
    { lines: [
      { kind: 'context', oldLine: 10, newLine: 10 },
      { kind: 'removed', oldLine: 11, newLine: null },
      { kind: 'added', oldLine: null, newLine: 11 },
      { kind: 'added', oldLine: null, newLine: 12 },
    ] },
    { lines: [{ kind: 'removed', oldLine: 30, newLine: null }] },
  ],
};

test('block comments anchor to changed source, including deletion-only hunks and renames', () => {
  assert.deepEqual(hunkCommentAnchor(diff, 0), { path: diff.path, sourcePath: diff.path, side: 'head', line: 11, hunkIndex: 0 });
  assert.deepEqual(hunkCommentAnchor(diff, 1), { path: diff.path, sourcePath: diff.oldPath, side: 'base', line: 30, hunkIndex: 1 });
  assert.equal(hunkCommentAnchor(diff, 3), null);
});

test('same numbered lines on opposite sides retain independent comment locations', () => {
  assert.deepEqual(locateInlineComment(diff, { path: diff.path, side: 'base', line: 11, hunkIndex: 0 }), { hunkIndex: 0, lineIndex: 1 });
  assert.deepEqual(locateInlineComment(diff, { path: diff.path, side: 'head', line: 11, hunkIndex: 0 }), { hunkIndex: 0, lineIndex: 2 });
  assert.equal(locateInlineComment(diff, { path: diff.path, side: 'head', line: 30, hunkIndex: 1 }), null);
  assert.equal(locateInlineComment(diff, { path: 'other.py', side: 'head', line: 11, hunkIndex: 0 }), null);
});

test('findings only become inline comments when their evidence occurs in this exact block', () => {
  const found = findingCommentAnchor(diff, { evidence: [{ path: diff.oldPath, side: 'base', line: 11 }] }, 0);
  assert.deepEqual(found, { path: diff.path, sourcePath: diff.oldPath, side: 'base', line: 11, hunkIndex: 0 });
  assert.equal(findingCommentAnchor(diff, { evidence: [{ path: diff.path, side: 'head', line: 900 }] }, 0), null);
  assert.equal(findingCommentAnchor(diff, { evidence: [{ path: diff.oldPath, side: 'base', line: 30 }] }, 0), null);
  assert.equal(findingCommentAnchor(diff, { evidence: [{ path: 'other.py', side: 'head', line: 11 }] }, 0), null);
});
