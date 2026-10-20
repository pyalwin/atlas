import test from 'node:test';
import assert from 'node:assert/strict';
import { evaluateReview, DEFAULT_REVIEW_QUESTIONS, questionsForGuide } from '../src/review-verdict.mjs';

function complete(overrides = {}) {
  return {
    review: { id: 'scope1', baseSha: 'base', headSha: 'head', files: [{ path: 'a.py' }, { path: 'test_a.py' }], stats: { changedFiles: 2 } },
    guide: { reviewId: 'scope1', baseSha: 'base', headSha: 'head', questions: DEFAULT_REVIEW_QUESTIONS },
    reviewedFiles: ['a.py', 'test_a.py'],
    answers: Object.fromEntries(DEFAULT_REVIEW_QUESTIONS.map(question => [question.id, 'yes'])),
    validation: { status: 'passed', note: 'CI run 42: unit and integration checks passed on head.' },
    ...overrides,
  };
}

test('fresh reviewer state never approves', () => {
  const state = complete({ reviewedFiles: [], answers: {}, validation: {} });
  const verdict = evaluateReview(state);
  assert.equal(verdict.status, 'needs_review');
  assert.equal(verdict.canApprove, false);
  assert.equal(verdict.progress.answered, 0);
});

test('approval requires all files, comprehension and explicit validation', () => {
  assert.equal(evaluateReview(complete()).status, 'ready');
  for (const changes of [{ reviewedFiles: ['a.py'] }, { answers: {} }, { validation: { status: 'passed', note: '  ' } }]) {
    assert.equal(evaluateReview(complete(changes)).canApprove, false);
  }
});

test('unsure is unresolved, disagreement requests changes', () => {
  const state = complete();
  state.answers.impact = 'unsure';
  assert.equal(evaluateReview(state).status, 'needs_review');
  state.answers.impact = 'no';
  assert.equal(evaluateReview(state).status, 'changes_requested');
});

test('failed tests request changes even when every answer says yes', () => {
  assert.equal(evaluateReview(complete({ validation: { status: 'failed', note: 'Regression fails.' } })).status, 'changes_requested');
});

test('not applicable requires an explicit rationale', () => {
  assert.equal(evaluateReview(complete({ validation: { status: 'not_applicable' } })).canApprove, false);
  assert.equal(evaluateReview(complete({ validation: { status: 'not_applicable', note: 'Documentation-only typo; rendered output inspected.' } })).canApprove, true);
});

function quizState() {
  const state = complete();
  state.guide = { ...state.guide, version: 4, questions: [
    { id: 'q-call', kind: 'choice', prompt: 'Which parser does the updated caller use?', choices: [{ id: 'new', text: 'The quoted-string parser' }, { id: 'old', text: 'The old splitter' }], correctChoiceId: 'new' },
  ] };
  state.answers = { 'q-call': { choice: 'new', submitted: true } };
  return state;
}

test('comprehension requires a submitted, source-correct answer and mistakes are not objections', () => {
  const state = quizState();
  assert.equal(evaluateReview(state).canApprove, true);
  state.answers['q-call'] = { choice: 'new', submitted: false };
  assert.equal(evaluateReview(state).canApprove, false);
  state.answers['q-call'] = { choice: 'old', submitted: true };
  const wrong = evaluateReview(state);
  assert.equal(wrong.status, 'needs_review');
  assert.equal(wrong.progress.answered, 1);
  assert.equal(wrong.progress.understood, 0);
  state.answers['q-call'] = { choice: 'invented', submitted: true };
  assert.equal(evaluateReview(state).progress.answered, 0);
});

test('changed question identity cannot inherit a previous correct response', () => {
  const state = quizState();
  state.guide.questions[0].id = 'q-new-fingerprint';
  assert.equal(evaluateReview(state).canApprove, false);
});

test('reflection needs written reasoning and explicit comparison with evidence', () => {
  const state = quizState();
  state.guide.questions = [{ id: 'reflection', kind: 'reflection', prompt: 'Explain a changed input and its observable result.' }];
  state.answers = { reflection: { response: 'yes', submitted: true, confirmed: true } };
  assert.equal(evaluateReview(state).canApprove, false);
  state.answers.reflection.response = 'The parser keeps the quoted comma inside the value.';
  state.answers.reflection.confirmed = false;
  assert.equal(evaluateReview(state).canApprove, false);
  state.answers.reflection.confirmed = true;
  assert.equal(evaluateReview(state).canApprove, true);
});

