export const DEFAULT_REVIEW_QUESTIONS = [
  { id: 'purpose', prompt: 'Is the intended change clear, and do you agree it is needed?' },
  { id: 'impact', prompt: 'Do you understand and accept the effect on callers and existing behavior?' },
  { id: 'correctness', prompt: 'Have the changed code and compatibility risks been addressed?' },
  { id: 'tests', prompt: 'Does the validation address the important success and failure cases?' },
];

const text = value => typeof value === 'string' ? value.trim() : '';
const choices = new Set(['yes', 'no', 'unsure']);

const REFLECTION_QUESTIONS = [
  { id: 'explain-behavior', prompt: 'Walk through one input whose behavior changes between these revisions.' },
  { id: 'explain-caller', prompt: 'Which caller could observe this change, and what would it receive?' },
  { id: 'explain-boundary', prompt: 'Choose a boundary case from the changed code. How is it handled?' },
  { id: 'explain-test', prompt: 'Which test expectation supports this change, and what does it leave unproven?' },
].map(question => ({ ...question, kind: 'reflection', context: 'Use a concrete example from the pinned source.',
  explanation: 'Compare your explanation with the relevant diff, callers, and test expectations. Describe what the source establishes and what still needs verification.',
  choices: [], correctChoiceId: null, evidence: [] }));

export function questionsForGuide(guide) {
  const usable = (Array.isArray(guide?.questions) ? guide.questions : [])
    .filter(question => text(question?.id) && text(question?.prompt));
  return [...new Map((usable.length ? usable : REFLECTION_QUESTIONS).map(question => [question.id, question])).values()];
}

export function questionAssessment(question, answer) {
  if (question.kind === 'choice') {
    const valid = Array.isArray(question.choices) && question.choices.some(item => item.id === answer?.choice);
    const submitted = valid && answer?.submitted === true;
    const correct = submitted && answer.choice === question.correctChoiceId;
    return { answered: submitted, complete: correct, correct, submitted };
  }
  if (question.kind === 'reflection') {
    const written = text(answer?.response).split(/\s+/).filter(Boolean).length >= 4;
    return { answered: written && answer?.submitted === true, complete: written && answer?.submitted === true && answer?.confirmed === true, selfAssessed: true };
  }
  const choice = typeof answer === 'object' ? answer?.choice : answer;
  return { answered: choices.has(choice), complete: choice === 'yes', correct: choice === 'yes', legacyChoice: choice };
}

