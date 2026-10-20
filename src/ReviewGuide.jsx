import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUpRight, BookOpen, Check, CheckCheck, ChevronDown, CircleHelp, Clipboard, FileCode2, GitBranch, ListChecks, Loader2, MessageSquare, Search, ShieldCheck, TestTube2, TriangleAlert } from 'lucide-react';
import { evaluateReview, questionAssessment, questionsForGuide } from './review-verdict.mjs';
import { ReviewUniverse } from './ReviewUniverse.jsx';
import { ReviewQuestion } from './ReviewQuestion.jsx';
import { ReviewCI } from './ReviewCI.jsx';
import './review-guide.css';
import './review-refinements.css';

const STEPS = [
  { id: 'brief', label: 'Understand the change', short: 'Change', icon: GitBranch },
  { id: 'files', label: 'Review the files', short: 'Files', icon: FileCode2 },
  { id: 'tests', label: 'Check the evidence', short: 'Tests', icon: TestTube2 },
  { id: 'questions', label: 'Make the call', short: 'Questions', icon: MessageSquare },
  { id: 'verdict', label: 'Your verdict', short: 'Verdict', icon: ShieldCheck },
];
const sha = value => (value || '').slice(0, 8);
const leaf = path => (path || '').split('/').pop();
const list = value => Array.isArray(value) ? value : [];
const text = value => typeof value === 'string' ? value : value?.text || value?.detail || '';
const titleContent = value => String(value || '').split(/([A-Za-z_][\w.]*\(\))/g).map((part, index) =>
  /^[A-Za-z_][\w.]*\(\)$/.test(part) ? <code className="ru-title-code" key={index}>{part}</code> : part);
