import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, ArrowUpRight, Braces, Check, ChevronDown, CircleHelp, Code2, ExternalLink, FileCode2, GitBranch, GitPullRequest, Loader2, MessageSquare, Pencil, ShieldCheck, SlidersHorizontal, X } from 'lucide-react';
import { ReviewGuide } from './ReviewGuide.jsx';
import { findingCommentAnchor, hunkCommentAnchor, locateInlineComment } from './inline-review.mjs';
import './pr-review.css';
import './inline-review.css';

const number = value => new Intl.NumberFormat('en').format(value || 0);
const shortSha = value => (value || '').slice(0, 8);
const statusCode = status => ({ added: 'A', modified: 'M', deleted: 'D', renamed: 'R' }[status] || 'M');
const edgeLabel = change => ({ added: 'New reference', removed: 'Removed reference', unchanged: 'Existing reference' }[change] || 'Reference');
const safeExternal = value => { try { const url = new URL(value); return url.protocol === 'https:' && url.hostname === 'github.com' ? url.href : null; } catch { return null; } };
const sourceUrl = (review, path, side, line) => {
  const url = safeExternal(review?.url);
  if (!url) return null;
  const root = url.split('/pull/')[0];
  return `${root}/blob/${side === 'base' ? review.baseSha : review.headSha}/${path.split('/').map(encodeURIComponent).join('/')}${line ? `#L${line}` : ''}`;
};
function scrollReviewEvidence(target) {
  if (!target) return;
  // Scroll only the review's visible panes, keeping the app header in place.
  let frame = requestAnimationFrame(() => {
    frame = requestAnimationFrame(() => {
      for (let pane = target.parentElement; pane; pane = pane.parentElement) {
        if (/auto|scroll/.test(getComputedStyle(pane).overflowY) && pane.scrollHeight > pane.clientHeight) {
          const item = target.getBoundingClientRect(), bounds = pane.getBoundingClientRect();
          pane.scrollTop += item.top - bounds.top - Math.max(0, (pane.clientHeight - Math.min(item.height, pane.clientHeight)) / 2);
        }
        if (pane.classList.contains('pr-workspace')) break;
      }
    });
  });
  return () => cancelAnimationFrame(frame);
}

async function request(projectId, endpoint, { signal, body, params = {} } = {}) {
  const url = new URL(`/api/review${endpoint}`, location.origin);
  url.searchParams.set('project', projectId);
  Object.entries(params).forEach(([key, value]) => value != null && url.searchParams.set(key, value));
  const response = await fetch(url, { signal, method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  let data;
  try { data = await response.json(); } catch { throw new Error('Atlas could not read the review response. Check that the local index server is running.'); }
  if (!response.ok) throw new Error(data.error || `The review request returned ${response.status}.`);
  return data;
}

function Status({ status }) {
  return <span className={`pr-file-status ${status || 'modified'}`} title={status} aria-label={status}>{statusCode(status)}</span>;
}

function CommentEditor({ anchor, initialBody = '', onSave, onCancel, editing = false }) {
  const [body, setBody] = useState(initialBody);
  const inputRef = useRef(null);
  const inputId = useId();
  useEffect(() => { inputRef.current?.focus({ preventScroll: true }); }, []);
  const save = () => { if (body.trim()) onSave(body.trim()); };
  return <div className="pr-inline-editor">
    <label htmlFor={inputId}>{editing ? 'Edit your comment' : 'Your comment'} · {anchor.side}:{anchor.line}</label>
    <textarea id={inputId} ref={inputRef} value={body} onChange={event => setBody(event.target.value)} placeholder="What should change, or what needs an answer?" aria-label={`Comment on ${anchor.path}:${anchor.line} (${anchor.side})`} onKeyDown={event => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); save(); }
      if (event.key === 'Escape') { event.preventDefault(); onCancel(); }
    }}/>
    <div className="pr-inline-thread-actions"><button className="pr-inline-save" disabled={!body.trim()} onClick={save}><Check size={12}/>{editing ? 'Save changes' : 'Save comment'}</button><button onClick={onCancel}>Cancel</button><span className="pr-inline-local">Saved in this review only</span></div>
  </div>;
}

