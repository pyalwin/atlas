export function findingComment(finding) {
  return [`[${finding.priority}] ${finding.title}`, finding.cause,
    `When: ${finding.trigger}`, `Effect: ${finding.consequence}`, `Suggested correction: ${finding.fix}`].join('\n\n');
}

export function reviewRequest(project, review) {
  return `Use Atlas MCP to review project ${project}, review ${review.id}, base ${review.baseSha}, head ${review.headSha}. Call get_review_context and follow its reviewGuide. Read pinned diffs, surrounding source, affected callers and tests on both revisions. Write concise behavioral TLDRs for changed blocks, not English translations of expressions or lists of variable names. Report only concrete defects introduced by this change, with their trigger, cause, consequence, correction and changed-line evidence. Zero findings is valid. Disclose missing evidence and tests not run. Save the result with save_review_analysis for this exact scope, then return the Atlas review link. Keep all analysis in this session; do not post to GitHub.`;
}

export const findingKey = finding => finding.fingerprint || JSON.stringify(finding);