const inlineCode = value => String(value || '').split(/(`[^`]+`)/g).map((part, index) =>
  part.startsWith('`') ? <code key={index}>{part.slice(1, -1)}</code> : part);
const blankState = scope => ({ scope, step: 'brief', visited: [], answers: {}, answerRationales: {}, fileConcerns: {}, inlineComments: [], validation: { status: 'not_checked', note: '' }, limitations: { acknowledged: false, note: '' }, decision: '', guideVersion: null });
function restoreState(scope) {
  try {
    const saved = JSON.parse(localStorage.getItem(scope));
    if (!saved || saved.scope !== scope) return blankState(scope);
    const record = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    return { ...blankState(scope), ...saved,
      step: STEPS.some(step => step.id === saved.step) ? saved.step : 'brief',
      visited: list(saved.visited),
      answers: record(saved.answers), answerRationales: record(saved.answerRationales), fileConcerns: record(saved.fileConcerns),
      inlineComments: list(saved.inlineComments).filter(item => item && typeof item.id === 'string' && typeof item.path === 'string' && typeof item.body === 'string' && ['base', 'head'].includes(item.side) && Number.isInteger(item.line) && item.line > 0),
      validation: { ...record(saved.validation), status: ['passed', 'failed', 'not_applicable'].includes(saved.validation?.status) ? saved.validation.status : 'not_checked', note: typeof saved.validation?.note === 'string' ? saved.validation.note : '' },
      limitations: { acknowledged: saved.limitations?.acknowledged === true, note: typeof saved.limitations?.note === 'string' ? saved.limitations.note : '' },
    };
  } catch { return blankState(scope); }
}
function githubSource(review, evidence) {
  try {
    const url = new URL(review.url);
    if (url.protocol !== 'https:' || url.hostname !== 'github.com' || !url.pathname.includes('/pull/')) return null;
    const commit = evidence.side === 'base' ? review.baseSha : review.headSha;
    return `${url.origin}${url.pathname.split('/pull/')[0]}/blob/${encodeURIComponent(commit)}/${evidence.path.split('/').map(encodeURIComponent).join('/')}#L${evidence.line || 1}`;
  } catch { return null; }
}

function Evidence({ items, review, onOpen, compact = false }) {
  const locations = Array.isArray(items) ? items : items?.path ? [items] : [];
  if (!locations.length) return null;
  return <div className={`rg-evidence ${compact ? 'compact' : ''}`}>
    {locations.slice(0, compact ? 3 : 6).map((item, index) => {
      const changed = review.files?.find(file => file.path === item.path || file.oldPath === item.path);
      const label = `${compact ? leaf(item.path) : item.path}:${item.line || 1}`;
      return changed ? <button key={index} onClick={() => onOpen(changed.path, item)} title={`${item.side || 'head'} · ${item.path}:${item.line || 1}`}><FileCode2 size={12}/><code>{label}</code><span>{item.side || 'head'}</span><ArrowUpRight size={12}/></button> : githubSource(review, item) ? <a key={index} href={githubSource(review, item)} target="_blank" rel="noreferrer" title={`${item.side || 'head'} snapshot`}><code>{label}</code><span>{item.side || 'head'}</span><ArrowUpRight size={12}/></a> : <span key={index} className="rg-evidence-location"><code>{label}</code><span>{item.side || 'head'}</span></span>;
    })}
  </div>;
}

function Watchpoints({ items, review, onOpen, title = 'What to watch for' }) {
  if (!list(items).length) return null;
  return <section className="rg-watchpoints" aria-label={title}>
    <h3><TriangleAlert size={15}/>{title}</h3>
    {items.map((point, index) => <div className="rg-watchpoint" key={point.id || index}>
      {point.title && <strong>{point.title}</strong>}
      <p>{text(point)}</p>
      <Evidence items={point.evidence} review={review} onOpen={onOpen} compact/>
    </div>)}
  </section>;
}

export function ReviewGuide({ review, projectId, selectedPath, onSelectFile, reviewed = new Set(), onToggleReviewed, children, onSelectContext, onSelectConnection, explorationRequest = 0 }) {
  const scope = `atlas-review-guide:v1:${projectId}:${review.id}:${review.baseSha}:${review.headSha}`;
  const [saved, setSaved] = useState(() => restoreState(scope));
  const [result, setResult] = useState(null), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  const [fileQuery, setFileQuery] = useState(''), [copyStatus, setCopyStatus] = useState(''), [copyFallback, setCopyFallback] = useState('');
  const [storageError, setStorageError] = useState(false);
  const [ciResult, setCIResult] = useState(null), [ciLoading, setCILoading] = useState(false), [ciError, setCIError] = useState(''), [ciRetry, setCIRetry] = useState(0);
  const [revision, setRevision] = useState('changes'), [questionIndex, setQuestionIndex] = useState(0), [factIndex, setFactIndex] = useState(0), [filePickerOpen, setFilePickerOpen] = useState(false);
  const contentRef = useRef(null), workspaceRef = useRef(null), copyTimer = useRef(null), lastExploration = useRef(explorationRequest);
  const state = saved.scope === scope ? saved : blankState(scope);
  const guide = result?.scope === scope ? result.guide : null;
  const step = state.step;
  const quietStage = ['tests', 'questions', 'verdict'].includes(step);
  const needsCI = step === 'tests' || step === 'verdict';
  const ci = ciResult?.scope === scope ? ciResult.data : null;
  const stepIndex = STEPS.findIndex(item => item.id === step);
  const questions = questionsForGuide(guide);
  const files = guide ? list(guide.files) : [];
  const reviewedSet = useMemo(() => reviewed instanceof Set ? reviewed : new Set(reviewed), [reviewed]);
  const verdict = useMemo(() => evaluateReview({ review, guide, reviewedFiles: reviewedSet, answers: state.answers, fileConcerns: state.fileConcerns, inlineComments: state.inlineComments, ci, ciLoading, validation: state.validation, limitations: state.limitations }), [review, guide, reviewedSet, state.answers, state.fileConcerns, state.inlineComments, ci, ciLoading, state.validation, state.limitations]);
  const selected = files.find(file => file.path === selectedPath);
  const inlinePointEvidence = list(selected?.hunks).flatMap(hunk => list(hunk.watchpoints)).flatMap(point => list(point.evidence));
  const otherFilePoints = list(selected?.watchpoints).filter(point => !list(point.evidence).some(ref =>
    inlinePointEvidence.some(inline => inline.path === ref.path && inline.side === ref.side && inline.line <= (ref.endLine || ref.line) && ref.line <= (inline.endLine || inline.line))));
  const currentWatchpoints = list(guide?.watchpoints).filter(point => !(ci && ['passed', 'failed', 'pending'].includes(ci.status) && point.id === 'test-execution'));
  const selectedIndex = files.findIndex(file => file.path === selectedPath);
  const limitations = [...new Set([...list(guide?.limitations), ...list(guide?.coverage?.limitations), ...list(review.warnings), ...Object.entries(review.truncated || {}).filter(([, value]) => value).map(([name]) => `${({ evidence: 'Connection source samples', nodes: 'Map files', edges: 'Map connections', symbols: 'Changed definitions', diffs: 'Diff previews', files: 'Changed files' })[name] || name} are capped for this snapshot.`)].map(text).filter(Boolean))];
  const needsLimitationAcknowledgment = review.truncated?.diffs || list(review.files).some(file => file.excluded || file.binary || file.diffTruncated);
  const concern = state.fileConcerns[selectedPath];
  const fileComments = state.inlineComments.filter(comment => comment.path === selectedPath);
  const openComments = state.inlineComments.filter(comment => !comment.resolved && comment.body.trim());
  const filteredFiles = files.filter(file => file.path.toLowerCase().includes(fileQuery.toLowerCase()));

  useEffect(() => {
    setSaved(restoreState(scope)); setResult(null); setError(''); setFileQuery(''); setCopyFallback(''); setCopyStatus('');
  }, [scope]);
  useEffect(() => {
    if (saved.scope !== scope) return;
    try { localStorage.setItem(scope, JSON.stringify(saved)); setStorageError(false); } catch { setStorageError(true); }
  }, [saved, scope]);
  useEffect(() => {
    const controller = new AbortController();
    setError(''); setResult(null);
    const url = new URL('/api/review/guide', location.origin);
    url.searchParams.set('project', projectId); url.searchParams.set('review', review.id);
    fetch(url, { signal: controller.signal }).then(async response => {
      let data;
      try { data = await response.json(); } catch { throw new Error('The review guide could not be read. Check the local Atlas server and retry.'); }
      if (!response.ok) throw new Error(data.error || `The guide request returned ${response.status}.`);
      if (data.reviewId !== review.id || data.baseSha !== review.baseSha || data.headSha !== review.headSha) throw new Error('The guide belongs to a different revision. Reload this review before continuing.');
      if (!data.brief || !Array.isArray(data.files)) throw new Error('The review guide is incomplete. Retry to rebuild it.');
      if (controller.signal.aborted) return;
      setResult({ scope, guide: data });
      const previousSelection = restoreState(scope).selectedPath;
      const initialFile = data.files.find(file => file.path === previousSelection) || data.files[0];
      if (initialFile) onSelectFile(initialFile.path);
      setSaved(previous => {
        const current = previous.scope === scope ? previous : restoreState(scope);
        // Version 3 adds coverage evidence; questions and source revisions are unchanged.
        const compatibleCoverageUpdate = current.guideVersion === 2 && data.version === 3;
        return current.guideVersion != null && current.guideVersion !== data.version && !compatibleCoverageUpdate
          ? { ...blankState(scope), fileConcerns: current.fileConcerns, inlineComments: current.inlineComments, validation: current.validation, limitations: current.limitations, guideVersion: data.version }
          : { ...current, guideVersion: data.version };
      });
    }).catch(cause => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [projectId, review.id, review.baseSha, review.headSha, scope, retry]);
  useEffect(() => {
    if (!needsCI) return;
    const controller = new AbortController();
    setCILoading(true); setCIError('');
    const url = new URL('/api/review/ci', location.origin);
    url.searchParams.set('project', projectId); url.searchParams.set('review', review.id);
    fetch(url, { signal: controller.signal }).then(async response => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'GitHub checks could not be loaded.');
      if (data.reviewId !== review.id || data.baseSha !== review.baseSha || data.headSha !== review.headSha) throw new Error('The check results belong to another revision.');
      if (!controller.signal.aborted) setCIResult({ scope, data });
    }).catch(cause => { if (!controller.signal.aborted) { setCIError(cause.message); setCIResult(null); } })
      .finally(() => { if (!controller.signal.aborted) setCILoading(false); });
    return () => controller.abort();
  }, [needsCI, projectId, review.id, review.baseSha, review.headSha, scope, ciRetry]);
  useEffect(() => () => clearTimeout(copyTimer.current), []);
  useEffect(() => {
    const onKey = event => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault(); setFilePickerOpen(value => !value);
      } else if (event.key === 'Escape') setFilePickerOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => {
    if (!guide || !selectedPath) return;
    setSaved(previous => previous.scope !== scope || previous.selectedPath === selectedPath ? previous : { ...previous, selectedPath });
  }, [guide, selectedPath, scope]);
  useEffect(() => {
    contentRef.current?.scrollTo({ top: 0, behavior: 'instant' });
    workspaceRef.current?.scrollTo({ top: 0, behavior: 'instant' });
  }, [selectedPath]);
  useEffect(() => {
    if (lastExploration.current === explorationRequest) return;
    lastExploration.current = explorationRequest;
    if (explorationRequest) navigate('files');
  }, [explorationRequest]);
  function update(patch) {
    setSaved(previous => ({ ...(previous.scope === scope ? previous : blankState(scope)), ...patch }));
  }
  function navigate(next) {
    if (next === 'impact') next = 'brief';
    update({ step: next, visited: [...new Set([...state.visited, step])] });
    setCopyFallback(''); setCopyStatus('');
    requestAnimationFrame(() => {
      contentRef.current?.scrollTo({ top: 0, behavior: 'instant' });
      workspaceRef.current?.scrollTo({ top: 0, behavior: 'instant' });
      contentRef.current?.focus({ preventScroll: true });
    });
  }
  function openEvidence(path, evidence) {
    if (step !== 'files') navigate('files');
    onSelectFile(path, evidence);
  }
  function addComment(comment) {
    if (!review.files.some(file => file.path === comment.path) || !['base', 'head'].includes(comment.side) || !Number.isInteger(comment.line) || comment.line < 1 || !comment.body?.trim()) return;
    setSaved(previous => {
      const current = previous.scope === scope ? previous : blankState(scope);
      return { ...current, inlineComments: [...current.inlineComments, { ...comment, id: crypto.randomUUID(), body: comment.body.trim().slice(0, 6000), createdAt: new Date().toISOString(), resolved: false }] };
    });
  }
  function updateComment(id, patch) {
    setSaved(previous => ({ ...previous, inlineComments: previous.inlineComments.map(comment => comment.id === id ? { ...comment,
      ...(typeof patch.body === 'string' ? { body: patch.body.slice(0, 6000) } : {}), ...(typeof patch.resolved === 'boolean' ? { resolved: patch.resolved } : {}) } : comment) }));
  }
  function deleteComment(id) { setSaved(previous => ({ ...previous, inlineComments: previous.inlineComments.filter(comment => comment.id !== id) })); }
  function recordCI() {
    if (ciLoading || ci?.status !== 'passed' || ci.complete === false) return;
    const urls = list(ci.runs).length ? ci.runs : ci.checks;
    const skipped = list(urls).filter(run => run.conclusion === 'skipped').length;
    update({ validation: { status: 'passed', source: 'github-actions', note: `GitHub aggregate: passed; ${skipped} skipped. Head ${review.headSha}. Checked ${ci.checkedAt}.\n${list(urls).map(run => `${run.name}: ${run.conclusion || run.status} — ${run.url || 'No run link'}`).join('\n')}` } });
  }
  function questionLabel(question) { return question.prompt || question.text || question.label; }
  function openReason(reason) {
    if (reason.id.startsWith('comment:')) {
      const comment = state.inlineComments.find(item => item.id === reason.id.slice(8));
      if (comment) openEvidence(comment.path, { path: comment.sourcePath || comment.path, side: comment.side, line: comment.line, hunkIndex: comment.hunkIndex });
      return;
    }
    if (reason.id.startsWith('concern:')) {
      openEvidence(reason.id.slice(8));
      return;
    }
    if (reason.id.startsWith('question:')) {
      const index = questions.findIndex(item => item.id === reason.id.slice(9));
      if (index >= 0) setQuestionIndex(index);
    }
    navigate(reason.step || 'questions');
  }
  function packet(kind) {
    const lines = [
      kind === 'agent' ? 'Help me review this change. Explain the intent and cite specific lines in your answers.' : `Atlas review: ${verdict.label}`,
      `PR: ${review.title || review.id}`, review.url ? `URL: ${review.url}` : null,
      `Base: ${review.baseSha}`, `Head: ${review.headSha}`, '',
      guide?.brief?.summary, '', `Reviewed files: ${verdict.progress.reviewed}/${verdict.progress.total}`,
      ...files.map(file => `- ${file.path} (${file.status}, ${file.role})${reviewedSet.has(file.path) ? ' [reviewed]' : ''}`),
      ...(kind === 'agent' && selected ? ['', `Current file: ${selected.path}`, selected.summary,
        ...list(selected.evidence).map(item => `Evidence: ${item.side}:${item.path}:${item.line}${githubSource(review, item) ? ` ${githubSource(review, item)}` : ''}`)] : []),
      ...questions.map(question => { const answer = state.answers[question.id]; return `${questionLabel(question)}\nAnswer: ${answer?.response || question.choices?.find(choice => choice.id === answer?.choice)?.text || 'Unanswered'}\nAssessment: ${questionAssessment(question, answer).complete ? question.kind === 'reflection' ? 'Self-assessed against evidence' : 'Matches source evidence' : 'Needs review'}`; }),
      '', 'Inline comments:', ...state.inlineComments.map(comment => `${comment.side}:${comment.sourcePath || comment.path}:${comment.line} — ${comment.body} [${comment.resolved ? 'resolved' : 'open'}]`),
      '', 'File concerns:', ...Object.entries(state.fileConcerns).filter(([, value]) => value.text?.trim()).map(([path, value]) => `${path}: ${value.text}${value.resolved ? ' [resolved]' : ' [open]'}`),
      '', `Validation: ${state.validation.status}`, state.validation.note,
      state.limitations.note ? `Additional inspection: ${state.limitations.note}` : null,
      '', `Local assessment: ${verdict.summary}`, ...list(verdict.reasons).map(reason => `- ${reason.label}: ${reason.detail}`),
      '', 'This is a local review record. No review has been submitted to GitHub.',
    ];
    return lines.filter(value => value != null).join('\n');
  }
  async function copy(kind) {
    const body = packet(kind);
    try { await navigator.clipboard.writeText(body); setCopyStatus(kind === 'agent' ? 'Context copied. Paste it into your agent.' : 'Review copied.'); setCopyFallback(''); }
    catch { setCopyFallback(body); setCopyStatus('Select and copy the context below.'); }
    clearTimeout(copyTimer.current); copyTimer.current = setTimeout(() => setCopyStatus(''), 5500);
  }
  function isComplete(id) {
    if (id === 'files') return files.length > 0 && files.every(file => reviewedSet.has(file.path));
    if (id === 'questions') return questions.every(question => questionAssessment(question, state.answers[question.id]).complete);
    if (id === 'tests') return state.validation.status !== 'not_checked' && !!state.validation.note.trim();
    if (id === 'verdict') return verdict.status === 'ready';
    return state.visited.includes(id);
  }

  const changes = list(guide?.brief?.behaviorChanges);
  const testFiles = files.filter(file => file.role === 'tests');
  const primaryFiles = files.filter(file => file.role !== 'tests');
  const hasCallMap = [...list(guide?.impact?.before), ...list(guide?.impact?.after)].some(node => ['function', 'class', 'method', 'call-expression'].includes(node.kind));
  const changeFile = primaryFiles[Math.min(factIndex, Math.max(0, primaryFiles.length - 1))];
  const revisionText = changeFile ? text(revision === 'base' ? changeFile.before : revision === 'head' ? changeFile.after : changeFile.summary).replace(/^Author explanation:\s*/, '') : guide?.brief?.summary;
  const revisionEvidence = list(changeFile?.evidence).filter(item => revision === 'changes' || item.side === revision);
  const questionAnswer = state.answers[questions[Math.min(questionIndex, Math.max(0, questions.length - 1))]?.id] || {};
  const question = questions[Math.min(questionIndex, Math.max(0, questions.length - 1))];
  const chooseFile = (path, evidence) => { setFilePickerOpen(false); openEvidence(path, evidence); };
  const nextUnreviewed = files.find(file => !reviewedSet.has(file.path));
  const continueLabel = ({ brief: 'Inspect the code', files: 'Check the tests', tests: 'Review questions', questions: 'See the verdict', verdict: verdict.canApprove ? 'Revisit code' : 'Keep reviewing' })[step];
  const continueReview = () => navigate(step === 'verdict' ? verdict.reasons?.[0]?.step || 'files' : STEPS[stepIndex + 1].id);
  const fileChooser = <div className="ru-file-picker" role="region" aria-label="Changed file navigator">
    <label><Search size={15}/><input autoFocus aria-label="Find a changed file" placeholder="Find a changed file…" value={fileQuery} onChange={event => setFileQuery(event.target.value)}/><button aria-label="Close file navigator" onClick={() => setFilePickerOpen(false)}>×</button></label>
    <div>{filteredFiles.map((file, index) => <button key={file.path} aria-current={file.path === selectedPath ? 'true' : undefined} onClick={() => chooseFile(file.path)}><span className={reviewedSet.has(file.path) ? 'reviewed' : ''}>{reviewedSet.has(file.path) ? <Check size={12}/> : index + 1}</span><code>{file.path}</code><small>{file.role}</small></button>)}{!filteredFiles.length && <p>No matching files.</p>}</div>
  </div>;
  return <div className="review-guide ru-workspace" data-step={step} data-minimized={quietStage || undefined} ref={workspaceRef}>
    <div className="ru-universe-shell" inert={quietStage ? '' : undefined} aria-hidden={quietStage ? true : undefined}>
      <ReviewUniverse review={review} guide={guide} selectedPath={selectedPath} reviewed={reviewedSet} step={step === 'brief' ? 'impact' : step} revision={step === 'brief' ? revision : 'changes'} compact={quietStage}
        onSelectFile={chooseFile}
        onSelectContext={node => { navigate('files'); onSelectContext?.(node); }}
        onSelectEdge={edge => { navigate('files'); onSelectConnection?.(edge); }}/>
    </div>
    {quietStage && <button className="ru-minimap-return" onClick={() => navigate('brief')} aria-label="Expand the change universe"><span>Back to the change<ArrowUpRight size={12}/></span></button>}
    <div className="ru-toolbar">
      <div className="ru-scope-label"><span className="ru-overline">CHANGE CONSTELLATION</span><button className="ru-progress-fraction" onClick={() => setFilePickerOpen(!filePickerOpen)} aria-expanded={filePickerOpen}>{verdict.progress.reviewed}<span> / {verdict.progress.total} reviewed</span><ChevronDown size={12}/></button></div>
      {step === 'brief' && <div className="ru-revisions" role="group" aria-label="Map revision">{[{ id: 'base', label: 'Before' }, { id: 'changes', label: 'Changes' }, { id: 'head', label: 'After' }].map(item => <button key={item.id} aria-pressed={revision === item.id} className={revision === item.id ? 'active' : ''} onClick={() => setRevision(item.id)}>{item.label}</button>)}</div>}
    </div>
    {filePickerOpen && fileChooser}
    {!guide ? <div className="ru-loading" role={error ? 'alert' : 'status'}>{error ? <><CircleHelp size={24}/><h2>The guide is unavailable.</h2><p>{error}</p><button className="ru-action" onClick={() => setRetry(value => value + 1)}>Retry<ArrowRight size={15}/></button></> : <><Loader2 size={24} className="pr-spin"/><h2>Finding the change.</h2><p>Reading the pinned source and its connections.</p></>}</div> : <>
      {['brief', 'questions', 'verdict'].includes(step) && <section ref={contentRef} tabIndex={-1} className={`ru-narrative ru-narrative-${step}`} aria-label={STEPS[stepIndex].label}>
        {step === 'brief' && <>
          <span className="ru-overline">01 / UNDERSTAND THE CHANGE</span>
          <h2>{titleContent(guide.brief.title || review.title)}</h2>
          <p className="ru-meta"><span>{primaryFiles.length ? `${primaryFiles.length} core files` : testFiles.length ? 'Test-only change' : 'No changed files'}</span>{testFiles.length > 0 && <button className="ru-link" onClick={() => navigate('tests')}>{testFiles.length} test files<ArrowUpRight size={12}/></button>}</p>
          <div className="ru-change-story" aria-live="polite">
            <span className="ru-overline">{revision === 'base' ? 'BEFORE' : revision === 'head' ? 'AFTER' : 'WHAT CHANGES'}{changeFile && <code>{leaf(changeFile.path)}</code>}</span>
            <p>{revisionText}</p><Evidence items={revisionEvidence} review={review} onOpen={chooseFile} compact/>
            {primaryFiles.length > 1 && <div className="ru-fact-nav"><button aria-label="Previous changed area" disabled={factIndex <= 0} onClick={() => { setFactIndex(index => index - 1); onSelectFile(primaryFiles[factIndex - 1].path); }}><ArrowLeft size={16}/></button><span>{factIndex + 1} / {primaryFiles.length}</span><button aria-label="Next changed area" disabled={factIndex >= primaryFiles.length - 1} onClick={() => { setFactIndex(index => index + 1); onSelectFile(primaryFiles[factIndex + 1].path); }}><ArrowRight size={16}/></button></div>}
          </div>
          <button className="ru-action" onClick={() => navigate('files')}>Inspect the code<ArrowRight size={17}/></button>
        </>}
        {step === 'questions' && question && <>
          <span className="ru-overline">04 / UNDERSTAND, THEN DECIDE</span>
          <div className="ru-question-dots" aria-label="Review questions">{questions.map((item, index) => <button key={item.id} aria-label={`Question ${index + 1}: ${questionLabel(item)}`} aria-current={questionIndex === index ? 'step' : undefined} className={`${questionIndex === index ? 'active' : ''} ${questionAssessment(item, state.answers[item.id]).complete ? 'yes' : ''}`} onClick={() => setQuestionIndex(index)}>{questionAssessment(item, state.answers[item.id]).complete ? <Check size={12}/> : index + 1}</button>)}</div>
          <ReviewQuestion key={question.id} question={question} answer={questionAnswer} onChange={answer => update({ answers: { ...state.answers, [question.id]: answer } })} evidence={<Evidence items={question.evidence} review={review} onOpen={chooseFile}/>} hasNext={questionIndex < questions.length - 1} onNext={() => setQuestionIndex(index => index + 1)}/>
          <details className="ru-details ru-agent"><summary><MessageSquare size={14}/>Ask the author's agent<ChevronDown size={12}/></summary><p>Connector not connected. Copy the exact commits, file context, and open questions to your agent.</p><button className="ru-link" onClick={() => copy('agent')}><Clipboard size={14}/>Copy agent context</button><small>Nothing is sent automatically.</small></details>
        </>}
        {step === 'verdict' && <>
          <span className="ru-overline">05 / YOUR VERDICT</span><div className={`ru-verdict-beacon ${verdict.status}`}><span/><ShieldCheck size={23}/></div><h2>{verdict.label}</h2><p>{verdict.summary}</p>
          <div className="ru-verdict-progress"><span>{verdict.progress.reviewed}/{verdict.progress.total} files</span><span>{verdict.progress.understood}/{verdict.progress.questions} understood</span><span>{({ not_checked: 'Tests pending', passed: 'Tests recorded passing', failed: 'Tests recorded failing', not_applicable: 'Tests not applicable' })[state.validation.status]}</span></div>
          <div className="ru-reasons">{verdict.reasons.map(reason => <button key={reason.id} onClick={() => openReason(reason)}><span/>{reason.label}<ArrowUpRight size={13}/></button>)}</div>
          {!!openComments.length && <p className="ru-fine-print">{openComments.length} open inline comment{openComments.length === 1 ? '' : 's'} to resolve.</p>}
          <button className="ru-link" onClick={() => copy('review')}><Clipboard size={14}/>Copy review record</button>
          {Object.entries(state.fileConcerns).some(([, item]) => item.text?.trim()) && <details className="ru-details"><summary>Reviewer concerns<ChevronDown size={12}/></summary>{Object.entries(state.fileConcerns).filter(([, item]) => item.text?.trim()).map(([path, item]) => <div key={path}><button className="ru-link" onClick={() => chooseFile(path)}>{leaf(path)}<ArrowUpRight size={12}/></button><p>{item.text}</p><small>{item.resolved ? 'Resolved' : 'Open concern'}</small></div>)}</details>}
          {!!list(verdict.advisories).length && <p className="ru-fine-print">{verdict.advisories.join(' ')}</p>}
          <p className="ru-fine-print">Local assessment for {sha(review.headSha)}. Nothing is submitted to GitHub.</p>
        </>}
      </section>}
      {['files', 'tests'].includes(step) && <>
        {step === 'files' && <div className="ru-file-orbit"><span className="ru-overline">{step === 'files' ? '02 / INSPECT THE CHANGE' : '04 / TEST EVIDENCE'}</span><h2>{step === 'files' ? 'Code, in context.' : 'What gives us confidence?'}</h2><p>{step === 'files' ? `${verdict.progress.reviewed} of ${files.length} files reviewed. Select a world to inspect it.` : 'See what the tests check, where the evidence stops, and what still needs verification.'}</p><button className="ru-link" onClick={() => setFilePickerOpen(!filePickerOpen)}><Search size={14}/>Find a changed file</button>{step === 'files' && nextUnreviewed && nextUnreviewed.path !== selectedPath && <button className="ru-link" onClick={() => chooseFile(nextUnreviewed.path)}>Next unreviewed<ArrowRight size={14}/></button>}</div>}
        <section className={`ru-inspector ru-inspector-${step}`} aria-label={step === 'files' ? 'File inspection' : 'Test evidence'}>
          <header className="ru-inspector-header"><div><span className="ru-overline">{step === 'files' ? `FILE ${selectedIndex + 1} / ${files.length}` : 'VERIFY THE HEAD COMMIT'}</span><h3>{step === 'files' ? leaf(selectedPath) || 'Select a file' : 'What do the tests establish?'}</h3></div><div className="ru-inspector-controls">{step === 'files' && <><button aria-label="Previous file" disabled={selectedIndex <= 0} onClick={() => onSelectFile(files[selectedIndex - 1].path)}><ArrowLeft size={16}/></button><button aria-label="Next file" disabled={selectedIndex >= files.length - 1} onClick={() => onSelectFile(files[selectedIndex + 1].path)}><ArrowRight size={16}/></button></>}<button aria-label="Return to constellation" onClick={() => navigate('brief')}>×</button></div></header>
          <div className="ru-inspector-scroll" ref={contentRef} tabIndex={-1}>
            {step === 'files' && selected && <>
              <div className="ru-file-intro"><code className="ru-file-path">{selected.path}</code><p className="ru-file-summary">{selected.summary}</p><Evidence items={selected.evidence} review={review} onOpen={chooseFile} compact/>
                {!!list(selected.hunks).length && <div className="ru-hunks">{selected.hunks.slice(0, 8).map((hunk, index) => <button key={index} onClick={() => onSelectFile(selected.path, { ...hunk.evidence?.[0], hunkIndex: hunk.index ?? index })}><ArrowDown size={11}/>{hunk.heading || `Change ${index + 1}`}</button>)}</div>}
              </div>
              <div className="ru-diff-slot">{typeof children === 'function' ? children({ fileGuide: selected, comments: fileComments, onAddComment: addComment, onUpdateComment: updateComment, onDeleteComment: deleteComment }) : children}</div>
              {!!otherFilePoints.length && <details className="ru-details"><summary>Other file considerations<ChevronDown size={12}/></summary><Watchpoints items={otherFilePoints} review={review} onOpen={chooseFile}/></details>}
              <details className="ru-details"><summary>Before, after & related tests<ChevronDown size={12}/></summary><span className="ru-overline">BEFORE</span><p>{text(selected.before)}</p><span className="ru-overline">AFTER</span><p>{text(selected.after)}</p>{list(selected.relatedTests).slice(0,12).map((test,index)=><div key={index}><Evidence items={test.evidence} review={review} onOpen={chooseFile}/></div>)}{!!list(selected.analysis?.limitations).length && <p className="ru-fine-print">{selected.analysis.limitations.map(text).join(' ')}</p>}</details>
              {!!concern?.text?.trim() && <details className="ru-details"><summary>Earlier file note</summary><p>{concern.text}</p><label className="ru-check"><input type="checkbox" checked={concern.resolved === true} onChange={event => update({ fileConcerns: { ...state.fileConcerns, [selectedPath]: { ...concern, resolved: event.target.checked } } })}/>This earlier concern is resolved.</label></details>}
            </>}
            {step === 'files' && !selected && <p>Select a changed file in the constellation.</p>}
            {step === 'tests' && <>
              <ReviewCI data={ci} loading={ciLoading} error={ciError} onRefresh={() => setCIRetry(value => value + 1)} onRecord={recordCI} recorded={state.validation.source === 'github-actions' && state.validation.status === 'passed'}/>
              <div className="ru-test-summary"><span className="ru-overline">COVERAGE FROM SOURCE</span><p>{list(guide.coverage?.testFiles).length} changed test files · {guide.coverage?.testsChanged || 0} test changes. The scenarios below describe written expectations; measured coverage has not been imported.</p></div>
              <section className="ru-coverage-assessment" aria-label="Test sufficiency assessment"><span className="ru-overline">IS THE EVIDENCE SUFFICIENT?</span><h4>{guide.coverage?.assessment?.summary || 'Sufficiency needs your judgment.'}</h4><p>{ci && ['passed', 'failed', 'pending'].includes(ci.status) ? 'Compare these scenarios with the CI results above. Confirm that the important success and failure cases are exercised; measured branch coverage has not been imported.' : guide.coverage?.assessment?.detail || 'Changed test code shows what is being checked. Confirm the important success and failure cases, and verify results for this commit.'}</p></section>
              {!!list(guide.coverage?.coveredScenarios).length ? <section className="ru-covered-scenarios" aria-label="Observed test coverage"><h4>What the tests check</h4>{guide.coverage.coveredScenarios.map((scenario,index)=><details className="ru-details ru-test-scenario" key={scenario.id || index}><summary><span className={`ru-change ${scenario.change}`}>{scenario.change}</span><span>{scenario.title}</span><ChevronDown size={12}/></summary><p>{scenario.detail}</p><Evidence items={scenario.evidence} review={review} onOpen={chooseFile} compact/>{list(scenario.examples).map((example,exampleIndex)=><div key={exampleIndex}><small>{example.change === 'added' ? 'Changed example' : 'Existing example'}</small><div className="ru-test-example"><code>{example.input}</code><ArrowRight size={12}/><code>{example.expected}</code></div><Evidence items={example.evidence} review={review} onOpen={chooseFile} compact/></div>)}{list(scenario.checks).map((check,checkIndex)=><div className="ru-assertion" key={checkIndex}><p>{check.text}</p><Evidence items={check.evidence} review={review} onOpen={chooseFile} compact/></div>)}{!!list(scenario.limits).length && <p className="ru-fine-print">{scenario.limits.join(' ')}</p>}</details>)}</section> : <div className="ru-test-list">{list(guide.coverage?.changedTestNames).map((test,index)=><div key={index}><span className={`ru-change ${test.change}`}>{test.change}</span><code>{test.name}</code><Evidence items={test.evidence} review={review} onOpen={chooseFile} compact/></div>)}{!list(guide.coverage?.changedTestNames).length && <p>No changed test definitions were identified. Inspect the file diffs and relevant existing tests.</p>}</div>}
              {(!!list(guide.coverage?.gaps).length || !!list(guide.coverage?.reviewerChecks).length) && <section className="ru-coverage-checks" aria-label="Coverage gaps and reviewer checks"><h4>Where to look closer</h4>{list(guide.coverage?.gaps).map((gap,index)=><p key={`gap:${index}`}>{text(gap)}</p>)}{list(guide.coverage?.reviewerChecks).map((check,index)=><div key={check.id || index}><strong>{check.title}</strong><p>{inlineCode(check.detail)}</p><Evidence items={check.evidence} review={review} onOpen={chooseFile} compact/></div>)}</section>}
              {!!list(guide.coverage?.observedAssertions).length && <details className="ru-details"><summary>{guide.coverage.observedAssertions.length} observed assertions<ChevronDown size={12}/></summary>{guide.coverage.observedAssertions.map((assertion,index)=><div className="ru-assertion" key={index}><small>{assertion.change}</small><code>{assertion.text}</code><Evidence items={assertion.evidence} review={review} onOpen={chooseFile} compact/></div>)}</details>}
              <label className="ru-form">Verification status<select value={state.validation.status} onChange={event => update({ validation: { ...state.validation, source: 'manual', status: event.target.value } })}><option value="not_checked">Not checked yet</option><option value="passed">I verified passing checks</option><option value="failed">I verified failing checks</option><option value="not_applicable">Tests do not apply to this change</option></select></label>
              <label className="ru-form">Evidence or reason<textarea rows={3} maxLength={6000} placeholder="CI run URL, command and result, or why tests do not apply." value={state.validation.note} onChange={event => update({ validation: { ...state.validation, source: 'manual', note: event.target.value } })}/></label>
              <p className="ru-fine-print">Your record for {sha(review.headSha)}. GitHub results are shown above; manual evidence is recorded by you.</p>
              <details className="ru-details"><summary>Whole PR checks<ChevronDown size={12}/></summary><Watchpoints items={currentWatchpoints} review={review} onOpen={chooseFile} title="Review beyond the tests"/></details>
              <details className="ru-details"><summary>Analysis boundaries<ChevronDown size={12}/></summary>{limitations.map((limitation,index)=><p className="ru-fine-print" key={index}>{limitation}</p>)}</details>
              {needsLimitationAcknowledgment && <div><label className="ru-check"><input type="checkbox" checked={state.limitations.acknowledged} onChange={event => update({ limitations: { ...state.limitations, acknowledged: event.target.checked } })}/>I inspected unavailable or truncated diffs outside Atlas.</label><label className="ru-form">Additional inspection<textarea rows={2} maxLength={6000} value={state.limitations.note} onChange={event => update({ limitations: { ...state.limitations, note: event.target.value } })} placeholder="Where you inspected the missing changes and what you concluded."/></label></div>}
            </>}
          </div>
          <div className="ru-inspector-actions">{step === 'files' && selected ? <><span>{fileComments.some(comment => !comment.resolved) ? `${fileComments.filter(comment => !comment.resolved).length} open comments` : state.fileConcerns[selectedPath]?.text?.trim() && !state.fileConcerns[selectedPath]?.resolved ? 'Open concern' : `${selected.role} · ${selected.status}`}</span><button className={`ru-action ${reviewedSet.has(selectedPath) ? 'reviewed' : ''}`} aria-pressed={reviewedSet.has(selectedPath)} onClick={() => onToggleReviewed(selectedPath)}><Check size={14}/>{reviewedSet.has(selectedPath) ? 'Reviewed' : 'Mark reviewed'}</button></> : <><span>{state.validation.status === 'not_checked' ? 'Verification pending' : 'Verification recorded locally'}</span><button className="ru-link" onClick={()=>navigate('questions')}>Review questions<ArrowRight size={14}/></button></>}</div>
        </section>
      </>}
    </>}
    {copyStatus && <div role="status" className="ru-copy-status">{copyStatus}</div>}
    {copyFallback && <div className="ru-copy-fallback"><button onClick={()=>setCopyFallback('')} aria-label="Close copy fallback">×</button><textarea aria-label="Review context to copy" value={copyFallback} readOnly rows={10} onFocus={event=>event.target.select()}/></div>}
    <div className="ru-dock"><nav className="ru-path" aria-label="Review lenses">{STEPS.map((item,index)=><button key={item.id} className={`${step === item.id ? 'active' : ''} ${isComplete(item.id) ? 'complete' : ''}`} aria-current={step === item.id ? 'step' : undefined} onClick={()=>navigate(item.id)}><span>{isComplete(item.id) ? <Check size={11}/> : String(index+1).padStart(2,'0')}</span>{item.short}</button>)}</nav><button className="ru-continue" onClick={continueReview} disabled={!guide} aria-label={continueLabel} title={continueLabel}><ArrowRight size={19}/></button></div>
    <span className="ru-save-status">{storageError ? 'Storage unavailable · keep this tab open' : 'Saved on this device'}</span>
  </div>;
}