function InlineComment({ comment, onUpdate, onDelete }) {
  const [editing, setEditing] = useState(false);
  return <article className={`pr-inline-thread ${comment.resolved ? 'resolved' : ''}`} aria-label={`Review comment on ${comment.side} line ${comment.line}`}>
    <div className="pr-inline-thread-header"><MessageSquare size={12}/><strong>{comment.resolved ? 'Resolved' : 'Your comment'}</strong>{comment.source === 'analysis' && <span>From Atlas observation</span>}<span>{comment.side}:{comment.line}</span></div>
    {editing ? <CommentEditor anchor={comment} initialBody={comment.body} editing onSave={body => { onUpdate(comment.id, { body }); setEditing(false); }} onCancel={() => setEditing(false)}/> : <>
      <p>{comment.body}</p>
      <div className="pr-inline-thread-actions"><button onClick={() => onUpdate(comment.id, { resolved: !comment.resolved })}>{comment.resolved ? 'Reopen' : <><Check size={11}/>Resolve</>}</button><button onClick={() => setEditing(true)}><Pencil size={10}/>Edit</button><button className="pr-inline-delete" onClick={() => onDelete(comment.id)}>Delete</button></div>
    </>}
  </article>;
}

function DiffView({ diff, loading, error, focus, fileGuide, comments = [], onAddComment, onUpdateComment, onDeleteComment, onEvidence }) {
  const diffRef = useRef(null);
  const [draft, setDraft] = useState(null);
  const fileComments = comments.filter(comment => comment.path === (diff?.path || fileGuide?.path));
  const commentLocations = fileComments.map(comment => ({ comment, location: locateInlineComment(diff, comment) }));
  const unplacedComments = commentLocations.filter(item => !item.location).map(item => item.comment);
  useEffect(() => setDraft(null), [diff?.path, loading]);
  useEffect(() => {
    if (!diffRef.current || !focus || focus.path !== diff?.path || loading) return;
    const line = Number(focus.line), hunk = Number(focus.hunkIndex);
    const target = Number.isInteger(line) && line > 0
      ? diffRef.current.querySelector(`[data-${focus.side === 'base' ? 'old' : 'new'}-line="${line}"]`)
      : null;
    return scrollReviewEvidence(target || (Number.isInteger(hunk) && hunk >= 0 ? diffRef.current.querySelector(`[data-hunk-index="${hunk}"]`) : diffRef.current));
  }, [diff, loading, focus]);
  const beginComment = (anchor, finding = null) => {
    if (!anchor || !onAddComment) return;
    setDraft({ ...anchor, source: finding ? 'analysis' : 'reviewer', findingId: finding?.id,
      body: finding ? [finding.title, finding.detail].filter(Boolean).join('\n\n') : '' });
  };
  const draftLocation = draft && locateInlineComment(diff, draft);
  useEffect(() => {
    if (!draft) return;
    return scrollReviewEvidence(diffRef.current?.querySelector('[data-comment-composer]'));
  }, [draft]);
  const renderComment = comment => <InlineComment key={comment.id} comment={comment} onUpdate={onUpdateComment} onDelete={onDeleteComment}/>;
  const unplaced = !!unplacedComments.length && <section className="pr-unplaced-comments" aria-label="Comments outside the displayed diff">
    <p>Saved comments whose lines are outside this diff preview.</p>{unplacedComments.map(renderComment)}
  </section>;
  if (loading) return <div className="pr-panel-state" role="status"><Loader2 className="pr-spin" size={20}/>Loading the snapshot diff…</div>;
  if (error) return <><div className="pr-panel-state pr-error" role="alert"><CircleHelp size={20}/>{error}</div>{unplaced}</>;
  if (!diff) return <div className="pr-panel-state"><FileCode2 size={22}/>Choose a changed file to read its diff.</div>;
  if (diff.binary) return <><div className="pr-panel-state"><FileCode2 size={22}/><strong>Binary file changed</strong><span>This file has no text diff.</span></div>{unplaced}</>;
  if (diff.excluded) return <><div className="pr-panel-state"><FileCode2 size={22}/><strong>Source preview unavailable</strong><span>{typeof diff.excluded === 'string' ? diff.excluded : 'This file is outside the code index.'} Its Git change is included in the review.</span></div>{unplaced}</>;
  const hunks = diff.hunks || [];
  if (!hunks.length) return <><div className="pr-panel-state"><FileCode2 size={22}/><strong>No text changes to display</strong><span>{diff.status === 'renamed' ? 'The file moved without a text change.' : 'The change may only affect file metadata.'}</span></div>{unplaced}</>;
  return <>
    <div className="pr-inline-review-note"><MessageSquare size={12}/><span>Select a line number to comment.</span><span>{fileComments.filter(comment => !comment.resolved).length ? `${fileComments.filter(comment => !comment.resolved).length} open · ` : ''}Local comments · not posted</span></div>
    <div ref={diffRef} className="pr-diff-scroll pr-inline-diff" tabIndex="0" aria-label={`Diff for ${diff.path}`}>
      {hunks.map((hunk, index) => {
        const explanation = fileGuide?.hunks?.find(item => item.index === index);
        const blockAnchor = hunkCommentAnchor(diff, index, fileGuide?.oldPath);
        return <section className="pr-diff-block" data-hunk-index={index} key={index} aria-label={`Change block ${index + 1}`}>
          <div className="pr-hunk-reading">
            <div className="pr-hunk-reading-header"><span>Change {index + 1} of {hunks.length}</span>{blockAnchor && onAddComment && <button onClick={() => beginComment(blockAnchor)}><MessageSquare size={12}/>Comment on block</button>}</div>
            <p className={explanation?.summary ? '' : 'pr-hunk-no-summary'}>{explanation?.summary || 'Read the changed lines below. A source explanation is not available for this block.'}</p>
            {(explanation?.watchpoints || []).map((finding, findingIndex) => {
              const anchor = findingCommentAnchor(diff, finding, index, fileGuide?.oldPath);
              const evidence = finding.evidence?.[0];
              return <div className="pr-hunk-finding" key={finding.id || findingIndex}>
                <strong>{finding.title}</strong><p>{finding.detail}</p>
                <div className="pr-hunk-finding-actions">{anchor && onAddComment && <button onClick={() => beginComment(anchor, finding)}><MessageSquare size={11}/>Use as comment</button>}{evidence && <button className="pr-hunk-source" onClick={() => onEvidence?.(evidence)}>{evidence.side || 'head'}:{evidence.line}<ArrowUpRight size={10}/></button>}</div>
              </div>;
            })}
          </div>
          <div className="pr-hunk-code"><table className="pr-diff-table"><caption className="pr-sr-only">Change block {index + 1}. Unified diff. Old line, new line, change, source code.</caption><tbody>
            <tr className="pr-diff-hunk"><td colSpan="4"><span>@@ −{hunk.oldStart},{hunk.oldLines} +{hunk.newStart},{hunk.newLines} @@</span></td></tr>
            {hunk.lines.map((line, lineIndex) => {
              const lineComments = commentLocations.filter(item => item.location?.hunkIndex === index && item.location?.lineIndex === lineIndex);
              const composer = draftLocation?.hunkIndex === index && draftLocation?.lineIndex === lineIndex;
              const lineNumber = side => {
                const value = side === 'base' ? line.oldLine : line.newLine;
                return value == null ? '' : onAddComment ? <button className="pr-line-comment-button" aria-label={`Comment on ${side} line ${value}`} title={`Comment on ${side} line ${value}`} onClick={() => beginComment({ path: diff.path, sourcePath: side === 'base' ? diff.oldPath || fileGuide?.oldPath || diff.path : diff.path, side, line: value, hunkIndex: index })}>{value}</button> : value;
              };
              return <React.Fragment key={`${index}-${lineIndex}`}>
                <tr data-old-line={line.oldLine} data-new-line={line.newLine} className={`pr-diff-line ${line.kind} ${focus?.path === diff.path && Number(focus.line) === (focus.side === 'base' ? line.oldLine : line.newLine) ? 'pr-diff-target' : ''}`}>
                  <td className="pr-line-number">{lineNumber('base')}</td><td className="pr-line-number">{lineNumber('head')}</td><td className="pr-line-sign" aria-label={line.kind}>{line.kind === 'added' ? '+' : line.kind === 'removed' ? '−' : ' '}</td><td className="pr-code"><code>{line.text || ' '}</code></td>
                </tr>
                {!!lineComments.length && <tr className="pr-inline-comment-row"><td colSpan="4">{lineComments.map(({ comment }) => renderComment(comment))}</td></tr>}
                {composer && <tr className="pr-inline-comment-row" data-comment-composer><td colSpan="4"><div className="pr-inline-thread"><CommentEditor key={`${draft.side}:${draft.line}:${draft.findingId || 'reviewer'}`} anchor={draft} initialBody={draft.body} onSave={body => { onAddComment({ ...draft, body, resolved: false }); setDraft(null); }} onCancel={() => setDraft(null)}/></div></td></tr>}
              </React.Fragment>;
            })}
          </tbody></table></div>
        </section>;
      })}
    </div>
    {diff.truncated && <p className="pr-inline-note">This diff preview is capped. Inspect the full Git comparison before completing the review.</p>}
    {unplaced}
  </>;
}

