// Comments keep the changed file's canonical path for grouping and the original
// source path for base-side evidence in renamed files.
export function hunkCommentAnchor(diff, hunkIndex, oldPath) {
  const hunk = diff?.hunks?.[hunkIndex];
  if (!hunk) return null;
  const line = hunk.lines.find(item => item.kind === 'added' && item.newLine)
    || hunk.lines.find(item => item.kind === 'removed' && item.oldLine)
    || hunk.lines.find(item => item.newLine || item.oldLine);
  if (!line) return null;
  const side = line.newLine ? 'head' : 'base';
  return {
    path: diff.path, sourcePath: side === 'base' ? diff.oldPath || oldPath || diff.path : diff.path,
    side, line: side === 'head' ? line.newLine : line.oldLine, hunkIndex,
  };
}

export function locateInlineComment(diff, comment) {
  if (!diff || comment.path !== diff.path || !['base', 'head'].includes(comment.side)
      || !Number.isInteger(comment.line) || comment.line < 1) return null;
  const field = comment.side === 'base' ? 'oldLine' : 'newLine';
  const candidateIndices = Number.isInteger(comment.hunkIndex)
    ? [comment.hunkIndex] : (diff.hunks || []).map((_, index) => index);
  for (const hunkIndex of candidateIndices) {
    const lineIndex = diff.hunks?.[hunkIndex]?.lines.findIndex(line => line[field] === comment.line);
    if (lineIndex >= 0) return { hunkIndex, lineIndex };
  }
  return null;
}

export function findingCommentAnchor(diff, finding, hunkIndex, oldPath) {
  for (const evidence of finding?.evidence || []) {
    const side = evidence.side || 'head';
    const sourcePath = side === 'base' ? diff?.oldPath || oldPath || diff?.path : diff?.path;
    if (evidence.path !== sourcePath) continue;
    const anchor = { path: diff.path, sourcePath, side, line: evidence.line, hunkIndex };
    if (locateInlineComment(diff, anchor)) return anchor;
  }
  // A finding without an in-block source location must not be pinned to an
  // arbitrary line and presented as an exact location.
  return null;
}
