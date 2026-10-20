import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, CheckCircle2, ChevronDown, ChevronRight, CircleHelp, Code2, FolderPlus, GitBranch, Layers3, Loader2, Orbit, Plus, RefreshCw, Save, Search, Sparkles, Trash2, X } from 'lucide-react';
import './project-manager.css';

const number = value => new Intl.NumberFormat('en').format(value || 0);
const clone = value => JSON.parse(JSON.stringify(value));
const unique = values => [...new Set(values.filter(Boolean))];
const inProgress = project => project?.state === 'indexing' || ['queued', 'running', 'pending'].includes(project?.job?.state);
const hasGraph = project => project?.state === 'ready' || Boolean(project?.indexedAt);
const projectEndpoint = (id, suffix = '') => `/api/projects/${encodeURIComponent(id)}${suffix}`;
const groupKey = (kind, id) => `${kind}:${id}`;
const groupEntries = map => [
  ...(map?.features || []).map(group => ({ ...group, groupKind: 'features' })),
  ...(map?.foundations || []).map(group => ({ ...group, groupKind: 'foundations' })),
];
const makeId = prefix => `${prefix}-${globalThis.crypto?.randomUUID?.().slice(0, 8) || Math.random().toString(36).slice(2, 10)}`;
const blankGroup = kind => ({ id: makeId(kind === 'features' ? 'feature' : 'foundation'), name: kind === 'features' ? 'New feature' : 'New foundation', summary: '', paths: [], evidence: [], ...(kind === 'features' ? { capabilities: [] } : {}) });
const blankCapability = () => ({ id: makeId('capability'), name: 'New capability', summary: '', paths: [], evidence: [] });

function readableError(data, fallback) {
  const primary = typeof data?.error === 'string' ? data.error : data?.error?.message || data?.message;
  const details = data?.details || data?.errors;
  const extra = Array.isArray(details) ? details.map(item => typeof item === 'string' ? item : item.message || item.path || '').filter(Boolean).join(' ') : typeof details === 'string' ? details : '';
  return [primary || fallback, extra].filter(Boolean).join(' ');
}