test('open inline comments bubble into verdict at the precise file and line', () => {
  const state = quizState();
  state.inlineComments = [{ id: 'note', path: 'a.py', side: 'head', line: 15, body: 'Explain the empty input case.', resolved: false }];
  const verdict = evaluateReview(state);
  assert.equal(verdict.canApprove, false);
  assert.ok(verdict.reasons.some(reason => reason.id === 'comment:note' && reason.label.includes('a.py:15')));
  state.inlineComments[0].resolved = true;
  assert.equal(evaluateReview(state).canApprove, true);
});

test('current failed or pending CI cannot be overridden by a manually recorded pass', () => {
  const state = quizState();
  state.ci = { reviewId: 'scope1', baseSha: 'base', headSha: 'head', status: 'failed', complete: true };
  assert.equal(evaluateReview(state).status, 'changes_requested');
  state.ci.status = 'pending';
  assert.equal(evaluateReview(state).status, 'needs_review');
  state.ci.headSha = 'different';
  assert.equal(evaluateReview(state).canApprove, true, 'Unrelated CI cannot decide this review');
});

test('recorded GitHub evidence requires fresh, complete results for the same scope', () => {
  const state = quizState();
  state.validation.source = 'github-actions';
  for (const ci of [null, { reviewId: 'scope1', baseSha: 'base', headSha: 'old', status: 'passed', complete: true },
    { reviewId: 'scope1', baseSha: 'base', headSha: 'head', status: 'passed', complete: false }]) {
    state.ci = ci;
    assert.equal(evaluateReview(state).canApprove, false);
  }
  state.ci = { reviewId: 'scope1', baseSha: 'base', headSha: 'head', status: 'passed', complete: true };
  assert.equal(evaluateReview(state).canApprove, true);
});

test('refreshing CI suspends approval backed by an earlier passing result', () => {
  const state = quizState();
  state.validation.source = 'github-actions';
  state.ci = { reviewId: 'scope1', baseSha: 'base', headSha: 'head', status: 'passed', complete: true };
  assert.equal(evaluateReview(state).canApprove, true);

  state.ciLoading = true;
  const refreshing = evaluateReview(state);
  assert.equal(refreshing.status, 'needs_review');
  assert.equal(refreshing.canApprove, false);
  assert.ok(refreshing.reasons.some(reason => reason.id === 'ci-loading' && reason.step === 'tests'));

  state.ciLoading = false;
  state.ci.status = 'failed';
  assert.equal(evaluateReview(state).status, 'changes_requested', 'The refreshed failure supersedes the recorded pass.');
  state.ci.status = 'passed';
  assert.equal(evaluateReview(state).canApprove, true, 'Approval becomes available after a successful refresh finishes.');
});

test('open file concerns block readiness until resolved', () => {
  const state = complete({ fileConcerns: { 'a.py': { text: 'What happens for an empty input?' } } });
  assert.equal(evaluateReview(state).status, 'needs_review');
  state.fileConcerns['a.py'].blocking = true;
  assert.equal(evaluateReview(state).status, 'changes_requested');
  state.fileConcerns['a.py'].resolved = true;
  assert.equal(evaluateReview(state).canApprove, true);
});

test('decisions from a different immutable scope cannot approve', () => {
  for (const field of ['reviewId', 'baseSha', 'headSha']) {
    const state = complete();
    state.guide[field] = 'another';
    assert.equal(evaluateReview(state).canApprove, false);
  }
  assert.equal(evaluateReview(complete({ guide: null })).canApprove, false);
});

test('omitted changed files cannot be waived by marking all visible files', () => {
  const state = complete({ limitations: { acknowledged: true, note: 'Viewed the small map.' } });
  state.review.stats.changedFiles = 3;
  state.review.truncated = { files: true };
  assert.equal(evaluateReview(state).canApprove, false);
});

test('unavailable source needs external inspection evidence', () => {
  for (const field of ['excluded', 'binary', 'diffTruncated']) {
    const state = complete();
    state.review.files[0][field] = true;
    assert.equal(evaluateReview(state).canApprove, false);
    state.limitations = { acknowledged: true, note: 'Inspected full a.py diff in GitHub at the pinned head.' };
    assert.equal(evaluateReview(state).canApprove, true);
  }
});