function ConnectionList({ edges, selectedEdge, onEdge, onEvidence, source, sourceLoading, sourceError, review }) {
  const sourceRef = useRef(null);
  useEffect(() => { if (sourceLoading || source) return scrollReviewEvidence(sourceRef.current); }, [sourceLoading, source]);
  const displayed = selectedEdge ? [selectedEdge] : edges;
  return <div className="pr-connections">
    <div className="pr-connections-intro"><p>Static references in the base and head snapshots. A reference suggests code to inspect; it does not prove a runtime effect.</p>{selectedEdge && <button className="pr-subtle-button" onClick={() => onEdge(null)}><X size={12}/>Show all file connections</button>}</div>
    {!displayed.length && <div className="pr-panel-state"><GitBranch size={22}/><strong>No resolved file connections</strong><span>Dynamic dispatch, external packages, and unindexed languages may not appear.</span></div>}
    {displayed.slice(0, 150).map((edge, index) => <article className={`pr-connection ${edge.change}`} key={`${edge.source}-${edge.target}-${edge.kind}-${index}`}>
      <div className="pr-connection-route"><code>{edge.source.replace(/^file:/, '')}</code><ArrowRight size={13}/><code>{edge.target.replace(/^file:/, '')}</code></div>
      <div className="pr-connection-meta"><span className={`pr-edge-badge ${edge.change}`}>{edgeLabel(edge.change)}</span><span>{edge.kind}</span><span>{number(edge.weight || 1)} reference{(edge.weight || 1) === 1 ? '' : 's'}</span></div>
      <div className="pr-evidence-links">{(edge.evidence || []).slice(0, 8).map((evidence, evidenceIndex) => <button key={evidenceIndex} className={`pr-evidence-link ${source?.path === evidence.path && source?.highlightLine === evidence.line && source?.side === evidence.side ? 'active' : ''}`} onClick={() => onEvidence(evidence)}><Code2 size={12}/><code>{evidence.path}:{evidence.line}</code><span>{evidence.side || (edge.change === 'removed' ? 'base' : 'head')}</span><ArrowUpRight size={12}/></button>)}{!edge.evidence?.length && <span className="pr-inline-note">No sampled source location is available for this connection.</span>}</div>
    </article>)}
    {displayed.length > 150 && <p className="pr-inline-note">Showing 150 connections. Select a connection in the map to inspect it directly.</p>}
    {(source || sourceLoading || sourceError) && <section ref={sourceRef} className="pr-source-evidence" aria-label="Connection source evidence">
      <div className="pr-source-heading"><span><Code2 size={13}/>Source evidence</span>{source && <span className="pr-snapshot-label">{source.side} · {shortSha(source.side === 'base' ? review.baseSha : review.headSha)}</span>}</div>
      {sourceLoading ? <div className="pr-panel-state" role="status"><Loader2 className="pr-spin" size={18}/>Reading snapshot source…</div> : sourceError ? <p className="pr-inline-note pr-error" role="alert">{sourceError}</p> : source && <>
        <div className="pr-source-path"><code>{source.path}:{source.highlightLine}</code>{sourceUrl(review, source.path, source.side, source.highlightLine) && <a href={sourceUrl(review, source.path, source.side, source.highlightLine)} target="_blank" rel="noreferrer">Open on GitHub<ExternalLink size={11}/></a>}</div>
        <div className="pr-source-lines" tabIndex="0">{source.lines.map((line, index) => <div className={source.startLine + index === source.highlightLine ? 'highlighted' : ''} key={index}><span>{source.startLine + index}</span><code>{line || ' '}</code></div>)}</div>
        {source.truncated && <p className="pr-inline-note">Showing a source excerpt around this reference.</p>}
      </>}
    </section>}
  </div>;
}