/** A local recommendation from explicit human decisions, never a GitHub approval. */
export function evaluateReview({
  review, guide, reviewedFiles = [], answers = {}, fileConcerns = {},
  inlineComments = [], ci = null, ciLoading = false, validation = {}, limitations = {}, acknowledged,
} = {}) {
  const files = Array.isArray(review?.files) ? review.files : [];
  const knownPaths = new Set(files.map(file => file.path));
  const marks = new Set((reviewedFiles instanceof Set ? [...reviewedFiles] : Array.isArray(reviewedFiles) ? reviewedFiles : [])
    .filter(path => knownPaths.has(path)));
  const questions = questionsForGuide(guide);
  const assessmentFor = question => questionAssessment(question, answers[question.id]);
  const total = Math.max(files.length, Number.isFinite(review?.stats?.changedFiles) ? review.stats.changedFiles : 0);
  const progress = { reviewed: marks.size, total, answered: questions.filter(question => assessmentFor(question).answered).length, understood: questions.filter(question => assessmentFor(question).complete).length, questions: questions.length };
  const reasons = [], advisories = [];
  const add = (id, label, detail, step) => reasons.push({ id, label, detail, step });
  let changesRequested = false;
  if (!review?.id || !guide || guide.reviewId !== review.id || guide.baseSha !== review.baseSha || guide.headSha !== review.headSha) {
    add('scope', 'The review brief is not ready', 'Load the explanation for these exact base and head commits.', 'brief');
  }
  if (!total) {
    return { status: 'no_changes', label: 'No changes to review', summary: 'These revisions contain no changed files. Choose a different comparison.',
      reasons, advisories, progress, canApprove: false };
  }
  if (files.length < total || review?.truncated?.files) {
    add('omitted-files', 'Some changed files are missing from this scope', 'Use a smaller comparison or inspect the full PR before recording a verdict.', 'files');
  }
  if (marks.size < files.length) {
    add('files', `${files.length - marks.size} file${files.length - marks.size === 1 ? '' : 's'} still need review`,
      'Read each file’s explanation, diff, and watchpoints, then mark it reviewed.', 'files');
  }
  const limitedFiles = files.filter(file => file.excluded || file.binary || file.diffTruncated);
  if ((limitedFiles.length || review?.truncated?.diffs) && !(limitations.acknowledged === true && text(limitations.note))) {
    add('limited-source', 'Some source changes need inspection outside Atlas',
      'Record where you inspected unavailable or truncated diffs and what you concluded.', 'tests');
  }
  if (review?.truncated?.nodes || review?.truncated?.edges || review?.truncated?.evidence || review?.truncated?.symbols) {
    advisories.push('The code map or definition list is sampled. Your impact assessment must account for omitted detail.');
  }
  for (const file of files) {
    const concern = fileConcerns[file.path];
    const note = text(typeof concern === 'string' ? concern : concern?.text);
    if (!note || concern?.resolved === true) continue;
    if (concern?.blocking === true) changesRequested = true;
    add(`concern:${file.path}`, `Open concern in ${file.path}`, note, 'files');
  }
  for (const question of questions) {
    const assessment = assessmentFor(question);
    if (assessment.complete) continue;
    const choice = assessment.legacyChoice;
    if (choice === 'no') changesRequested = true;
    add(`question:${question.id}`,
      assessment.answered ? 'Revisit a change in behavior' : choice === 'no' ? 'A reviewer disagrees with the change' : choice === 'unsure' ? 'A review question needs clarification' : 'A review question is unanswered',
      question.prompt, 'questions');
  }
  for (const comment of Array.isArray(inlineComments) ? inlineComments : []) {
    if (!knownPaths.has(comment.path) || !text(comment.body) || comment.resolved === true) continue;
    if (comment.blocking === true) changesRequested = true;
    add(`comment:${comment.id}`, `Open comment in ${comment.path}:${comment.line}`, comment.body, 'files');
  }
  const scopedCI = ci?.reviewId === review?.id && ci?.baseSha === review?.baseSha && ci?.headSha === review?.headSha;
  if (ciLoading) add('ci-loading', 'Refreshing GitHub check results', 'Wait for the current results before deciding.', 'tests');
  if (scopedCI && ci.status === 'failed') {
    changesRequested = true;
    add('ci-failed', 'GitHub checks report a failure', 'Inspect the failed checks on this exact head commit.', 'tests');
  } else if (scopedCI && ci.status === 'pending') {
    add('ci-pending', 'GitHub checks are still running', 'Refresh the check results before deciding.', 'tests');
  }
  if (validation.source === 'github-actions' && (!scopedCI || ci.status !== 'passed' || ci.complete === false)) {
    add('ci-evidence', 'Refresh the recorded GitHub evidence', 'The current check results must confirm the recorded result for these commits.', 'tests');
  }
  if (validation.status === 'failed') {
    changesRequested = true;
    add('validation', 'Validation has failed', text(validation.note) || 'Resolve the failing checks and record the new result.', 'tests');
  } else if (!['passed', 'not_applicable'].includes(validation.status) || !text(validation.note)) {
    add('validation', 'Validation evidence is not recorded',
      'Record the checks and their result, or explain why running tests is not applicable. Changed test code alone is not a passing result.', 'tests');
  }
  if (acknowledged) {
    for (const step of ['brief', 'impact', 'tests']) {
      if (acknowledged[step] !== true) add(`ack:${step}`, `Confirm the ${step === 'brief' ? 'purpose' : step} assessment`,
        'Read the evidence and record your understanding.', step);
    }
  }
  const status = changesRequested ? 'changes_requested' : reasons.length ? 'needs_review' : 'ready';
  return {
    status,
    label: status === 'ready' ? 'Ready to approve' : status === 'changes_requested' ? 'Changes requested' : 'Review incomplete',
    summary: status === 'ready'
      ? 'Your file reviews, answers, and recorded validation support approval of these commits. Nothing has been submitted to GitHub.'
      : status === 'changes_requested'
        ? 'An objection, blocking concern, or failed check needs resolution before approval.'
        : 'Resolve the remaining questions and evidence gaps before deciding whether to approve.',
    reasons, advisories, progress, canApprove: status === 'ready',
  };
}
