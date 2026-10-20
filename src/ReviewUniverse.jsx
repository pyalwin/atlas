import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Focus, List, Minus, Pause, Play, Plus, Search, X } from 'lucide-react';
import { Universe, buildReviewCallGraph } from './Universe.js';
import './review-universe.css';

const leaf = path => path?.split('/').pop() || '';
const isTest = path => /^(tests?|__tests__)\//i.test(path || '') || /\/(tests|__tests__)\//i.test(path || '') || /(^|\/)test_[^/]+|[._](test|spec)\.[^/]+$/i.test(path || '');

// The map contains the reviewed snapshot's actual reference graph. Add back
// changed files omitted by a graph cap, without inventing any connections.
function reviewMap(review, guide) {
  const changedByPath = new Map((review.files || []).map(file => [file.path, file]));
  const nodes = new Map((review.graph?.nodes || []).map(node => {
    const file = changedByPath.get(node.path);
    return [node.id, { ...node, kind: 'file', reviewNode: true, reviewChanged: Boolean(file), reviewTest: isTest(node.path), status: file?.status || node.status, name: leaf(node.path) || node.name }];
  }));
  for (const file of review.files || []) {
    const existing = [...nodes.values()].find(node => node.path === file.path);
    if (!existing) {
      const id = `file:${file.path}`;
      nodes.set(id, { ...file, id, name: leaf(file.path), kind: 'file', reviewNode: true, reviewChanged: true, reviewTest: isTest(file.path), metrics: {} });
    }
  }
  const calls = buildReviewCallGraph(guide);
  return { layer: 'review', nodes: [...nodes.values(), ...calls.nodes], edges: [...(review.graph?.edges || []).filter(edge => nodes.has(edge.source) && nodes.has(edge.target)), ...calls.edges] };
}