export function PRReview({ project, onClose }) {
  const [options, setOptions] = useState(null), [mode, setMode] = useState('github');
  const [scopeOpen, setScopeOpen] = useState(true);
  const [pr, setPr] = useState(''), [base, setBase] = useState(''), [head, setHead] = useState('HEAD');
  const [review, setReview] = useState(null), [job, setJob] = useState(null), [starting, setStarting] = useState(false), [restoring, setRestoring] = useState(true);
  const [error, setError] = useState(''), [optionsError, setOptionsError] = useState('');
  const [selectedPath, setSelectedPath] = useState(''), [diffFocus, setDiffFocus] = useState(null);
  const [tab, setTab] = useState('diff'), [selectedEdge, setSelectedEdge] = useState(null), [reviewed, setReviewed] = useState(new Set());
  const [diff, setDiff] = useState(null), [diffLoading, setDiffLoading] = useState(false), [diffError, setDiffError] = useState('');
  const [evidence, setEvidence] = useState(null), [source, setSource] = useState(null), [sourceLoading, setSourceLoading] = useState(false), [sourceError, setSourceError] = useState('');
  const epoch = useRef(0), submitController = useRef(null), pendingEdge = useRef(null);
  const projectId = project?.id;
  const reviewedKey = review ? `atlas-reviewed:v2:${projectId}:${review.id}:${review.baseSha}:${review.headSha}` : null;
  const busy = starting || ['queued', 'running'].includes(job?.state);
  const showScope = scopeOpen || !review;
  const files = review?.files || [];
  const selectedFile = files.find(file => file.path === selectedPath);
  const edges = useMemo(() => (review?.graph?.edges || []).filter(edge => edge.source === `file:${selectedPath}` || edge.target === `file:${selectedPath}`), [review, selectedPath]);
  useEffect(() => { if (error || optionsError) setScopeOpen(true); }, [error, optionsError, review?.id]);
  function applySession(data) {
    setJob(data.job || null);
    if (data.review && (!data.job || data.job.state === 'complete')) {
      setReview(data.review);
      setScopeOpen(false);
      setSelectedPath(previous => data.review.files.some(file => file.path === previous) ? previous : data.review.files[0]?.path || '');
    }
    if (data.job?.state === 'error') setError(data.job.error || data.job.message || 'The review could not be prepared.');
  }
  useEffect(() => {
    const sequence = ++epoch.current, controller = new AbortController();
    submitController.current?.abort();
    setReview(null); setJob(null); setError(''); setOptionsError(''); setOptions(null); setScopeOpen(true); setSelectedPath(''); setDiffFocus(null); setReviewed(new Set()); setStarting(false); setRestoring(true); setPr(''); setBase(''); setHead('HEAD');
    if (!projectId) { setRestoring(false); return () => controller.abort(); }
    request(projectId, '/options', { signal: controller.signal }).then(data => {
      if (sequence !== epoch.current) return;
      setOptions(data); setBase(data.base || 'HEAD~1'); setHead(data.head || 'HEAD'); setMode(data.repository ? 'github' : 'local');
      if (data.available === false) setOptionsError(data.error || 'This repository does not have a committed revision to review yet.');
    }).catch(err => { if (err.name !== 'AbortError' && sequence === epoch.current) setOptionsError(err.message); });
    request(projectId, '', { signal: controller.signal }).then(data => { if (sequence === epoch.current) applySession(data); }).catch(err => { if (err.name !== 'AbortError' && sequence === epoch.current) setError(err.message); }).finally(() => { if (sequence === epoch.current) setRestoring(false); });
    return () => { controller.abort(); submitController.current?.abort(); ++epoch.current; };
  }, [projectId]);
  useEffect(() => {
    if (!projectId || !['queued', 'running'].includes(job?.state)) return undefined;
    const controller = new AbortController(), sequence = epoch.current;
    const timer = setTimeout(() => request(projectId, '', { signal: controller.signal }).then(data => { if (sequence === epoch.current) applySession(data); }).catch(err => {
      if (err.name !== 'AbortError' && sequence === epoch.current) { setError(err.message); setJob(previous => ({ ...previous, state: 'error' })); }
    }), 1200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [projectId, job]);
  useEffect(() => {
    const edge = pendingEdge.current;
    pendingEdge.current = null;
    setDiff(null); setDiffError(''); setSelectedEdge(edge); setEvidence(null); setSource(null); setTab(edge ? 'connections' : 'diff');
    if (!projectId || !review?.id || !selectedPath) { setDiffLoading(false); return undefined; }
    const controller = new AbortController();
    setDiffLoading(true);
    request(projectId, '/diff', { signal: controller.signal, params: { review: review.id, path: selectedPath } }).then(data => { if (!controller.signal.aborted) setDiff(data); }).catch(err => { if (err.name !== 'AbortError' && !controller.signal.aborted) setDiffError(err.message); }).finally(() => { if (!controller.signal.aborted) setDiffLoading(false); });
    return () => controller.abort();
  }, [projectId, review?.id, selectedPath]);
  useEffect(() => {
    setSource(null); setSourceError('');
    if (!evidence || !projectId || !review?.id) { setSourceLoading(false); return undefined; }
    const controller = new AbortController();
    setSourceLoading(true);
    request(projectId, '/source', { signal: controller.signal, params: { review: review.id, path: evidence.path, side: evidence.side || 'head', line: evidence.line } }).then(data => { if (!controller.signal.aborted) setSource(data); }).catch(err => { if (err.name !== 'AbortError' && !controller.signal.aborted) setSourceError(err.message); }).finally(() => { if (!controller.signal.aborted) setSourceLoading(false); });
    return () => controller.abort();
  }, [projectId, review?.id, evidence]);
  useEffect(() => {
    if (!diffFocus || diffLoading || diff?.path !== diffFocus.path || !diffFocus.line) return;
    const inDiff = diff.hunks?.some(hunk => hunk.lines.some(line =>
      (diffFocus.side === 'base' ? line.oldLine : line.newLine) === Number(diffFocus.line)));
    if (inDiff || (Number.isInteger(diffFocus.hunkIndex) && diff.hunks?.[diffFocus.hunkIndex])) return;
    setEvidence({
      path: diffFocus.side === 'base' ? selectedFile?.oldPath || diffFocus.path : diffFocus.path,
      side: diffFocus.side || 'head', line: Number(diffFocus.line),
    });
    setTab('connections');
  }, [diff, diffLoading, diffFocus, selectedFile?.oldPath]);
  useEffect(() => {
    if (!review?.id) { setReviewed(new Set()); return; }
    try { setReviewed(new Set(JSON.parse(localStorage.getItem(reviewedKey) || sessionStorage.getItem(`atlas-reviewed:${projectId}:${review.id}`) || '[]'))); }
    catch { setReviewed(new Set()); }
  }, [projectId, review?.id, reviewedKey]);
  async function startReview(event) {
    event.preventDefault();
    if (!projectId || busy) return;
    submitController.current?.abort();
    const controller = new AbortController(), sequence = ++epoch.current;
    submitController.current = controller;
    pendingEdge.current = null;
    setStarting(true); setError(''); setReview(null); setJob(null); setSelectedPath(''); setReviewed(new Set()); setSelectedEdge(null); setEvidence(null);
    try { const data = await request(projectId, '', { signal: controller.signal, body: mode === 'github' ? { pr: pr.trim() } : { base: base.trim(), head: head.trim() } }); if (sequence === epoch.current) applySession(data); }
    catch (err) { if (err.name !== 'AbortError' && sequence === epoch.current) setError(err.message); }
    finally { if (sequence === epoch.current) setStarting(false); }
  }
  function selectFile(path, evidence) {
    pendingEdge.current = null;
    setSelectedPath(path); setSelectedEdge(null); setEvidence(null); setTab('diff');
    setDiffFocus(evidence ? { ...evidence, path } : null);
  }
  function selectEdge(edge) {
    if (edge && edge.source !== `file:${selectedPath}` && edge.target !== `file:${selectedPath}`) {
      const changed = files.find(file => edge.source === `file:${file.path}` || edge.target === `file:${file.path}`);
      if (changed) { pendingEdge.current = edge; setSelectedPath(changed.path); }
    }
    setSelectedEdge(edge); setTab('connections'); setEvidence(null); setSource(null);
  }
  function markReviewed(path = selectedPath) {
    if (!files.some(file => file.path === path)) return;
    setReviewed(previous => {
      const next = new Set(previous);
      next.has(path) ? next.delete(path) : next.add(path);
      try { localStorage.setItem(reviewedKey, JSON.stringify([...next])); } catch {}
      return next;
    });
  }
  function tabKeyDown(event) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 'diff' : event.key === 'End' ? 'connections' : tab === 'diff' ? 'connections' : 'diff';
    setTab(next);
    document.getElementById(`pr-${next}-tab`)?.focus();
  }
  function selectGraphNode(node) {
    if (files.some(file => file.path === node.path)) { selectFile(node.path); return; }
    const connected = review.graph.edges.find(edge => (edge.source === node.id && edge.target === `file:${selectedPath}`) || (edge.target === node.id && edge.source === `file:${selectedPath}`)) || review.graph.edges.find(edge => edge.source === node.id || edge.target === node.id);
    if (connected) selectEdge(connected);
  }
  const githubUrl = safeExternal(review?.url);
  return <section className={`pr-workspace ${review ? 'has-review' : ''}`} aria-label="Pull request review workspace">
    <div className="pr-heading">
      <div className="pr-heading-copy"><span className="pr-kicker"><GitPullRequest size={13}/>REVIEW WORKSPACE</span><h1 title={review?.title}>{review ? <><GitPullRequest size={14}/>{review.number ? `PR #${review.number}` : 'Compare revisions'}<span className="pr-scope-title">{review.title}</span></> : 'Follow the change.'}</h1><p>{review ? <><span>{review.baseRef} <code>{shortSha(review.baseSha)}</code></span><ArrowRight size={12}/><span>{review.headRef} <code>{shortSha(review.headSha)}</code></span></> : 'See what changed, what it connects to, and where to look next.'}</p></div>
      <div className="pr-heading-actions">{review && <button className="pr-subtle-button pr-scope-toggle" onClick={() => setScopeOpen(!showScope)} aria-expanded={showScope} aria-controls="pr-review-scope"><SlidersHorizontal size={12}/>{showScope ? 'Hide scope' : 'Change scope'}</button>}{githubUrl && <a className="pr-secondary-button" href={githubUrl} target="_blank" rel="noreferrer">Open pull request<ExternalLink size={12}/></a>}<button className="pr-subtle-button" onClick={onClose}><ArrowLeft size={13}/>Back to explorer</button></div>
    </div>
    {!projectId ? <div className="pr-panel-state pr-no-project"><GitPullRequest size={36}/><h2>Choose a repository first.</h2><p>Open an indexed repository to review a pull request or compare two Git refs.</p><button className="pr-primary-button" onClick={onClose}>Back to explorer<ArrowRight size={14}/></button></div> : <>
      {showScope && <div id="pr-review-scope" className="pr-scope-controls"><form className="pr-scope-form" onSubmit={startReview}>
        <div className="pr-scope-tabs" role="group" aria-label="Review source"><button type="button" className={mode === 'github' ? 'active' : ''} onClick={() => setMode('github')}><GitPullRequest size={12}/>GitHub PR</button><button type="button" className={mode === 'local' ? 'active' : ''} onClick={() => setMode('local')}><GitBranch size={12}/>Compare refs</button></div>
        {mode === 'github' ? <label className="pr-pr-input"><span className="pr-sr-only">Pull request URL or number</span><input aria-describedby="pr-scope-help" value={pr} onChange={event => setPr(event.target.value)} placeholder={options?.repository ? `${options.repository} · PR URL or #number` : 'https://github.com/owner/repository/pull/123'} required autoComplete="off" spellCheck="false"/></label> : <div className="pr-ref-inputs"><label><span>Base</span><input value={base} onChange={event => setBase(event.target.value)} list="pr-git-refs" placeholder="main" required autoComplete="off" spellCheck="false"/></label><ArrowRight size={14}/><label><span>Head</span><input value={head} onChange={event => setHead(event.target.value)} list="pr-git-refs" placeholder="HEAD" required autoComplete="off" spellCheck="false"/></label><datalist id="pr-git-refs">{(options?.branches || []).map(branch => <option value={branch} key={branch}/>)}</datalist></div>}
        <button type="submit" className="pr-primary-button" disabled={busy || restoring || options?.available === false}>{busy ? <Loader2 className="pr-spin" size={14}/> : <GitPullRequest size={14}/>} {busy ? 'Preparing review' : 'Review changes'}{!busy && <ArrowRight size={13}/>}</button>
      </form>
      <div className="pr-scope-help" id="pr-scope-help"><ShieldCheck size={12}/><span>{mode === 'github' ? 'Review a pull request from this repository. Atlas fetches its base and head snapshots.' : 'Compare committed Git refs from their merge base. Working tree changes are not included.'}</span><span className="pr-snapshot-note">ISOLATED SNAPSHOTS</span></div></div>}
      {(error || optionsError) && <div className="pr-alert" role="alert"><CircleHelp size={15}/><span>{error || optionsError}</span><button onClick={() => { setError(''); setOptionsError(''); }} aria-label="Dismiss review error"><X size={14}/></button></div>}
      {busy && <div className="pr-progress" role="status" aria-live="polite"><Loader2 className="pr-spin" size={15}/><div><strong>{job?.message || 'Preparing the review scope…'}</strong><span>{job?.phase ? job.phase.replaceAll('_', ' ') : 'Resolving the base and head revisions'}</span></div><div className="pr-progress-track"><span/></div></div>}
      {restoring && !review && <div className="pr-panel-state" role="status"><Loader2 className="pr-spin" size={22}/>Loading review workspace…</div>}
      {!restoring && !review && !busy && <div className="pr-welcome">
        <div className="pr-welcome-symbol"><GitPullRequest size={29}/><span/><span/><span/></div>
        <span className="pr-kicker">THE CHANGE, IN CONTEXT</span><h2>A diff tells half the story.</h2><p>Bring a pull request into Atlas to connect the changed lines<br className="pr-desktop-break"/> to the code that depends on them.</p>
        <div className="pr-welcome-flow"><div><GitBranch size={18}/><strong>Potential dependents</strong><span>What uses this code</span></div><ArrowRight size={21}/><div className="active"><FileCode2 size={18}/><strong>Your changes</strong><span>Files, symbols, and diffs</span></div><ArrowRight size={21}/><div><Code2 size={18}/><strong>Dependencies</strong><span>What this code uses</span></div></div>
        <p className="pr-welcome-note"><CircleHelp size={12}/>Connections come from static code analysis. They guide inspection, not runtime predictions.</p>
      </div>}
      {review && <ReviewGuide
        key={`${projectId}:${review.id}:${review.baseSha}:${review.headSha}`}
        review={review} projectId={projectId} selectedPath={selectedPath}
        onSelectFile={selectFile} reviewed={reviewed} onToggleReviewed={markReviewed}
        onSelectContext={selectGraphNode} onSelectConnection={selectEdge}
      >
        {({ fileGuide, comments, onAddComment, onUpdateComment, onDeleteComment }) =>
            <section className="pr-file-detail" aria-label="Selected file review">
              <div className="pr-detail-heading"><div>{selectedFile ? <><Status status={selectedFile.status}/><code>{selectedFile.path}</code></> : <span>File review</span>}</div></div>
              {selectedFile?.oldPath && selectedFile.oldPath !== selectedFile.path && <p className="pr-rename-note">Renamed from <code>{selectedFile.oldPath}</code></p>}
              <div className="pr-detail-tabs" role="tablist" aria-label="File review view" onKeyDown={tabKeyDown}><button role="tab" tabIndex={tab === 'diff' ? 0 : -1} aria-selected={tab === 'diff'} aria-controls="pr-diff-panel" id="pr-diff-tab" className={tab === 'diff' ? 'active' : ''} onClick={() => setTab('diff')}><Code2 size={13}/>Diff<span>{selectedFile ? `+${selectedFile.additions} −${selectedFile.deletions}` : ''}</span></button><button role="tab" tabIndex={tab === 'connections' ? 0 : -1} aria-selected={tab === 'connections'} aria-controls="pr-connections-panel" id="pr-connections-tab" className={tab === 'connections' ? 'active' : ''} onClick={() => setTab('connections')}><GitBranch size={13}/>Connections<span>{edges.length}</span></button><span className="pr-detail-snapshot"><ShieldCheck size={11}/>Exact revision</span></div>
              {!!selectedFile?.changedSymbols?.length && <details className="pr-changed-symbols"><summary><Braces size={12}/>{number(selectedFile.changedSymbols.length)} changed symbol{selectedFile.changedSymbols.length === 1 ? '' : 's'}<ChevronDown size={12}/></summary><div>{selectedFile.changedSymbols.map((symbol, index) => <button key={`${symbol.id}-${index}`} className={`pr-symbol ${symbol.change}`} onClick={() => { setEvidence({ path: symbol.side === 'base' ? selectedFile.oldPath || selectedFile.path : selectedFile.path, line: symbol.line, side: symbol.side }); setTab('connections'); }} title={`Read ${symbol.side} source at line ${symbol.line}`}><Braces size={11}/><code>{symbol.name}</code><span>{symbol.change}</span><small>{symbol.side}:{symbol.line}</small><ArrowUpRight size={11}/></button>)}</div></details>}
              {tab === 'diff' ? <div id="pr-diff-panel" role="tabpanel" aria-labelledby="pr-diff-tab"><DiffView diff={diff} loading={diffLoading} error={diffError} focus={diffFocus} fileGuide={fileGuide} comments={comments} onAddComment={onAddComment} onUpdateComment={onUpdateComment} onDeleteComment={onDeleteComment} onEvidence={evidence => { setEvidence(evidence); setTab('connections'); }}/></div> : <div id="pr-connections-panel" role="tabpanel" aria-labelledby="pr-connections-tab"><ConnectionList edges={edges} selectedEdge={selectedEdge} onEdge={selectEdge} onEvidence={setEvidence} source={source} sourceLoading={sourceLoading} sourceError={sourceError} review={review}/></div>}
            </section>
        }
      </ReviewGuide>}
    </>}
  </section>;
}