test('sampled graph evidence is disclosed without treating the diff as missing', () => {
  const state = complete();
  state.review.truncated = { evidence: true, nodes: true };
  const verdict = evaluateReview(state);
  assert.equal(verdict.canApprove, true);
  assert.equal(verdict.advisories.length, 1);
});

test('empty and unloaded comparisons cannot approve', () => {
  assert.equal(evaluateReview().canApprove, false);
  const state = complete();
  state.review.files = [];
  state.review.stats.changedFiles = 0;
  assert.equal(evaluateReview(state).status, 'no_changes');
});

test('unknown marks and malformed choices cannot count toward completion', () => {
  const state = complete({ reviewedFiles: new Set(['a.py', 'missing.py']), answers: { purpose: true, impact: 'approved' } });
  const verdict = evaluateReview(state);
  assert.equal(verdict.progress.reviewed, 1);
  assert.equal(verdict.progress.answered, 0);
  assert.equal(verdict.canApprove, false);
});

test('missing generated questions retain human comprehension gates', () => {
  const state = complete({ answers: {} });
  state.guide.questions = [];
  assert.equal(evaluateReview(state).progress.questions, DEFAULT_REVIEW_QUESTIONS.length);
  assert.equal(evaluateReview(state).canApprove, false);
});

test('invalid generated questions cannot remove the comprehension gates', () => {
  const state = complete({ answers: {} });
  state.guide.questions = [{}, { id: 'blank', prompt: '' }, null];
  const verdict = evaluateReview(state);
  assert.equal(verdict.progress.questions, DEFAULT_REVIEW_QUESTIONS.length);
  assert.equal(verdict.canApprove, false);
});

test('fallback questions can complete through the reflection flow shown to the reviewer', () => {
  for (const generated of [undefined, [], [{}, { id: 'blank', prompt: '' }, null]]) {
    const state = complete();
    state.guide.questions = generated;
    const questions = questionsForGuide(state.guide);
    assert.ok(questions.length > 0);
    assert.ok(questions.every(question => question.kind === 'reflection'));
    assert.equal(evaluateReview(state).canApprove, false, 'Legacy yes answers cannot silently complete the new reflections.');

    state.answers = Object.fromEntries(questions.map(question => [question.id, {
      response: 'The parser now preserves the comma inside a quoted value.',
      submitted: true, confirmed: false,
    }]));
    const unconfirmed = evaluateReview(state);
    assert.equal(unconfirmed.progress.answered, questions.length);
    assert.equal(unconfirmed.progress.understood, 0);
    assert.equal(unconfirmed.canApprove, false);

    for (const answer of Object.values(state.answers)) answer.confirmed = true;
    const confirmed = evaluateReview(state);
    assert.equal(confirmed.progress.understood, questions.length);
    assert.equal(confirmed.canApprove, true, 'Written, submitted, self-assessed fallback reflections satisfy the same verdict gates.');
  }
});

test('optional stage acknowledgements are explicit, not inferred from navigation', () => {
  const state = complete({ acknowledged: { brief: true, impact: false, tests: true } });
  assert.equal(evaluateReview(state).canApprove, false);
  state.acknowledged.impact = true;
  assert.equal(evaluateReview(state).canApprove, true);
});

test('agent findings need assessment; dismissals are specific to the evidence fingerprint', () => {
  const inputs = complete();
  const finding = {id: 'quota', fingerprint: 'revision-one', title: 'Quota bypass', consequence: 'Over-quota requests are accepted.'};
  inputs.guide.findings = [finding];
  assert.equal(evaluateReview(inputs).canApprove, false);
  assert.ok(evaluateReview(inputs).reasons.some(reason => reason.id === 'finding:quota'));
  inputs.findingDecisions = {'revision-one': {decision: 'dismissed', note: 'The caller rejects this state before invoking the function.'}};
  assert.equal(evaluateReview(inputs).canApprove, true);
  inputs.findingDecisions = {'revision-one': {decision: 'confirmed'}};
  assert.equal(evaluateReview(inputs).status, 'changes_requested');
  finding.fingerprint = 'revision-two';
  assert.equal(evaluateReview(inputs).status, 'needs_review');
});