export function ReviewUniverse({ review, guide, selectedPath, reviewed = new Set(), step = 'brief', revision = 'changes', compact = false, onSelectFile, onSelectContext, onSelectEdge }) {
  const container = useRef(null), scene = useRef(null), callbacks = useRef({}), lastSelected = useRef(null), loadedModel = useRef(null);
  const [error, setError] = useState(''), [hovered, setHovered] = useState(null), [navigatorOpen, setNavigatorOpen] = useState(false), [query, setQuery] = useState('');
  const [motion, setMotion] = useState(() => !window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const model = useMemo(() => reviewMap(review, guide), [review, guide]);
  const testPaths = useMemo(() => new Set((review.files || []).filter(file => {
    const explanation = guide?.files?.find(item => item.path === file.path);
    return explanation ? explanation.role === 'tests' : isTest(file.path);
  }).map(file => file.path)), [review, guide]);
  const byPath = useMemo(() => new Map(model.nodes.filter(node => !node.reviewCall).map(node => [node.path, node])), [model]);
  const callMode = step === 'impact' && model.nodes.some(node => node.reviewCall && !testPaths.has(node.path));
  const selectedNode = callMode ? model.nodes.find(node => node.reviewCall && node.reviewAnchor && (node.path === selectedPath || review.files?.some(file => file.path === selectedPath && file.oldPath === node.path))) || model.nodes.find(node => node.reviewCall && node.reviewAnchor) : testPaths.has(selectedPath) ? null : byPath.get(selectedPath);
  const selectedId = selectedNode?.id || null;
  const changed = model.nodes.filter(node => node.reviewChanged && !node.reviewCall);
  const implementation = changed.filter(node => !testPaths.has(node.path));
  const testOnly = changed.length > 0 && implementation.length === 0;
  const shown = node => !testOnly && Boolean(node.reviewCall) === callMode && !(node.reviewChanged ? testPaths.has(node.path) : node.reviewTest);
  const visibleFiles = model.nodes.filter(node => !node.reviewCall && node.path?.toLowerCase().includes(query.toLowerCase())).sort((a, b) => Number(b.reviewChanged) - Number(a.reviewChanged) || a.path.localeCompare(b.path));
  callbacks.current = { onSelectFile, onSelectContext, onSelectEdge, revision, review };

  function openCallEvidence(item) {
    const preferred = callbacks.current.revision === 'base' ? 'base' : 'head';
    const evidence = item.evidence?.find(ref => ref.side === preferred) || item.evidence?.[0];
    if (!evidence) return;
    const file = callbacks.current.review.files?.find(candidate => candidate.path === evidence.path || candidate.oldPath === evidence.path);
    if (file) callbacks.current.onSelectFile?.(file.path, evidence);
  }

  function activate(node) {
    if (node.reviewCall) openCallEvidence(node);
    else if (node.reviewChanged) callbacks.current.onSelectFile?.(node.path);
    else {
      scene.current?.select(node.id);
      callbacks.current.onSelectContext?.(node);
    }
    setNavigatorOpen(false);
  }
  useEffect(() => {
    if (!container.current) return;
    setError('');
    const instance = new Universe(container.current, {
      onSelect: activate,
      onSelectEdge: edge => edge.reviewCall ? openCallEvidence(edge) : callbacks.current.onSelectEdge?.(edge),
      onHover: setHovered,
      onError: () => setError('The 3D view is unavailable. Use the file picker and evidence panel to continue reviewing.'),
    });
    scene.current = instance;
    loadedModel.current = null;
    return () => { instance.dispose(); if (scene.current === instance) scene.current = null; loadedModel.current = null; };
  }, [review.id, review.baseSha, review.headSha]);
  useEffect(() => {
    if (!scene.current || loadedModel.current === model) return;
    scene.current.setData({ ...model, preserveCamera: Boolean(loadedModel.current) }, selectedId);
    scene.current.setReviewState({ revision, reviewed, selectedId, step, testPaths });
    loadedModel.current = model;
    lastSelected.current = selectedId;
  }, [model]);
  useEffect(() => {
    scene.current?.setReviewState({ revision, reviewed: reviewed instanceof Set ? reviewed : new Set(reviewed), selectedId, step, testPaths });
  }, [revision, reviewed, selectedId, model, step, testPaths]);
  useEffect(() => {
    if (selectedId && lastSelected.current && selectedId !== lastSelected.current) scene.current?.focusReview(selectedId);
    lastSelected.current = selectedId;
  }, [selectedId]);
  useEffect(() => { scene.current?.setMotion(motion && !compact); }, [motion, model, compact]);

  const candidate = hovered || selectedNode;
  const current = candidate && shown(candidate) ? candidate : null;
  const visibleIds = new Set(model.nodes.filter(shown).map(node => node.id));
  const connections = current ? model.edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target) && (edge.source === current.id || edge.target === current.id) && (revision === 'changes' || (revision === 'base' ? edge.change !== 'added' : edge.change !== 'removed'))) : [];
  const label = node => `${node.reviewCall ? `${node.name} in ` : ''}${node.path} · ${node.reviewCall ? node.reviewAnchor ? 'definition' : `${node.status || 'sampled'} call expression` : node.reviewChanged ? node.status : 'connected file'}${reviewed.has?.(node.path) ? ' · reviewed' : ''}`;

  return <div className="ru-universe" data-step={step} data-revision={revision} aria-label="Pull request universe">
    <div ref={container} className="ru-universe-viewport"/>
    {testOnly && !error && <div className="ru-map-empty"><strong>This change contains tests only.</strong><span>Review the test diffs in the evidence panel.</span></div>}
    <div className="ru-map-tools" aria-label="Universe controls">
      <button type="button" aria-label="Find a file in the universe" aria-expanded={navigatorOpen} onClick={() => setNavigatorOpen(open => !open)} title="Find a file"><List size={16}/></button>
      <span/>
      <button type="button" aria-label="Zoom in on universe" onClick={() => scene.current?.zoom(1)} title="Zoom in"><Plus size={15}/></button>
      <button type="button" aria-label="Zoom out of universe" onClick={() => scene.current?.zoom(-1)} title="Zoom out"><Minus size={15}/></button>
      <button type="button" aria-label="Fit universe to view" onClick={() => scene.current?.reset()} title="Fit the constellation"><Focus size={16}/></button>
      <button type="button" aria-label={motion ? 'Pause universe motion' : 'Resume universe motion'} aria-pressed={!motion} onClick={() => setMotion(value => !value)} title={motion ? 'Pause motion' : 'Resume motion'}>{motion ? <Pause size={13}/> : <Play size={13}/>}</button>
    </div>
    {navigatorOpen && <section className="ru-map-navigator" aria-label="Universe file navigator">
      <div className="ru-map-find"><Search size={15}/><input aria-label="Find a changed or connected file" value={query} onChange={event => setQuery(event.target.value)} placeholder="Find a file…" autoFocus/><button type="button" aria-label="Close file navigator" onClick={() => setNavigatorOpen(false)}><X size={15}/></button></div>
      <div>{visibleFiles.slice(0, 120).map(node => <button type="button" key={node.id} onClick={() => activate(node)} aria-label={label(node)} aria-pressed={node.path === selectedPath}><i className={node.reviewChanged ? node.reviewTest ? 'test' : 'changed' : 'context'}/><span>{node.path}<small>{node.reviewChanged ? `${node.status}${reviewed.has?.(node.path) ? ' · reviewed' : ''}` : 'connected file'}</small></span></button>)}</div>
      {!visibleFiles.length && <p>No files match.</p>}
      {visibleFiles.length > 120 && <p>Showing 120 of {visibleFiles.length}. Refine your search.</p>}
    </section>}
    {error && <div className="ru-map-fallback" role="status"><p>{error}</p>{model.nodes.filter(node => shown(node) && (node.reviewCall || node.reviewChanged)).map(node => <button type="button" key={node.id} onClick={() => activate(node)} aria-label={label(node)}><i className={node.reviewTest ? 'test' : 'changed'}/><span>{node.reviewCall ? node.name : node.path}<small>{node.status}{reviewed.has?.(node.path) ? ' · reviewed' : ''}</small></span></button>)}</div>}
    <div className="ru-map-bottom">
      <div className="ru-map-readout" aria-live="polite">{current ? <><strong>{current.reviewCall ? current.name : current.path}</strong><span>{current.reviewCall ? `${current.path}${current.evidence?.some(ref => ref.side === (revision === 'base' ? 'base' : 'head')) ? '' : ` · ${current.evidence?.[0]?.side || 'available'} source only`}` : current.reviewChanged ? 'In this PR' : 'Connected file'}{connections.length ? ` · ${connections.length} ${callMode ? 'sampled call' : 'indexed reference'}${connections.length === 1 ? '' : 's'}` : ''}</span></> : <><strong>Follow a connection.</strong><span>Select a file to see its place in the change.</span></>}</div>
      {revision !== 'changes' && <span className="ru-map-hint">{`${revision === 'base' ? 'Before' : 'After'} · ${(revision === 'base' ? review.baseSha : review.headSha)?.slice(0, 8)}`}</span>}
      <div className="ru-map-legend" aria-label="Universe legend"><span><i className="changed"/>{callMode ? 'Definition' : 'Changed'}</span><span><i className="context"/>{callMode ? 'Call expression' : 'Context'}</span>{revision === 'changes' && <><span><i className="added-line"/>Added {callMode ? 'call' : 'link'}</span><span><i className="removed-line"/>Removed {callMode ? 'call' : 'link'}</span></>}</div>
      {callMode && <span className="ru-map-hint">Sampled call expressions · not runtime flow</span>}
      <span className="ru-map-hint">Drag to orbit · Scroll to zoom · Select a connection for evidence</span>
      {(review.truncated?.nodes || review.truncated?.edges) && <span className="ru-map-hint">Connections are sampled. All {changed.length} changed files remain available.</span>}
    </div>
  </div>;
}

export default ReviewUniverse;