async function request(url, { method = 'GET', body, signal } = {}) {
  const payload = body === undefined && method !== 'GET' ? {} : body;
  const response = await fetch(url, { method, signal, headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' }, body: payload === undefined ? undefined : JSON.stringify(payload) });
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) throw new Error(readableError(data, response.status === 422 || response.status === 400 ? 'Check the fields below and try again.' : `Atlas could not complete this request (${response.status}).`));
  return data;
}

function normalizeMap(map) {
  const next = clone(map);
  const cleanGroup = group => ({
    ...group,
    name: (group.name || '').trim(),
    summary: (group.summary || '').trim(),
    paths: unique((group.paths || []).map(path => path.trim())),
    ...(Array.isArray(group.capabilities) ? { capabilities: group.capabilities.map(cleanGroup) } : {}),
  });
  next.features = (next.features || []).map(cleanGroup);
  next.foundations = (next.foundations || []).map(cleanGroup);
  return next;
}

function dateLabel(value) {
  if (!value) return 'Not indexed yet';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Index available' : `Indexed ${date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`;
}

function changeLabel(changes) {
  if (changes?.state === 'changed') return `${number(changes.count)} ${changes.count === 1 ? 'change' : 'changes'} since the last index`;
  if (changes?.state === 'current') return 'Up to date with the last check';
  return 'Not checked against this index';
}

function coverageLabel(project) {
  const analysis = project?.languageAnalysis || project?.stats?.languageAnalysis;
  if (Array.isArray(analysis) && analysis.length) {
    const python = analysis.find(item => item.name === 'Python' && item.mode === 'semantic' && (item.parsedFiles || 0) > 0);
    const scripts = analysis.filter(item => item.mode === 'imports-and-symbols' && (item.parsedFiles || 0) > 0);
    return [
      python ? `${number(python.parsedFiles)} Python ${python.parsedFiles === 1 ? 'file includes' : 'files include'} definitions, imports, calls, and inheritance.` : '',
      scripts.length ? `${scripts.map(item => item.name).join(' and ')} include named definitions and relative imports.` : '',
      analysis.some(item => item.mode === 'file-only' || item.parsedFiles < item.files) ? 'Other files appear without their internal definitions.' : '',
    ].filter(Boolean).join(' ');
  }
  const parsed = project?.coverage?.parsedLanguages || project?.parsedLanguages;
  if (!parsed) return 'Language detail will appear after this project is indexed again.';
  const names = Array.isArray(parsed) ? parsed : [parsed];
  return `${names.join(', ')} code is parsed for definitions and links. Other languages appear as files.`;
}

function sourceText(value) {
  if (typeof value === 'string') return value;
  if (!value || typeof value !== 'object') return '';
  return [value.path ? `${value.path}${value.line ? `:${value.line}` : ''}` : '', value.reason || value.summary || value.description || value.text || ''].filter(Boolean).join(' — ');
}

function foldCapabilities(group) {
  const { capabilities = [], ...next } = group;
  next.paths = unique([...(next.paths || []), ...capabilities.flatMap(capability => capability.paths || [])]);
  next.evidence = [...(next.evidence || []), ...capabilities.flatMap(capability => capability.evidence || [])].filter((value, index, values) => values.findIndex(other => JSON.stringify(other) === JSON.stringify(value)) === index);
  return next;
}

function DocumentationEditor({group, onChange}) {
  if (!group.documentation && !group.entryPoints?.length) return null;
  return <details className="pm-evidence"><summary>Feature guide and starting points</summary>{Object.entries(group.documentation || {}).map(([key, value]) => <label className="pm-field" key={key}><span>{key}</span><textarea rows={5} maxLength={8000} value={value} onChange={event => onChange({...group, documentation: {...group.documentation, [key]: event.target.value}})}/></label>)}{group.entryPoints?.map((entry, index) => <p key={index}>{entry.path}:{entry.line} — {entry.reason}</p>)}</details>;
}

function Evidence({ group }) {
  const reasons = [group.reason, group.suggestionReason, ...(Array.isArray(group.reasons) ? group.reasons : [])].map(sourceText).filter(Boolean);
  const evidence = (Array.isArray(group.evidence) ? group.evidence : []).map(value => ({
    source: sourceText(value),
    excerpt: typeof value?.excerpt === 'string' ? value.excerpt.slice(0, 900) + (value.excerpt.length > 900 ? '…' : '') : '',
  })).filter(item => item.source || item.excerpt);
  if (!reasons.length && !evidence.length) return null;
  return <details className="pm-evidence">
    <summary><Search size={13}/><span>Why this group was suggested</span><ChevronDown size={13}/></summary>
    {reasons.map((reason, index) => <p key={`reason-${index}`}>{reason}</p>)}
    {evidence.length > 0 && <ul>{evidence.map((item, index) => <li key={index}>{item.source}{item.excerpt && <p className="pm-source-excerpt">{item.excerpt}</p>}</li>)}</ul>}
  </details>;
}

function ProjectCard({ project, active, busy, onOpen, onReview, onIndex, onCheck, onAutoUpdate }) {
  const working = inProgress(project);
  const state = working ? 'indexing' : project.state || 'new';
  const labels = { new: 'Ready to index', indexing: project.job?.phase === 'suggestions' ? 'Finding groups' : 'Indexing', ready: 'Ready to explore', error: 'Needs attention' };
  const languages = Array.isArray(project.languages) ? project.languages : Object.entries(project.languages || {}).map(([name, files]) => ({ name, files }));
  return <article className={`pm-project-card ${active ? 'is-active' : ''}`}>
    <div className="pm-card-heading"><span className="pm-repository-icon"><GitBranch size={20}/></span><div><h3>{project.name}</h3><p className="pm-project-path" title={project.path}>{project.path}</p></div>{active && <span className="pm-active-label">CURRENT</span>}</div>
    <div className="pm-card-status"><span className={`pm-state ${state}`}>{working ? <Loader2 size={12} className="pm-spin"/> : state === 'ready' ? <CheckCircle2 size={12}/> : <span className="pm-state-dot"/>}{labels[state] || state}</span><span className="pm-map-state">{project.mapStatus === 'reviewed' ? 'Map reviewed' : project.mapStatus === 'missing' || !project.indexedAt ? 'Map not built yet' : 'Map needs a look'}</span></div>
    {working && <div className="pm-job" role="status"><p>{project.job?.message || 'Reading the repository and building its local map…'}</p><progress aria-label={`Indexing ${project.name}`}/><span>{project.job?.phase ? project.job.phase.replaceAll('_', ' ') : 'Starting'}</span></div>}
    {project.job?.error && <p className="pm-inline-error">{typeof project.job.error === 'string' ? project.job.error : 'The last job did not finish. Try rebuilding the index.'}</p>}
    {project.indexedAt && <><div className="pm-card-metrics"><span><strong>{number(project.stats?.files)}</strong> files</span><span><strong>{number(project.stats?.symbols)}</strong> definitions</span><span><strong>{number(project.stats?.semanticEdges ?? project.stats?.edges)}</strong> links</span></div><div className="pm-languages">{languages.slice(0, 4).map(language => <span key={language.name}>{language.name}{language.files !== undefined && <small>{number(language.files)}</small>}</span>)}{languages.length > 4 && <span>+{languages.length - 4} more</span>}</div><p className="pm-coverage">{coverageLabel(project)}</p></>}
    <div className="pm-card-actions"><button className="pm-button primary" disabled={!hasGraph(project) || busy === 'activate'} onClick={() => onOpen(project)}>{busy === 'activate' ? <Loader2 size={14} className="pm-spin"/> : <Orbit size={14}/>}Open universe<ArrowRight size={14}/></button><button className="pm-button secondary" disabled={!project.indexedAt || busy === 'review'} onClick={() => onReview(project)}><Layers3 size={14}/>{project.hasSuggestion ? 'Review new suggestions' : 'Review map'}</button></div>
    <div className="pm-card-maintenance"><div><span className="pm-small-date">{dateLabel(project.indexedAt)}</span><span className={`pm-change-state ${project.changes?.state || 'unknown'}`}>{changeLabel(project.changes)}</span></div><div className="pm-maintenance-actions"><button className="pm-text-button" disabled={working || Boolean(busy)} onClick={() => onCheck(project)} title="Check this repository for changes">{busy === 'check' ? <Loader2 size={12} className="pm-spin"/> : <Search size={12}/>}Check</button><button className="pm-text-button" disabled={working || Boolean(busy)} onClick={() => onIndex(project)}>{busy === 'index' ? <Loader2 size={12} className="pm-spin"/> : <RefreshCw size={12}/>} {project.state === 'new' ? 'Build index' : project.state === 'error' ? 'Try again' : 'Reindex'}</button></div></div>
    <label className="pm-auto-update"><input type="checkbox" checked={Boolean(project.autoUpdate)} disabled={busy === 'auto'} onChange={event => onAutoUpdate(project, event.target.checked)}/><span>Keep this map up to date<small>Check for changes while Atlas is running.</small></span></label>
    {project.warnings?.length > 0 && <details className="pm-index-notes"><summary><CircleHelp size={12}/>Index notes <span>{project.warnings.length}</span></summary><ul>{project.warnings.map((warning, index) => <li key={index}>{sourceText(warning)}</li>)}</ul></details>}
  </article>;
}

function CapabilityEditor({ capability, index, onChange, onRemove }) {
  return <details className="pm-capability">
    <summary><span className="pm-capability-number">{String(index + 1).padStart(2, '0')}</span><span>{capability.name || 'Unnamed capability'}<small>{(capability.paths || []).filter(Boolean).length} assigned code areas</small></span><ChevronDown size={15}/></summary>
    <div className="pm-capability-fields"><label className="pm-field"><span>Capability name</span><input maxLength={160} value={capability.name || ''} onChange={event => onChange({ ...capability, name: event.target.value })}/></label><label className="pm-field"><span>What someone can do</span><textarea rows={3} maxLength={4000} value={capability.summary || ''} onChange={event => onChange({ ...capability, summary: event.target.value })}/></label><label className="pm-field"><span>Assigned paths</span><textarea className="pm-path-input" rows={4} spellCheck={false} value={(capability.paths || []).join('\n')} onChange={event => onChange({ ...capability, paths: event.target.value.split('\n') })}/><small>One path per line, relative to the repository.</small></label><DocumentationEditor group={capability} onChange={onChange}/><Evidence group={capability}/><button className="pm-text-button danger" onClick={onRemove}><Trash2 size={13}/>Remove capability from draft</button></div>
  </details>;
}

export function ProjectManager({ open, onClose, onProjectSelected, onProjectUpdated, activeProjectId, reviewProjectId }) {
  const [projects, setProjects] = useState([]);
  const [repositoryScope, setRepositoryScope] = useState(null);
  const [serverActive, setServerActive] = useState(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState({});
  const [adding, setAdding] = useState(false);
  const [repositoryPath, setRepositoryPath] = useState('');
  const [repositoryName, setRepositoryName] = useState('');
  const [addError, setAddError] = useState('');
  const [view, setView] = useState('projects');
  const [reviewId, setReviewId] = useState(null);
  const [draft, setDraft] = useState(null);
  const [savedMap, setSavedMap] = useState(null);
  const [suggestion, setSuggestion] = useState(null);
  const [usingSuggestion, setUsingSuggestion] = useState(false);
  const [selectedKey, setSelectedKey] = useState('');
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState('');
  const [saving, setSaving] = useState(false);
  const [mergeTarget, setMergeTarget] = useState('');
  const [confirmation, setConfirmation] = useState(null);
  const dialog = useRef(null);
  const confirmDialog = useRef(null);
  const firstInput = useRef(null);
  const callbacks = useRef({});
  const projectSnapshot = useRef([]);
  const reviewIdRef = useRef(null);
  const pendingSuggestions = useRef(new Set());
  const loadedSuggestions = useRef(new Map());
  const pollNow = useRef(null);
  const mapSequence = useRef(0);
  const closeRef = useRef(null);
  const restoreFocus = useRef(null);
  const requestedReview = useRef(null);
  callbacks.current = { onClose, onProjectSelected, onProjectUpdated };
  reviewIdRef.current = reviewId;
  const reviewProject = projects.find(project => project.id === reviewId);
  const groups = groupEntries(draft);
  const currentGroup = groups.find(group => groupKey(group.groupKind, group.id) === selectedKey);
  const dirty = Boolean(draft && savedMap && (usingSuggestion || JSON.stringify(draft) !== JSON.stringify(savedMap)));

  const updateProject = useCallback(project => {
    if (!project?.id) return;
    setProjects(current => {
      const next = current.some(item => item.id === project.id) ? current.map(item => item.id === project.id ? project : item) : [...current, project];
      projectSnapshot.current = next;
      return next;
    });
  }, []);

  const refresh = useCallback(async signal => {
    const data = await request('/api/projects', { signal });
    const next = data.projects || [];
    const previous = new Map(projectSnapshot.current.map(project => [project.id, project]));
    setProjects(next);
    setRepositoryScope(data.repositoryScope || null);
    projectSnapshot.current = next;
    setServerActive(data.activeProjectId || null);
    setListError('');
    setLoading(false);
    for (const project of next) {
      const before = previous.get(project.id);
      if (before && project.state === 'ready' && project.indexedAt && before.indexedAt !== project.indexedAt) callbacks.current.onProjectUpdated?.(project);
      const waitingForSuggestions = pendingSuggestions.current.has(project.id);
      const candidateVersion = project.suggestionUpdatedAt || 'candidate';
      const newCandidate = project.hasSuggestion && loadedSuggestions.current.get(project.id) !== candidateVersion;
      if ((waitingForSuggestions || newCandidate) && !inProgress(project)) {
        pendingSuggestions.current.delete(project.id);
        if (project.job?.state === 'error' && waitingForSuggestions) {
          if (reviewIdRef.current === project.id) setReviewError(typeof project.job.error === 'string' ? project.job.error : 'The new suggestions could not be prepared. Your saved map is still available.');
          continue;
        }
        if (reviewIdRef.current === project.id) {
          try {
            const candidate = await request(projectEndpoint(project.id, '/map'), { signal });
            if (reviewIdRef.current === project.id && candidate.suggestion) {
              setSuggestion(candidate.suggestion);
              loadedSuggestions.current.set(project.id, candidateVersion);
              setNotice('New suggestions are ready. Review them before saving any changes.');
            }
          } catch (error) { if (error.name !== 'AbortError') setReviewError(error.message); }
        }
      }
    }
    return next;
  }, []);

  useEffect(() => {
    if (!open) return;
    let stopped = false;
    let timer;
    let controller;
    let generation = 0;
    const tick = async () => {
      const currentGeneration = ++generation;
      clearTimeout(timer);
      controller?.abort();
      controller = new AbortController();
      let next = projectSnapshot.current;
      try { next = await refresh(controller.signal); }
      catch (error) { if (error.name !== 'AbortError' && !stopped && generation === currentGeneration) { setListError(error.message); setLoading(false); } }
      if (!stopped && generation === currentGeneration) timer = setTimeout(tick, next.some(inProgress) || pendingSuggestions.current.size ? 2000 : 15000);
    };
    pollNow.current = tick;
    tick();
    return () => { stopped = true; generation++; clearTimeout(timer); controller?.abort(); pollNow.current = null; };
  }, [open, refresh]);

  useEffect(() => {
    if (!open) { requestedReview.current = null; return; }
    if (!reviewProjectId || requestedReview.current === reviewProjectId) return;
    const project = projects.find(item => item.id === reviewProjectId);
    if (!project) return;
    requestedReview.current = reviewProjectId;
    openReview(project);
  }, [open, reviewProjectId, projects]);

  useEffect(() => {
    if (!open) {
      mapSequence.current++;
      setView('projects'); setReviewId(null); setDraft(null); setSavedMap(null); setSuggestion(null); setUsingSuggestion(false); setConfirmation(null); setReviewError('');
      return;
    }
    restoreFocus.current = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const frame = requestAnimationFrame(() => dialog.current?.focus());
    return () => {
      cancelAnimationFrame(frame);
      document.body.style.overflow = previousOverflow;
      if (restoreFocus.current?.isConnected) restoreFocus.current.focus();
    };
  }, [open]);

  useEffect(() => {
    if (!dirty || !open) return;
    const guard = event => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty, open]);

  useEffect(() => {
    if (confirmation) requestAnimationFrame(() => confirmDialog.current?.querySelector('button')?.focus());
  }, [confirmation]);

  function leaveSafely(action, message = 'Your map has changes that have not been saved. Keep editing, or discard those changes and continue.') {
    if (saving) return;
    if (dirty) setConfirmation({ message, action });
    else action();
  }
  closeRef.current = () => leaveSafely(() => callbacks.current.onClose?.());

  function backToProjects() {
    leaveSafely(() => {
      mapSequence.current++;
      setView('projects'); setReviewId(null); setDraft(null); setSavedMap(null); setSuggestion(null); setUsingSuggestion(false); setReviewError(''); setNotice('');
    });
  }

  async function actionFor(project, action, operation) {
    setBusy(current => ({ ...current, [project.id]: action }));
    setListError(''); setNotice('');
    try { return await operation(); }
    catch (error) { setListError(`${project.name}: ${error.message}`); return null; }
    finally { setBusy(current => { const next = { ...current }; delete next[project.id]; return next; }); }
  }

  async function addRepository(event) {
    event.preventDefault();
    if (!repositoryPath.trim()) { setAddError('Enter the local folder that contains your repository.'); firstInput.current?.focus(); return; }
    setAdding(true); setAddError(''); setNotice('');
    try {
      const result = await request('/api/projects', { method: 'POST', body: { path: repositoryPath.trim(), ...(repositoryName.trim() ? { name: repositoryName.trim() } : {}) } });
      const project = result.project || result;
      updateProject(project);
      setRepositoryPath(''); setRepositoryName('');
      setNotice(`${project.name || 'Your repository'} has been added. Its progress will appear below.`);
      pollNow.current?.();
    } catch (error) { setAddError(error.message); }
    finally { setAdding(false); }
  }

  function openProject(project) {
    leaveSafely(() => actionFor(project, 'activate', async () => {
      const result = await request(projectEndpoint(project.id, '/activate'), { method: 'POST' });
      const active = result.project || project;
      updateProject(active); setServerActive(active.id);
      await callbacks.current.onProjectSelected?.(active);
      callbacks.current.onClose?.();
    }));
  }

  function indexProject(project) {
    return actionFor(project, 'index', async () => {
      const result = await request(projectEndpoint(project.id, '/index'), { method: 'POST' });
      updateProject(result.project || result);
      setNotice(`${project.name} is being indexed. You can leave this window open to follow its progress.`);
      pollNow.current?.();
    });
  }

  function checkProject(project) {
    return actionFor(project, 'check', async () => {
      const result = await request(projectEndpoint(project.id, '/changes'));
      updateProject(result.project || { ...project, changes: result.changes || result });
      setNotice(result.checking ? 'Checking the repository for changes. The result will appear here when it is ready.' : result.project?.changes ? changeLabel(result.project.changes) : result.changes ? changeLabel(result.changes) : 'The repository check is complete.');
      pollNow.current?.();
    });
  }

  function setAutoUpdate(project, enabled) {
    return actionFor(project, 'auto', async () => {
      const result = await request(projectEndpoint(project.id), { method: 'PATCH', body: { autoUpdate: enabled } });
      updateProject(result.project || { ...project, autoUpdate: enabled });
    });
  }

  function openReview(project) {
    leaveSafely(async () => {
      const sequence = ++mapSequence.current;
      setView('review'); setReviewId(project.id); reviewIdRef.current = project.id;
      setDraft(null); setSavedMap(null); setSuggestion(null); setUsingSuggestion(false); setReviewLoading(true); setReviewError(''); setNotice(''); setMergeTarget('');
      try {
        const result = await request(projectEndpoint(project.id, '/map'));
        if (sequence !== mapSequence.current) return;
        updateProject(result.project);
        if (!result.map) {
          setReviewError('The first map is still being built. Return to repositories to follow its progress.');
          return;
        }
        const map = result.map;
        setDraft(clone(map)); setSavedMap(clone(map)); setSuggestion(result.suggestion || null);
        if (result.suggestion) loadedSuggestions.current.set(project.id, result.project?.suggestionUpdatedAt || 'candidate');
        const first = groupEntries(map)[0];
        setSelectedKey(first ? groupKey(first.groupKind, first.id) : '');
      } catch (error) { if (sequence === mapSequence.current) setReviewError(error.message); }
      finally { if (sequence === mapSequence.current) setReviewLoading(false); }
    });
  }

  function editGroup(group, updates) {
    setDraft(current => ({ ...current, [group.groupKind]: current[group.groupKind].map(item => item.id === group.id ? { ...item, ...updates } : item) }));
  }

  function addGroup(kind) {
    const group = blankGroup(kind);
    setDraft(current => ({ ...current, [kind]: [...(current[kind] || []), group] }));
    setSelectedKey(groupKey(kind, group.id)); setMergeTarget('');
  }

  function removeGroup(group) {
    const next = { ...draft, [group.groupKind]: draft[group.groupKind].filter(item => item.id !== group.id) };
    setDraft(next);
    const first = groupEntries(next)[0];
    setSelectedKey(first ? groupKey(first.groupKind, first.id) : '');
    setNotice(`${group.name || 'The group'} was removed from the draft. Its code stays in the repository.`);
  }

  function moveGroup(group, kind) {
    if (kind === group.groupKind) return;
    if (kind === 'foundations' && group.capabilities?.length) {
      setConfirmation({
        title: 'Move into foundations?',
        message: 'Foundations hold shared code areas. This will combine the capability paths and source evidence into the foundation, removing the separate capability names. Your repository files stay in place.',
        confirmLabel: 'Move to foundations', cancelLabel: 'Keep as feature',
        action: () => applyGroupMove(group, kind),
      });
      return;
    }
    applyGroupMove(group, kind);
  }

  function applyGroupMove(group, kind) {
    const { groupKind, ...value } = group;
    const moved = kind === 'foundations' ? foldCapabilities(value) : value;
    const usedIds = new Set((draft[kind] || []).map(item => item.id));
    if (usedIds.has(moved.id)) moved.id = makeId(kind === 'features' ? 'feature' : 'foundation');
    if (kind === 'features' && !moved.capabilities) moved.capabilities = [];
    setDraft({ ...draft, [groupKind]: draft[groupKind].filter(item => item.id !== group.id), [kind]: [...(draft[kind] || []), moved] });
    setSelectedKey(groupKey(kind, moved.id)); setMergeTarget('');
  }

  function mergeGroup() {
    const target = groups.find(group => groupKey(group.groupKind, group.id) === mergeTarget);
    if (!target || !currentGroup) return;
    if (target.groupKind === 'foundations' && currentGroup.capabilities?.length) {
      setConfirmation({
        title: 'Combine into a foundation?',
        message: `This moves the paths and source evidence into ${target.name}. Its shared code will no longer have separate capability names. The destination keeps its name and description.`,
        confirmLabel: 'Combine groups', cancelLabel: 'Keep separate',
        action: () => applyGroupMerge(target),
      });
      return;
    }
    applyGroupMerge(target);
  }

  function applyGroupMerge(target) {
    const next = clone(draft);
    next[currentGroup.groupKind] = next[currentGroup.groupKind].filter(item => item.id !== currentGroup.id);
    const destination = next[target.groupKind].find(item => item.id === target.id);
    destination.paths = unique([...(destination.paths || []), ...(currentGroup.paths || [])]);
    destination.evidence = [...(destination.evidence || []), ...(currentGroup.evidence || [])].filter((value, index, values) => values.findIndex(other => JSON.stringify(other) === JSON.stringify(value)) === index);
    const capabilities = [...(destination.capabilities || [])];
    const ids = new Set(capabilities.map(item => item.id));
    for (const capability of currentGroup.capabilities || []) {
      const copy = clone(capability);
      if (ids.has(copy.id)) copy.id = makeId('capability');
      ids.add(copy.id); capabilities.push(copy);
    }
    if (capabilities.length) destination.capabilities = capabilities;
    if (target.groupKind === 'foundations') {
      const folded = foldCapabilities(destination);
      next.foundations = next.foundations.map(group => group.id === target.id ? folded : group);
    }
    setDraft(next); setSelectedKey(groupKey(target.groupKind, target.id)); setMergeTarget('');
    setNotice(`Moved ${currentGroup.name}'s paths and source evidence into ${target.name}. Review the description, then save.`);
  }

  async function generateSuggestions() {
    if (!reviewProject) return;
    setBusy(current => ({ ...current, [reviewProject.id]: 'suggestions' })); setReviewError('');
    try {
      const result = await request(projectEndpoint(reviewProject.id, '/suggestions'), { method: 'POST' });
      updateProject(result.project);
      pendingSuggestions.current.add(reviewProject.id);
      if (result.suggestion) setSuggestion(result.suggestion);
      setNotice('Atlas is looking for useful groups. Your saved map and this draft stay in place.');
      pollNow.current?.();
    } catch (error) { setReviewError(error.message); }
    finally { setBusy(current => { const next = { ...current }; delete next[reviewProject.id]; return next; }); }
  }

  function reviewSuggestion() {
    if (!suggestion) return;
    leaveSafely(() => {
      const candidate = suggestion.map || suggestion;
      setDraft(clone(candidate)); setUsingSuggestion(true); setReviewError('');
      const first = groupEntries(candidate)[0];
      setSelectedKey(first ? groupKey(first.groupKind, first.id) : '');
      setNotice('You are reviewing a new draft. Nothing replaces the saved map until you choose Save reviewed map.');
    }, 'Loading the new suggestions will replace your unsaved draft. Keep editing, or discard the draft and review the suggestions.');
  }

  async function saveMap() {
    if (!draft || !reviewProject || saving) return;
    const map = normalizeMap(draft);
    const unnamed = groupEntries(map).find(group => !group.name || (group.capabilities || []).some(capability => !capability.name));
    if (unnamed) { setReviewError('Give every group and capability a name before saving.'); setSelectedKey(groupKey(unnamed.groupKind, unnamed.id)); return; }
    const undescribed = groupEntries(map).find(group => !group.summary || (group.capabilities || []).some(capability => !capability.summary));
    if (undescribed) { setReviewError(`Add a short description for ${undescribed.name} and each of its capabilities before saving.`); setSelectedKey(groupKey(undescribed.groupKind, undescribed.id)); return; }
    setSaving(true); setReviewError(''); setNotice('');
    try {
      const result = await request(projectEndpoint(reviewId, '/map'), { method: 'PUT', body: { map } });
      const saved = result.map || map;
      setDraft(clone(saved)); setSavedMap(clone(saved)); setUsingSuggestion(false); setSuggestion(result.suggestion || null);
      const updated = result.project || { ...reviewProject, mapStatus: 'reviewed' };
      updateProject(updated);
      setNotice('Your reviewed map is saved.');
      await callbacks.current.onProjectUpdated?.(updated);
      pollNow.current?.();
    } catch (error) { setReviewError(error.message); }
    finally { setSaving(false); }
  }

  function handleKeys(event) {
    event.stopPropagation();
    if (event.key === 'Escape') {
      event.preventDefault();
      if (confirmation) { setConfirmation(null); requestAnimationFrame(() => dialog.current?.focus()); }
      else closeRef.current?.();
      return;
    }
    if (event.key !== 'Tab') return;
    const scope = confirmation ? confirmDialog.current : dialog.current;
    const focusable = [...(scope?.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, [tabindex="0"]') || [])].filter(element => element.getClientRects().length);
    if (!focusable.length) { event.preventDefault(); scope?.focus(); return; }
    const first = focusable[0]; const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !scope.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (document.activeElement === last || document.activeElement === scope || !scope.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
  }

  if (!open) return null;
  return <div className="pm-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) closeRef.current?.(); }}>
    <section ref={dialog} className={`pm-dialog ${view === 'review' ? 'pm-review-dialog' : ''}`} role="dialog" aria-modal="true" aria-labelledby="pm-title" tabIndex={-1} onKeyDown={handleKeys}>
      <header className="pm-header"><div className="pm-header-brand"><span className="pm-brand-orbit"><Orbit size={22}/></span><div><span className="pm-kicker">YOUR OBSERVATORY</span><h2 id="pm-title">{view === 'review' ? `Review ${reviewProject?.name || 'the product map'}` : 'A home for your codebases.'}</h2></div></div><button className="pm-icon-button" onClick={() => closeRef.current?.()} disabled={saving} aria-label="Close project manager"><X size={21}/></button></header>
      {view === 'projects' ? <div className="pm-projects-body">
        {repositoryScope ? <aside className="pm-add-panel"><div className="pm-add-orbit"><Orbit size={40}/><span/><i/></div><h3>Your repository workspace.</h3><p>This Atlas view is connected to one checkout.</p><p className="pm-path-input">{repositoryScope}</p><div className="pm-local-note"><Code2 size={16}/><p>Indexes and maps are saved in this checkout’s .atlas folder.</p></div></aside> : <aside className="pm-add-panel"><div className="pm-add-orbit"><Orbit size={40}/><span/><i/></div><h3>Bring a codebase into view.</h3><p>Add a local repository. Atlas will read its structure, build a graph, and suggest a first product map for you to review.</p><form onSubmit={addRepository}><label className="pm-field"><span>Repository folder</span><input ref={firstInput} value={repositoryPath} onChange={event => setRepositoryPath(event.target.value)} placeholder="/Users/you/workspace/my-project" autoComplete="off" spellCheck={false} required/><small>Use the full path to a folder on this machine.</small></label><label className="pm-field"><span>Project name <em>optional</em></span><input maxLength={100} value={repositoryName} onChange={event => setRepositoryName(event.target.value)} placeholder="The folder name works, too" autoComplete="off"/></label>{addError && <p className="pm-form-error" role="alert">{addError}</p>}<button type="submit" className="pm-button primary pm-add-button" disabled={adding}>{adding ? <Loader2 size={16} className="pm-spin"/> : <FolderPlus size={16}/>} {adding ? 'Adding repository…' : 'Add repository'}<ArrowRight size={15}/></button></form><div className="pm-local-note"><Code2 size={16}/><p>Your code stays on this machine.<br/><span>Atlas keeps its own local index.</span></p></div></aside>}
        <main className="pm-project-library"><div className="pm-section-heading"><div><span className="pm-kicker">SAVED REPOSITORIES</span><h3>{projects.length ? `${projects.length} ${projects.length === 1 ? 'place' : 'places'} to explore` : 'Your first map starts here.'}</h3></div><button className="pm-icon-button" onClick={() => pollNow.current?.()} aria-label="Refresh repository list"><RefreshCw size={16}/></button></div>{listError && <div className="pm-message error" role="alert">{listError}</div>}{notice && <div className="pm-message" role="status"><Check size={14}/><span>{notice}</span></div>}{loading && !projects.length ? <div className="pm-library-empty"><Loader2 className="pm-spin" size={28}/><p>Opening your project library…</p></div> : !projects.length ? <div className="pm-library-empty"><div className="pm-empty-constellation"><span/><span/><span/><span/><Orbit size={56}/></div><h4>Start with a repository you know.</h4><p>Seeing familiar code as a map makes it easier to judge the first suggestions. You can rename, move, and combine groups before making the map your own.</p><div className="pm-first-steps"><span><b>01</b>Add a folder</span><ChevronRight size={12}/><span><b>02</b>Review its map</span><ChevronRight size={12}/><span><b>03</b>Explore</span></div></div> : <div className="pm-project-grid">{projects.map(project => <ProjectCard key={project.id} project={project} active={project.id === (activeProjectId || serverActive)} busy={busy[project.id]} onOpen={openProject} onReview={openReview} onIndex={indexProject} onCheck={checkProject} onAutoUpdate={setAutoUpdate}/>)}</div>}</main>
      </div> : <>
        <div className="pm-review-topbar"><button className="pm-text-button" onClick={backToProjects} disabled={saving}><ArrowLeft size={14}/>Repositories</button><span className="pm-review-project-path">{reviewProject?.path}</span><span className={`pm-draft-state ${dirty ? 'unsaved' : ''}`}>{dirty ? 'Unsaved draft' : reviewProject?.mapStatus === 'reviewed' ? 'Reviewed map' : 'First suggested map'}</span></div>
        {reviewLoading ? <div className="pm-review-loading"><Loader2 size={27} className="pm-spin"/><p>Opening the product map…</p></div> : !draft ? <div className="pm-review-loading"><p className="pm-inline-error" role="alert">{reviewError || 'There is no map to review yet. Build the project index first.'}</p><button className="pm-button secondary" onClick={backToProjects}><ArrowLeft size={14}/>Back to repositories</button></div> : <>
          <div className="pm-review-guidance"><Sparkles size={17}/><div><strong>Suggestions are a starting point.</strong><span>Name groups after what people do. Put shared support code in foundations. Every change stays in this draft until you save.</span></div><button className="pm-button secondary" disabled={saving || inProgress(reviewProject) || busy[reviewId] === 'suggestions'} onClick={generateSuggestions}>{inProgress(reviewProject) || busy[reviewId] === 'suggestions' ? <Loader2 size={13} className="pm-spin"/> : <RefreshCw size={13}/>}New suggestions</button></div>
          {suggestion && !usingSuggestion && <div className="pm-suggestion-banner"><span><Sparkles size={15}/>New suggestions are ready. Your saved map is unchanged.</span><button className="pm-text-button" onClick={reviewSuggestion} disabled={saving}>Review new suggestions<ArrowRight size={14}/></button></div>}
          {inProgress(reviewProject) && <div className="pm-review-job" role="status"><Loader2 size={13} className="pm-spin"/>{reviewProject.job?.message || 'Working through the repository…'}</div>}
          {reviewError && <div className="pm-message error pm-review-message" role="alert">{reviewError}</div>}{notice && <div className="pm-message pm-review-message" role="status"><Check size={14}/><span>{notice}</span></div>}
          <div className="pm-review-workspace"><nav className="pm-group-nav" aria-label="Map groups">{['features', 'foundations'].map(kind => <div className="pm-group-section" key={kind}><div className="pm-group-section-heading"><span>{kind === 'features' ? 'PRODUCT FEATURES' : 'SHARED FOUNDATIONS'}</span><span>{draft[kind]?.length || 0}</span></div>{(draft[kind] || []).map(group => <button key={group.id} className={`pm-group-link ${selectedKey === groupKey(kind, group.id) ? 'selected' : ''} ${kind}`} onClick={() => { setSelectedKey(groupKey(kind, group.id)); setMergeTarget(''); }}><span className="pm-group-dot"/><span>{group.name || 'Unnamed group'}<small>{(group.paths || []).filter(Boolean).length} assigned paths</small></span><ChevronRight size={12}/></button>)}<button className="pm-add-group" onClick={() => addGroup(kind)} disabled={saving}><Plus size={13}/>Add {kind === 'features' ? 'feature' : 'foundation'}</button></div>)}</nav>
            <div className="pm-group-editor">{currentGroup ? <><div className="pm-editor-heading"><div><span className="pm-kicker">{currentGroup.groupKind === 'features' ? 'WHAT PEOPLE CAN DO' : 'WHAT SUPPORTS THE PRODUCT'}</span><h3>{currentGroup.name || 'Give this group a name'}</h3></div><label className="pm-kind-select"><span>Group type</span><select value={currentGroup.groupKind} onChange={event => moveGroup(currentGroup, event.target.value)} disabled={saving}><option value="features">Product feature</option><option value="foundations">Shared foundation</option></select></label></div>
              <fieldset disabled={saving} className="pm-editor-fields"><label className="pm-field"><span>Name</span><input maxLength={160} value={currentGroup.name || ''} onChange={event => editGroup(currentGroup, { name: event.target.value })}/></label><label className="pm-field"><span>What this part does</span><textarea rows={3} maxLength={4000} value={currentGroup.summary || ''} placeholder="Explain it to someone who has not read this code." onChange={event => editGroup(currentGroup, { summary: event.target.value })}/></label><label className="pm-field"><span>Assigned paths <b>{(currentGroup.paths || []).filter(Boolean).length}</b></span><textarea className="pm-path-input" rows={6} spellCheck={false} placeholder={'src/feature\nsrc/shared/helper.ts'} value={(currentGroup.paths || []).join('\n')} onChange={event => editGroup(currentGroup, { paths: event.target.value.split('\n') })}/><small>One file or folder per line, relative to this repository. Folder paths include the code inside them.</small></label><DocumentationEditor group={currentGroup} onChange={next => editGroup(currentGroup, next)}/><Evidence group={currentGroup}/>
              {(currentGroup.groupKind === 'features' || currentGroup.capabilities?.length > 0) && <section className="pm-capabilities-section"><div className="pm-subsection-heading"><div><h4>Capabilities</h4><p>The smaller things someone can do within this group.</p></div><button className="pm-text-button" onClick={() => editGroup(currentGroup, { capabilities: [...(currentGroup.capabilities || []), blankCapability()] })}><Plus size={13}/>Add capability</button></div>{(currentGroup.capabilities || []).map((capability, index) => <CapabilityEditor key={capability.id} capability={capability} index={index} onChange={updated => editGroup(currentGroup, { capabilities: currentGroup.capabilities.map((item, itemIndex) => itemIndex === index ? updated : item) })} onRemove={() => editGroup(currentGroup, { capabilities: currentGroup.capabilities.filter((_, itemIndex) => itemIndex !== index) })}/>)}{!currentGroup.capabilities?.length && <p className="pm-empty-capabilities">No capabilities yet. Add them when this feature needs a closer view.</p>}</section>}
              <div className="pm-group-tools"><div><h4>Combine with another group</h4><p>Move these paths and capabilities into an existing group. The destination keeps its name and description.</p><div className="pm-merge-controls"><select aria-label="Merge destination" value={mergeTarget} onChange={event => setMergeTarget(event.target.value)}><option value="">Choose a destination…</option>{groups.filter(group => groupKey(group.groupKind, group.id) !== selectedKey).map(group => <option key={groupKey(group.groupKind, group.id)} value={groupKey(group.groupKind, group.id)}>{group.name} · {group.groupKind === 'features' ? 'feature' : 'foundation'}</option>)}</select><button className="pm-button secondary" disabled={!mergeTarget} onClick={mergeGroup}>Merge group</button></div></div><button className="pm-text-button danger" onClick={() => removeGroup(currentGroup)}><Trash2 size={13}/>Remove this group from the draft</button></div></fieldset>
            </> : <div className="pm-no-group"><Layers3 size={34}/><h3>Start with one useful group.</h3><p>A feature describes something people can do. A foundation describes code that supports several features.</p><button className="pm-button primary" onClick={() => addGroup('features')}><Plus size={15}/>Add a feature</button></div>}</div>
          </div>
          <footer className="pm-review-footer"><div><span className={`pm-save-dot ${dirty ? 'unsaved' : ''}`}/><span>{dirty ? 'Your changes are only in this draft.' : 'Review the groups, then save when they make sense.'}</span></div><button className="pm-button primary" onClick={saveMap} disabled={saving}>{saving ? <Loader2 size={15} className="pm-spin"/> : <Save size={15}/>} {saving ? 'Saving map…' : 'Save reviewed map'}</button></footer>
        </>}
      </>}
      {confirmation && <div className="pm-confirm-backdrop"><div ref={confirmDialog} className="pm-confirm" role="alertdialog" aria-modal="true" aria-labelledby="pm-confirm-title" aria-describedby="pm-confirm-copy" tabIndex={-1}><span className="pm-kicker">{confirmation.title ? "BEFORE YOU CONTINUE" : "BEFORE YOU LEAVE"}</span><h3 id="pm-confirm-title">{confirmation.title || "Keep your changes?"}</h3><p id="pm-confirm-copy">{confirmation.message}</p><div><button className="pm-button primary" onClick={() => { setConfirmation(null); requestAnimationFrame(() => dialog.current?.focus()); }}>{confirmation.cancelLabel || "Keep editing"}</button><button className="pm-button secondary" onClick={() => { const action = confirmation.action; setConfirmation(null); action(); requestAnimationFrame(() => dialog.current?.focus()); }}>{confirmation.confirmLabel || "Discard and continue"}</button></div></div></div>}
    </section>
  </div>;
}

export default ProjectManager;
