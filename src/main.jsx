import { ProductOverview } from './ProductOverview.jsx';
import { FeatureDocumentation } from './FeatureDocumentation.jsx';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ArrowDownLeft, ArrowLeft, ArrowRight, ArrowUpRight, Braces, Check, ChevronRight, CircleHelp, Code2, Compass, Copy, FileCode2, Folder, GitBranch, Globe2, Layers3, Link2, List, Loader2, Maximize2, Minimize2, Minus, Orbit, PanelLeft, PanelRight, Pause, Play, Plus, Search, Sparkles, Sun, Moon, X } from 'lucide-react';
import { Universe } from './Universe.js';
import { ProjectManager } from './ProjectManager.jsx';
import { PRReview } from './PRReview.jsx';
import './styles.css';

const number = value => new Intl.NumberFormat('en').format(value || 0);
const compact = value => new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value || 0);
const isProduct = node => ['feature', 'capability', 'foundation', 'product-universe'].includes(node?.kind);
const productId = id => id === 'products' || /^(product|capability):/.test(id || '');
const title = node => isProduct(node) ? node.name : (node?.name || 'Repository').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
const kindName = node => ({ 'product-universe': 'Product', feature: 'Feature', capability: 'Capability', foundation: 'Foundation', repository: 'Repository', directory: 'Module', file: 'File', class: 'Class', function: 'Function' }[node?.kind] || 'Space');
const colorFor = node => isProduct(node) ? (node.kind === 'foundation' ? '#a69aef' : '#79dfc7') : ({ product: '#79dfc7', foundation: '#a69aef', operations: '#dfb77a', research: '#8eafdf' }[node?.category] || ({ file: '#79dfc7', class: '#a69aef', function: '#dfb77a' }[node?.kind] || '#8eafdf'));
const canEnter = node => (node?.metrics?.children || 0) > 0;
const featureCount = node => {
  const count = node?.metrics?.children || 0;
  const unit = node?.metrics?.capabilities ? (count === 1 ? 'capability' : 'capabilities') : (count === 1 ? 'code area' : 'code areas');
  return `${count} ${unit}`;
};
const breadcrumbsFor = graph => graph?.breadcrumbs || [];
function NodeIcon({ node, ...props }) {
  const Icon = node?.kind === 'foundation' ? Layers3 : isProduct(node) ? Orbit : node?.kind === 'file' ? FileCode2 : ['class', 'function'].includes(node?.kind) ? Braces : node?.kind === 'repository' ? Globe2 : Folder;
  return <Icon {...props}/>;
}
function foundationGraph(data, repositoryName) {
  return {
    ...data, layer: 'technical', scope: 'foundation-code',
    breadcrumbs: [{ id: 'root', name: repositoryName, kind: 'repository' }, data.parent],
  };
}
function groupRelationships(items = []) {
  const grouped = new Map();
  for (const item of items) {
    const previous = grouped.get(item.node.id);
    if (previous) { previous.weight += item.weight || 1; previous.kind += `, ${item.kind}`; previous.evidence.push(...(item.evidence || [])); }
    else grouped.set(item.node.id, { ...item, weight: item.weight || 1, evidence: [...(item.evidence || [])] });
  }
  return [...grouped.values()].sort((a, b) => b.weight - a.weight);
}
async function request(path, signal, projectId) {
  const url = new URL(`/api/${path}`, location.origin);
  if (projectId) url.searchParams.set('project', projectId);
  if (path.startsWith('product/') && new URLSearchParams(location.search).get('map') === 'candidate') url.searchParams.set('map', 'candidate');
  const response = await fetch(url, { signal });
  if (!response.ok) { let message; try { message = (await response.json()).error; } catch {} throw new Error(message || `The local index returned ${response.status}.`); }
  return response.json();
}

function App() {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme || 'dark');
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'light' ? '#f4f7f9' : '#080d12');
    try { localStorage.setItem('atlas-theme', theme); } catch { /* Theme still works when storage is unavailable. */ }
  }, [theme]);
  const [status, setStatus] = useState(null);
  const [activeProject, setActiveProject] = useState(null);
  const [projectsOpen, setProjectsOpen] = useState(false);
  const [projectView, setProjectView] = useState(null);
  const activeProjectRef = useRef(null), projectEpoch = useRef(0), initialization = useRef(0), observedIndex = useRef(null);
  async function api(path, signal) {
    return request(path, signal, activeProjectRef.current?.id);
  }
  const [graph, setGraph] = useState(null);
  const [catalog, setCatalog] = useState(null);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [mode, setMode] = useState(new URLSearchParams(location.search).get('view') === 'technical' || /^(dir|foundation):/.test(new URLSearchParams(location.search).get('space') || '') ? 'technical' : 'product');
  const [reviewOpen, setReviewOpen] = useState(new URLSearchParams(location.search).get('view') === 'review');
  const [returnFeature, setReturnFeature] = useState(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [view, setView] = useState(new URLSearchParams(location.search).has('space') || new URLSearchParams(location.search).get('view') === 'technical' ? 'map' : 'overview');
  const [renderStats, setRenderStats] = useState(null);
  const [showEdges, setShowEdges] = useState(true);
  const [motion, setMotion] = useState(!window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [source, setSource] = useState(null);
  const [proof, setProof] = useState(null);
  const [help, setHelp] = useState(false);
  const [copied, setCopied] = useState(false);
  const [linkKind, setLinkKind] = useState('all');
  const [notice, setNotice] = useState('');
  const [recent, setRecent] = useState([]);
  const [sceneError, setSceneError] = useState('');
  const [compactWindow, setCompactWindow] = useState(window.innerWidth <= 1100);
  const [sidebarOpen, setSidebarOpen] = useState(window.innerWidth > 1100);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(window.innerWidth <= 1100);
  const [mapExpanded, setMapExpanded] = useState(false);
  const panelsOverlaid = compactWindow || mapExpanded;
  const container = useRef(null), scene = useRef(null), searchInput = useRef(null);
  const navigation = useRef(0), sourceSequence = useRef(0), actions = useRef({}), pendingDestination = useRef(null);

  async function navigate(id = 'products', selection = null, replace = false) {
    if (!activeProjectRef.current) { setProjectsOpen(true); setLoading(false); return; }
    if (id === 'review') {
      ++navigation.current; pendingDestination.current = null;
      ++sourceSequence.current; setSource(null); setProof(null); setHelp(false);
      setReviewOpen(true); setLoading(false); setQuery(''); setError('');
      const url = `/?${new URLSearchParams({ project: activeProjectRef.current.id, view: 'review' })}`;
      if (replace) history.replaceState(null, '', url);
      else if (location.pathname + location.search !== url) history.pushState(null, '', url);
      return;
    }
    setReviewOpen(false);
    const destination = `${activeProjectRef.current.id}:${id}`;
    if (pendingDestination.current === destination) return;
    pendingDestination.current = destination;
    const sequence = ++navigation.current;
    const nextMode = productId(id) ? 'product' : 'technical';
    setMode(nextMode); setLoading(true); setError(''); setQuery(''); setProof(null);
    try {
      const data = id.startsWith('foundation:')
        ? foundationGraph(await api(`product/graph?parent=${encodeURIComponent(id)}`), activeProjectRef.current.name)
        : await api(`${nextMode === 'product' ? 'product/' : ''}graph?parent=${encodeURIComponent(id)}&limit=200`);
      if (sequence !== navigation.current) return;
      setGraph(data);
      if (id === 'products') setCatalog(data);
      const initial = selection || (id === 'products' ? data.nodes[0] || data.parent : id === 'root' ? data.nodes[0] || data.parent : data.parent);
      setSelected(initial); setLinkKind('all');
      if (!['root', 'products'].includes(id)) setRecent(items => [data.parent, ...items.filter(n => n.id !== id)].slice(0, 4));
      const params = new URLSearchParams({project: activeProjectRef.current.id});
      if (new URLSearchParams(location.search).get('map') === 'candidate') params.set('map', 'candidate');
      if (nextMode === 'technical') params.set('view', 'technical');
      if (!['root', 'products'].includes(id)) params.set('space', id);
      const url = params.size ? `/?${params}` : '/';
      if (replace) history.replaceState(null, '', url);
      else if (location.pathname + location.search !== url) history.pushState(null, '', url);
    } catch (err) { if (sequence === navigation.current) setError(err.message); }
    finally { if (sequence === navigation.current) { setLoading(false); pendingDestination.current = null; } }
  }
  function currentRoute() {
    const params = new URLSearchParams(location.search);
    if (params.get('view') === 'review') return 'review';
    return params.get('space') || (params.get('view') === 'technical' ? 'root' : 'products');
  }
  async function loadProject(project, route = 'products', replace = false) {
    const epoch = ++projectEpoch.current;
    ++initialization.current;
    const initialNavigation = ++navigation.current;
    ++sourceSequence.current; pendingDestination.current = null;
    activeProjectRef.current = project;
    setActiveProject(project); setStatus(null); setGraph(null); setCatalog(null); setSelected(null);
    setDetail(null); setSource(null); setProof(null); setReturnFeature(null); setRecent([]); setQuery(''); setResults([]);
    setError(''); setLoading(true); setView(route === 'products' ? 'overview' : 'map');
    scene.current?.setData({nodes: [], edges: []});
    try {
      const [nextStatus, nextCatalog] = await Promise.all([api('status'), api('product/graph?parent=products')]);
      if (epoch !== projectEpoch.current) return;
      observedIndex.current = nextStatus.indexedAt;
      setStatus(nextStatus); setCatalog(nextCatalog);
      if (navigation.current === initialNavigation) await navigate(route, null, replace);
    } catch (err) {
      if (epoch === projectEpoch.current) { setError(err.message); setLoading(false); setProjectsOpen(true); }
    }
  }
  async function initialize() {
    const sequence = ++initialization.current, epoch = projectEpoch.current;
    try {
      const data = await request('projects');
      if (sequence !== initialization.current || epoch !== projectEpoch.current) return;
      const requested = new URLSearchParams(location.search).get('project');
      const id = requested || data.activeProjectId;
      const project = data.projects.find(item => item.id === id) || (!requested && data.projects.find(item => item.indexedAt));
      if (!project || !project.indexedAt) {
        setLoading(false); setProjectsOpen(true);
        if (requested && !project) setError('That repository is not saved here. Choose one from your projects.');
        return;
      }
      if (project.id === activeProjectRef.current?.id) navigate(currentRoute(), null, true);
      else loadProject(project, currentRoute(), true);
    } catch (err) { if (sequence === initialization.current && epoch === projectEpoch.current) { setError(err.message); setLoading(false); } }
  }
  function projectSelected(project) { setProjectsOpen(false); setProjectView(null); loadProject(project, reviewOpen ? 'review' : 'products'); }
  function projectUpdated(project) {
    if (project.id === activeProjectRef.current?.id) loadProject(project, reviewOpen ? 'review' : 'products', true);
  }
  function openProjects(review = false) { setProjectView(review ? activeProjectRef.current?.id : null); setProjectsOpen(true); }
  function toggleBrowse() {
    setSidebarOpen(open => !open);
    if (panelsOverlaid) setInspectorCollapsed(true);
  }
  function toggleNotes() {
    setInspectorCollapsed(collapsed => !collapsed);
    if (panelsOverlaid) setSidebarOpen(false);
  }
  function toggleExpanded() {
    setMapExpanded(expanded => !expanded);
    setSidebarOpen(mapExpanded && !compactWindow);
    setInspectorCollapsed(!mapExpanded || compactWindow);
  }
  function home() { setReturnFeature(null); setView(mode === 'product' ? 'overview' : 'map'); navigate(mode === 'product' ? 'products' : 'root'); }
  function switchMode(nextMode) { setReturnFeature(null); setView(nextMode === 'product' ? 'overview' : 'map'); navigate(nextMode === 'product' ? 'products' : 'root'); }
  function openTechnical(node) {
    if (isProduct(selected)) setReturnFeature(selected);
    else if (isProduct(graph?.parent)) setReturnFeature(graph.parent);
    setView('map');
    navigate(canEnter(node) ? node.id : node.parentId || 'root', canEnter(node) ? null : node);
  }
  function selectNode(node) {
    setInspectorCollapsed(false);
    if (panelsOverlaid) setSidebarOpen(false);
    if (node.id === graph?.parent.id) { setSelected(node); scene.current?.focus(node.id); return; }
    if (isProduct(node)) { setView('map'); navigate(node.id); return; }
    if (mode === 'product') { openTechnical(node); return; }
    if (canEnter(node)) { setView('map'); navigate(node.id); return; }
    setSelected(node); scene.current?.focus(node.id);
  }
  function enterNode(node) { selectNode(node); }
  async function jumpTo(node) {
    setQuery('');
    if (isProduct(node) || canEnter(node) || mode === 'product') { selectNode(node); return; }
    if (node.id === graph?.parent.id || graph?.nodes.some(n => n.id === node.id)) selectNode(node);
    else {
      setInspectorCollapsed(false);
      if (panelsOverlaid) setSidebarOpen(false);
      navigate(node.parentId || 'root', node);
    }
  }
  async function showSource(node) {
    const sequence = ++sourceSequence.current;
    setProof(null); setSource({ loading: true, node });
    try { const data = await api(`source?id=${encodeURIComponent(node.id)}${node.evidenceLine ? `&line=${node.evidenceLine}` : ''}`); if (sequence === sourceSequence.current) setSource({ ...data, node }); }
    catch (err) { if (sequence === sourceSequence.current) setSource({ error: err.message, node }); }
  }
  function closeSource() { sourceSequence.current++; setSource(null); }
  function showEvidence(item, direction = 'outgoing') {
    const origin = detail?.node || selected;
    setProof({ from: direction === 'incoming' ? item.node : origin, to: direction === 'incoming' ? origin : item.node, ...item });
  }
  actions.current = { selectNode, enterNode, initialize, projectUpdated };

  useEffect(() => {
    const media = window.matchMedia('(max-width: 1100px)');
    const update = () => {
      setCompactWindow(media.matches);
      if (media.matches) { setSidebarOpen(false); setInspectorCollapsed(true); }
    };
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => { initialize(); const pop = () => actions.current.initialize(); window.addEventListener('popstate', pop); return () => window.removeEventListener('popstate', pop); }, []);
  useEffect(() => {
    if (!container.current) return;
    try {
      scene.current = new Universe(container.current, {
        onSelect: node => actions.current.selectNode(node),
        onHover: () => queueMicrotask(() => setRenderStats(scene.current?.stats)), onError: setSceneError,
      });
      scene.current.setMotion(motion);
    } catch { setSceneError('The 3D view needs WebGL. You can still browse every feature and connection in the list.'); }
    return () => scene.current?.dispose();
  }, []);
  useEffect(() => { if (graph) { scene.current?.setData(graph, selected?.id); scene.current?.setEdgesVisible(showEdges); setRenderStats(scene.current?.stats); } }, [graph]);
  useEffect(() => { scene.current?.select(selected?.id); setRenderStats(scene.current?.stats); }, [selected?.id]);
  useEffect(() => { scene.current?.setEdgesVisible(showEdges); setRenderStats(scene.current?.stats); }, [showEdges]);
  useEffect(() => { scene.current?.setMotion(motion); }, [motion]);
  useEffect(() => { scene.current?.setVisible(view === 'map' && !reviewOpen); }, [view, reviewOpen]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController(); setDetail(null); setDetailError('');
    api(`${isProduct(selected) ? 'product/' : ''}node?id=${encodeURIComponent(selected.id)}`, controller.signal).then(setDetail).catch(err => { if (err.name !== 'AbortError') setDetailError(err.message); });
    return () => controller.abort();
  }, [selected?.id, activeProject?.id]);
  useEffect(() => {
    if (!query.trim()) { setResults([]); setSearching(false); setSearchError(''); return; }
    const controller = new AbortController(); setSearching(true);
    const timer = setTimeout(() => api(`search?q=${encodeURIComponent(query.trim())}`, controller.signal).then(data => { setResults(data.results); setSearchError(''); }).catch(err => { if (err.name !== 'AbortError') setSearchError(err.message); }).finally(() => { if (!controller.signal.aborted) setSearching(false); }), 180);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, activeProject?.id]);
  useEffect(() => {
    function handleKey(event) {
      if (projectsOpen) return;
      if (reviewOpen) { if (event.key === 'Escape') setHelp(false); return; }
      if ((event.metaKey || event.ctrlKey) && event.key === 'k') { event.preventDefault(); setSidebarOpen(true); if (panelsOverlaid) setInspectorCollapsed(true); requestAnimationFrame(() => searchInput.current?.focus()); }
      if (event.key === 'Escape') { setQuery(''); closeSource(); setProof(null); setHelp(false); searchInput.current?.blur(); if (panelsOverlaid && !source && !proof && !help) { setSidebarOpen(false); setInspectorCollapsed(true); } }
    }
    window.addEventListener('keydown', handleKey); return () => window.removeEventListener('keydown', handleKey);
  }, [panelsOverlaid, source, proof, help, projectsOpen, reviewOpen]);
  useEffect(() => {
    if (!activeProject?.id) return;
    const id = activeProject.id;
    let cancelled = false;
    const refresh = async () => {
      if (document.hidden) return;
      try {
        const data = await request('projects');
        const project = data.projects.find(item => item.id === id);
        if (cancelled || activeProjectRef.current?.id !== id || !project) return;
        activeProjectRef.current = project; setActiveProject(project);
        if (project.indexedAt && observedIndex.current && project.indexedAt !== observedIndex.current) actions.current.projectUpdated(project);
      } catch { /* A transient status failure should not discard a working graph. */ }
    };
    const timer = setInterval(refresh, 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [activeProject?.id]);
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(''), 3000); return () => clearTimeout(timer); }, [notice]);

  const productMode = mode === 'product';
  const node = detail?.node || selected;
  const metrics = node?.metrics || {};
  const incoming = useMemo(() => groupRelationships(detail?.relationships?.incoming), [detail]);
  const outgoing = useMemo(() => groupRelationships(detail?.relationships?.outgoing), [detail]);
  const rootView = !graph || ['root', 'products'].includes(graph.parent.id);
  const foundationScope = graph?.scope === 'foundation-code';
  const referenceConnections = productMode || foundationScope;
  const outerScope = breadcrumbsFor(graph).at(-2);
  const edges = (graph?.edges || []).filter(edge => linkKind === 'all' || edge.kind === linkKind);
  const byId = new Map((graph?.nodes || []).map(n => [n.id, n]));
  const breadcrumbs = graph?.breadcrumbs || [];
  const sidebarNodes = graph?.nodes || [];
  const codeRoots = detail?.codeRoots || [];
  const productResults = query.trim() ? [...(catalog?.nodes || []), ...(!productMode ? catalog?.foundations || [] : []), ...(graph?.nodes || []).filter(isProduct)].filter((item, i, all) => (!productMode || item.kind !== 'foundation') && all.findIndex(n => n.id === item.id) === i && `${item.name} ${item.summary}`.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 8) : [];
  const summary = node?.summary || '';

  return <div className={`app-shell ${view === 'overview' && productMode ? 'overview-mode' : ''} ${reviewOpen ? 'review-mode' : productMode ? 'product-mode' : 'technical-mode'}`}>
    <header className="topbar">
      <button className="brand" onClick={() => { setReturnFeature(null); navigate('products'); }} aria-label="Atlas home"><span className="brand-mark"><Orbit size={24} strokeWidth={1.25}/></span><span>atlas<span className="brand-dot">.</span></span><span className="brand-caption">YOUR PRODUCT, CONNECTED</span></button>
      <div className="perspective-switch" aria-label="Explorer perspective"><button className={!reviewOpen && productMode ? 'active' : ''} onClick={() => switchMode('product')} aria-pressed={!reviewOpen && productMode}><Orbit size={15}/> Product guide</button><button className={!reviewOpen && !productMode ? 'active' : ''} onClick={() => switchMode('technical')} aria-pressed={!reviewOpen && !productMode}><Code2 size={15}/> Technical depth</button><button className={reviewOpen ? 'active' : ''} onClick={() => navigate('review')} aria-pressed={reviewOpen}><GitBranch size={15}/> PR review</button></div>
      <div className="header-actions"><button className="icon-button" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')} title={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`} aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}>{theme === 'light' ? <Moon size={18}/> : <Sun size={18}/>}</button><button className="project-picker" onClick={() => openProjects()} title={activeProject?.path || 'Choose a repository'}><Folder size={14}/><span>{activeProject?.name || 'Choose repository'}</span><ChevronRight size={12}/></button><button className="icon-button" onClick={() => setHelp(true)} title="About Atlas" aria-label="About Atlas"><CircleHelp size={18}/></button><span className="version">EXPLORER 03</span></div>
    </header>
    {reviewOpen && <PRReview key={activeProject?.id || 'none'} project={activeProject} onClose={() => switchMode('product')}/>}
    <div className={`workspace ${panelsOverlaid ? 'panels-overlaid' : ''}`} style={reviewOpen ? { display: 'none' } : undefined}>
      <aside id="browse-panel" aria-label="Browse the universe" className={`sidebar ${sidebarOpen ? '' : 'panel-collapsed'}`}>
        <div className="sidebar-top"><div className="eyebrow">THE OBSERVATORY</div><button className="panel-close sidebar-close" onClick={() => setSidebarOpen(false)} aria-label="Close browse panel"><X size={15}/></button><button className={`home-item ${rootView ? 'active' : ''}`} onClick={home}><Globe2 size={17}/><span>{productMode ? 'Product overview' : 'Repository modules'}</span><ChevronRight size={14}/></button>
          <div className="search-wrap"><Search size={15}/><input ref={searchInput} aria-label="Find code or features" placeholder={productMode ? 'Find a feature or code…' : 'Find a module or foundation…'} value={query} onChange={e => setQuery(e.target.value)}/>{query ? <button onClick={() => setQuery('')} aria-label="Clear search"><X size={13}/></button> : <kbd>⌘ K</kbd>}</div>
        </div>
        {query ? <div className="sidebar-scroll search-results">
          {productResults.length > 0 && <><div className="list-heading">{productMode ? 'FEATURES & CAPABILITIES' : 'FEATURES & CODE AREAS'}</div>{productResults.map(result => <SearchResult key={result.id} node={result} onClick={jumpTo}/>)}</>}
          <div className="list-heading">{searching ? 'SEARCHING CODE' : `${results.length} CODE MATCHES`}</div>{searching && <Loader2 className="spin muted" size={18}/>} {searchError && <p className="small-error">{searchError}</p>}{!searching && !results.length && !productResults.length && <p className="empty-copy">Try a feature, folder, file, or function name.</p>}{results.map(result => <SearchResult key={result.id} node={result} onClick={jumpTo}/>)}</div> : <>
          <div className="sidebar-section-label"><span>{productMode ? rootView ? 'PRODUCT FEATURES' : foundationScope ? 'FEATURES USING IT' : graph?.parent.metrics?.capabilities ? 'CAPABILITIES' : 'CODE AREAS' : rootView ? 'MODULES' : foundationScope ? 'CODE AREAS' : 'IN THIS MODULE'}</span><span>{number(foundationScope ? sidebarNodes.length : graph?.total ?? graph?.nodes.length)}</span></div>
          <div className="sidebar-scroll node-list">{sidebarNodes.map(item => <div key={item.id} className={`node-row ${selected?.id === item.id ? 'selected' : ''} ${isProduct(item) ? 'feature-row' : ''}`}><button className="node-select" onClick={event => { if (event.detail < 2) selectNode(item); }} title={item.summary || item.path}><span className="node-dot" style={{ '--node-color': colorFor(item) }}/><span className="node-name">{item.name}{isProduct(item) && <small>{item.kind === 'feature' ? featureCount(item) : kindName(item)}</small>}</span>{!isProduct(item) && <span className="node-count">{compact(item.metrics?.files || item.metrics?.children || 0)}</span>}</button>{(canEnter(item) || isProduct(item)) && <button className="node-enter" onClick={() => enterNode(item)} aria-label={`Explore ${item.name}`}><ChevronRight size={14}/></button>}</div>)}{graph?.truncated && <p className="empty-copy">Showing {graph.nodes.length} of {number(graph.total)} items. Search finds every indexed item.</p>}{loading && !graph && <div className="loading-rail"><Loader2 size={20} className="spin"/> Building the view…</div>}</div>
          {!productMode && recent.filter(n => !isProduct(n)).length > 0 && <div className="recent"><div className="list-heading">RECENTLY EXPLORED</div>{recent.filter(n => !isProduct(n)).slice(0, 3).map(item => <button key={item.id} onClick={() => navigate(item.id)}><Orbit size={13}/><span>{item.name}</span><ArrowUpRight size={12}/></button>)}</div>}
        </>}
      </aside>
      <main className="main-view">
        <div className="view-heading"><button className={`panel-toggle ${sidebarOpen ? 'active' : ''}`} onClick={toggleBrowse} aria-label="Toggle browse panel" aria-expanded={sidebarOpen} aria-controls="browse-panel"><PanelLeft size={15}/><span>Browse</span></button><div className="breadcrumb"><button onClick={home}><Orbit size={14}/>{productMode ? 'Product' : activeProject?.name || 'Repository'}</button>{breadcrumbs.filter(n => !['root', 'products'].includes(n.id)).map(crumb => <React.Fragment key={crumb.id}><ChevronRight size={12}/><button onClick={() => navigate(crumb.id)}>{crumb.name}</button></React.Fragment>)}{rootView && <><span className="breadcrumb-divider">/</span><span>{productMode ? 'How it fits together' : 'All modules'}</span></>}</div><div className="view-switch">{productMode && <button aria-label="Product overview" className={view === 'overview' ? 'active' : ''} onClick={() => { setView('overview'); navigate('products'); }}>Overview</button>}<button aria-label="Code map" className={view === 'map' ? 'active' : ''} onClick={() => setView('map')}><Orbit size={14}/> Code map</button><button aria-label="Connections view" className={view === 'connections' ? 'active' : ''} onClick={() => setView('connections')}><List size={14}/> Connections</button></div><div className="panel-actions"><button className={`panel-toggle ${!inspectorCollapsed ? 'active' : ''}`} onClick={toggleNotes} aria-label="Toggle field notes" aria-expanded={!inspectorCollapsed} aria-controls="field-notes"><PanelRight size={15}/><span>Field notes</span></button><button className="panel-toggle expand-universe" onClick={toggleExpanded} aria-label={mapExpanded ? 'Restore panels' : 'Expand universe'} aria-pressed={mapExpanded} title={mapExpanded ? 'Restore panels' : 'Expand universe'}>{mapExpanded ? <Minimize2 size={15}/> : <Maximize2 size={15}/>}<span>{mapExpanded ? 'Restore panels' : 'Expand universe'}</span></button></div></div>
        {activeProject && <div className="project-context">{activeProject.state === 'indexing' ? <span><Loader2 size={11} className="spin"/>{activeProject.job?.message || 'Updating graph…'}</span> : activeProject.changes?.state === 'changed' ? <button onClick={() => openProjects()}>Source changed · update available</button> : null}<button onClick={() => openProjects(true)}>Review feature map<ArrowUpRight size={11}/></button></div>}
        {!activeProject && !loading && !error && <div className="empty-project"><Orbit size={50}/><h2>A universe for your code.</h2><p>Choose a local repository to build its map, discover possible features, and follow the code that connects them.</p><button className="primary-button" onClick={() => openProjects()}>Choose a repository<ArrowRight size={15}/></button></div>}
        {!productMode && returnFeature && <button className="return-feature" onClick={() => navigate(returnFeature.id)}><ArrowLeft size={13}/> Back to {returnFeature.name}<span>Code behind this {kindName(returnFeature).toLowerCase()}</span></button>}
        {!rootView && <div className="scope-bar"><button onClick={() => { setView('map'); navigate(outerScope?.id || (productMode ? 'products' : 'root')); }}><ArrowLeft size={13}/>{outerScope && !['products', 'root'].includes(outerScope.id) ? `Back to ${outerScope.name}` : productMode ? 'All features' : 'All modules'}</button><span><span className="status-dot"/>Focused on {title(graph?.parent)}</span><button className="scope-reset" onClick={home}>{productMode ? 'Show full universe' : 'Show all modules'}<Maximize2 size={12}/></button></div>}
        <section className="intro" style={view === 'overview' ? {display: 'none'} : undefined}><div><div className="eyebrow"><span className="tiny-line"/>{productMode ? rootView ? 'START WITH WHAT PEOPLE DO' : `INSIDE ${kindName(graph?.parent).toUpperCase()}` : 'UNDER THE SURFACE'}</div><h1>{rootView ? productMode ? 'The product, connected.' : 'Under the surface.' : title(graph?.parent)}<span className="title-star">✳</span></h1><p>{rootView ? productMode ? 'Explore the features people use and how they connect.' : 'Folders, files, and the code between them. Trace a connection all the way down.' : graph?.parent.summary || 'Look around, follow the connections, and see how the pieces fit.'}</p></div></section>
        <div className="universe-meta" style={view === 'overview' ? {display: 'none'} : undefined}><span><span className="tiny-dot teal"/>{number(foundationScope ? sidebarNodes.length : graph?.nodes.length)} {productMode ? rootView ? 'product features' : foundationScope ? 'connected features' : graph?.parent.metrics?.capabilities ? 'capabilities' : 'code areas' : rootView ? 'modules' : 'nodes'}</span><span><Link2 size={12}/>{number(graph?.edges?.length)} {productMode ? 'direct connections' : 'connections'}</span><span className="depth-label">DEPTH {Math.max(0, breadcrumbs.length - 1).toString().padStart(2, '0')}</span></div>
        {productMode && view === 'overview' && activeProject && <ProductOverview project={activeProject} onSource={showSource}/>}
        <div className={`graph-stage ${view !== 'map' ? 'hidden-scene' : ''}`}><div className="universe-canvas" ref={container}/><div className="stage-vignette"/><div className="map-coordinate coordinate-top">ATLAS / {productMode ? 'PRODUCT LANDSCAPE' : 'CODE LANDSCAPE'}</div><div className="map-coordinate coordinate-bottom">{productMode ? 'THINGS PEOPLE COME HERE TO DO.' : 'THE CODE CONSTELLATION'}</div>{sceneError && <div className="scene-error"><Compass size={25}/><p>{sceneError}</p><button className="primary-button" onClick={() => setView('connections')}>Browse connections</button></div>}
          <div className="graph-toolbar"><div className="toolbar-group"><button onClick={() => setShowEdges(v => !v)} className={showEdges ? 'enabled' : ''} aria-label="Toggle connections" aria-pressed={showEdges}><GitBranch size={16}/><span>Connections</span><span className={`switch ${showEdges ? 'on' : ''}`}/></button></div><div className="toolbar-group"><button onClick={() => scene.current?.zoom(1)} title="Zoom in" aria-label="Zoom in"><Plus size={17}/></button><button onClick={() => scene.current?.zoom(-1)} title="Zoom out" aria-label="Zoom out"><Minus size={17}/></button><button onClick={() => scene.current?.reset()} title="Fit this space" aria-label="Fit this space"><Maximize2 size={15}/></button></div><div className="toolbar-group"><button onClick={() => setMotion(v => !v)} aria-label={motion ? 'Pause drifting' : 'Resume drifting'} title={motion ? 'Pause drifting' : 'Resume drifting'}>{motion ? <Pause size={14}/> : <Play size={14}/>}</button></div></div><div className="gesture-hint"><span>Drag to orbit</span><i>·</i><span>Scroll to zoom</span><i>·</i><span>Click to step inside</span></div>
        </div>
        {view === 'connections' && <div className="connections-view"><div className="connections-heading"><div><h2>{foundationScope ? `Connections inside ${graph.parent.name}` : productMode ? rootView ? 'How the features connect' : `Connections inside ${graph?.parent.name}` : 'How this code connects'}</h2><p>{foundationScope ? 'References between this foundation’s code areas. Open the evidence to see where.' : productMode ? 'Direct links found between feature code. Open the evidence to see why.' : 'Relationships found in the source. Select either end to look closer.'}</p></div>{!referenceConnections && <select aria-label="Filter relationships" value={linkKind} onChange={e => setLinkKind(e.target.value)}><option value="all">All relationships</option><option value="imports">Imports</option><option value="calls">Calls</option><option value="inherits">Inherits</option></select>}</div><div className={`edge-table ${referenceConnections ? 'product-edge-table' : ''}`}><div className="edge-table-heading"><span>FROM</span><span>{referenceConnections ? 'LINK' : 'RELATIONSHIP'}</span><span>TO</span><span>{referenceConnections ? 'EVIDENCE' : 'REFERENCES'}</span></div>{edges.length ? edges.slice(0, 500).map((edge, i) => <div className="edge-table-row" key={`${edge.source}-${edge.target}-${edge.kind}-${i}`}><button onClick={() => byId.get(edge.source) && selectNode(byId.get(edge.source))}>{byId.get(edge.source)?.name || edge.source}</button><span>{referenceConnections ? 'uses' : edge.kind}<ArrowRight size={12}/></span><button onClick={() => byId.get(edge.target) && selectNode(byId.get(edge.target))}>{byId.get(edge.target)?.name || edge.target}</button>{referenceConnections ? <button className="edge-proof" onClick={() => setProof({ ...edge, from: byId.get(edge.source), to: byId.get(edge.target) })}>{compact(edge.weight)}<Code2 size={12}/></button> : <span>{number(edge.weight || 1)}</span>}</div>) : <div className="empty-connections"><GitBranch size={30}/><h3>No direct links at this level.</h3><p>{productMode ? 'Open a feature to explore its capabilities and code. Shared infrastructure is available in Technical depth.' : 'Open a module to explore its files. Some relationships only appear closer in.'}</p></div>}{edges.length > 500 && <p className="empty-copy">Showing the first 500 connections. Open a smaller module to narrow the view.</p>}</div></div>}
        {loading && <div className="loading-overlay"><Loader2 size={22} className="spin"/><span>{productMode ? 'Connecting the product…' : 'Charting this module…'}</span></div>}
        {error && <div className="error-overlay"><Compass size={32}/><h2>This view is out of reach.</h2><p>{error}</p><div className="error-actions"><button className="primary-button" onClick={() => openProjects()}>Choose repository <Folder size={15}/></button><button className="secondary-button" onClick={initialize}>Try again <ArrowRight size={15}/></button></div></div>}
        <div className="map-legend">{productMode ? <><span><i style={{ background: '#79dfc7' }}/>Features & capabilities</span></> : <><span><i style={{ background: '#79dfc7' }}/>Files</span><span><i style={{ background: '#a69aef' }}/>Classes</span><span><i style={{ background: '#dfb77a' }}/>Functions</span></>}<span className="legend-note">{renderStats?.totalEdges > renderStats?.edges ? `${number(renderStats.edges)} links drawn · ` : ''}{productMode ? 'Connections follow code references' : 'Size reflects code volume'}</span></div>
      </main>
      <aside id="field-notes" className={`inspector ${inspectorCollapsed ? 'panel-collapsed' : ''}`} aria-label="Selected details"><button className="panel-close notes-close" onClick={() => setInspectorCollapsed(true)} aria-label="Close field notes"><X size={15}/></button>
        {node ? <><div className="inspector-kicker"><span>FIELD NOTES</span><span className="inspector-number">{kindName(node).toUpperCase()}</span></div><div className="planet-emblem" style={{ '--planet-color': colorFor(node) }}><span className="planet-orbit"/><span className="planet-core"/><span className="planet-moon"/></div><div className="inspector-title"><h2>{title(node)}</h2><span className="kind-badge">{kindName(node).toUpperCase()}</span></div>
          {!isProduct(node) && <div className="node-path"><NodeIcon node={node} size={12}/><span>{node.path || activeProject?.name || 'Repository'}</span><button onClick={async () => { try { await navigator.clipboard.writeText(node.path || activeProject?.name || 'Repository'); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { setNotice('Copy is unavailable. The path is shown above.'); } }} aria-label="Copy path">{copied ? <Check size={12}/> : <Copy size={12}/>}</button></div>}
          <p className="node-summary">{summary}</p><div className="summary-provenance"><span className="tiny-dot"/>{isProduct(node) ? node.summarySource === 'suggested' ? 'Suggested from source · needs your review' : node.summarySource === 'agent-generated' ? 'Agent-generated guide · awaiting review' : node.summarySource === 'reviewed' ? 'Reviewed product map' : node.kind === 'foundation' ? 'Shared across product features' : 'Mapped from repository docs & source' : /doc/i.test(node.summarySource || '') ? 'From its own documentation' : 'From the local code index'}</div>
          <FeatureDocumentation node={node} onSource={showSource}/>
          <div className="node-metrics">{isProduct(node) ? <><div><strong>{number(node.kind === 'foundation' ? incoming.length : metrics.children)}</strong><span>{node.kind === 'foundation' ? 'features use it' : node.kind === 'feature' && metrics.capabilities ? 'capabilities' : 'code areas'}</span></div><div><strong>{number(new Set([...incoming, ...outgoing].map(x => x.node.id)).size)}</strong><span>connections</span></div><div><strong>{number(node.kind === 'foundation' ? codeRoots.length : metrics.files)}</strong><span>{node.kind === 'foundation' ? 'code areas' : 'files'}</span></div></> : node.kind === 'directory' || node.kind === 'repository' ? <><div><strong>{number(metrics.files)}</strong><span>files</span></div><div><strong>{compact(metrics.symbols)}</strong><span>symbols</span></div><div><strong>{compact(metrics.lines)}</strong><span>indexed lines</span></div></> : <><div><strong>{number(metrics.children)}</strong><span>definitions</span></div><div><strong>{number(metrics.lines)}</strong><span>lines</span></div><div><strong>{node.line || '—'}</strong><span>starts at line</span></div></>}</div>
          {(canEnter(node) || isProduct(node)) && node.id !== graph?.parent.id && node.kind !== 'foundation' && <button className="primary-button explore-button" onClick={() => enterNode(node)}>{node.kind === 'feature' ? 'Explore this feature' : node.kind === 'capability' ? 'Explore this capability' : 'Take a closer look'}<ArrowRight size={16}/></button>}
          {isProduct(node) && codeRoots.length > 0 && <button className="secondary-button source-button" onClick={() => openTechnical(codeRoots[0])}><Code2 size={15}/>See the code<ArrowUpRight size={14}/></button>}
          {['file', 'class', 'function'].includes(node.kind) && <button className="secondary-button source-button" onClick={() => showSource(node)}><Code2 size={15}/>Read the source<ArrowUpRight size={14}/></button>}
          <div className="inspector-scroll">{detailError && <p className="small-error">{detailError}</p>}{!detail && !detailError && <div className="detail-loading"><Loader2 size={15} className="spin"/> Reading connections…</div>}
            {detail && <>
              <div className="detail-section"><div className="section-heading"><h3>{isProduct(node) ? node.kind === 'foundation' ? 'Features that use it' : 'Product connections' : 'Connected to'}</h3><span>{new Set([...incoming, ...outgoing].map(x => x.node.id)).size}</span></div>
                {node.kind !== 'foundation' && <><div className="connection-caption"><ArrowUpRight size={13}/><span>{isProduct(node) ? 'Uses parts of' : 'Uses code from'}</span></div>{outgoing.length ? outgoing.slice(0, isProduct(node) ? 20 : 6).map(item => <Relationship key={item.node.id} item={item} onClick={jumpTo} onEvidence={() => showEvidence(item)}/>) : <p className="empty-detail">No direct outgoing links found.</p>}<div className="connection-caption incoming"><ArrowDownLeft size={13}/><span>Used by</span></div></>}
                {incoming.length ? incoming.slice(0, isProduct(node) ? 20 : 6).map(item => <Relationship key={item.node.id} item={item} onClick={jumpTo} onEvidence={() => showEvidence(item, 'incoming')}/>) : <p className="empty-detail">No direct incoming links found.</p>}{isProduct(node) && <p className="connection-footnote">{node.kind === 'foundation' ? 'References from feature code into this foundation. Open the code button to see examples.' : 'Direct references between feature code. Shared storage alone does not create a connection.'}</p>}</div>
              {detail.children?.length > 0 && (!isProduct(node) || node.kind === 'feature') && <div className="detail-section"><div className="section-heading"><h3>{node.kind === 'feature' && metrics.capabilities ? 'What people can do' : 'What’s inside'}</h3><span>{number(metrics.children)}</span></div>{detail.children.slice(0, isProduct(node) ? 10 : 6).map(child => <button key={child.id} className="child-link" onClick={() => isProduct(child) ? jumpTo(child) : productMode ? openTechnical(child) : jumpTo(child)}><NodeIcon node={child} size={13}/><span>{child.name}</span><ChevronRight size={12}/></button>)}</div>}
              {isProduct(node) && codeRoots.length > 0 && <div className="detail-section"><div className="section-heading"><h3>Where it lives in code</h3><Code2 size={14}/></div>{codeRoots.slice(0, 8).map(root => <button key={root.id} className="code-root-link" onClick={() => openTechnical(root)}><Folder size={12}/><span>{root.path}</span><ArrowUpRight size={12}/></button>)}<p className="connection-footnote">{number(metrics.files)} mapped files · {number(metrics.symbols)} symbols{codeRoots.length > 8 ? ` · ${number(codeRoots.length)} code areas` : ''}</p></div>}
            </>}
            {node.evidence?.length > 0 && <div className="detail-section evidence-section"><div className="section-heading"><h3>Where this comes from</h3><FileCode2 size={13}/></div>{node.evidence.slice(0, 3).map((evidence, i) => <div className="evidence-path" key={i}>{typeof evidence === 'string' ? evidence : evidence.path}</div>)}</div>}
          </div><div className="inspector-tip"><Sparkles size={14}/><p>{isProduct(node) ? 'Start with the feature. Follow the connection.' : 'One connection can explain a lot.'}<br/><span>{isProduct(node) ? 'The implementation is one level below.' : 'Follow it and see where it goes.'}</span></p></div>
        </> : <div className="inspector-empty"><Orbit size={40}/><h2>Follow your curiosity.</h2><p>Choose a feature to see what it does and what it connects to.</p></div>}
      </aside>
    </div>
    <footer className="statusbar" style={reviewOpen ? { display: 'none' } : undefined}>
      <div className="footer-status"><span className="status-dot"/>{status ? 'Local index ready' : 'Connecting to local index'}<span className="footer-index-details"><span className="footer-separator">/</span><button onClick={() => setHelp(true)}>{productMode ? 'Product map · source-backed' : status?.indexedAt ? `Indexed ${new Date(status.indexedAt).toLocaleDateString('en', { month: 'short', day: 'numeric' })}` : 'Local repository'}</button></span></div>
      <div className="footer-stats"><span>{number(status?.stats?.files)} files</span><span>{number(status?.stats?.symbols)} symbols</span><span>{number(status?.stats?.semanticEdges ?? status?.stats?.edges)} code links</span></div>
      <button className="footer-note" onClick={() => setHelp(true)} title="About this map">
        {productMode ? <Layers3 size={14}/> : <GitBranch size={14}/>}
        <span><strong>{productMode ? 'Features first. Code beneath.' : 'Grounded in your code'}</strong><small>{productMode ? 'Connections checked against source' : 'Local source graph'}</small></span>
        <CircleHelp size={12}/>
      </button>
    </footer>
    <ProjectManager open={projectsOpen} onClose={() => { setProjectsOpen(false); setProjectView(null); }} onProjectSelected={projectSelected} onProjectUpdated={projectUpdated} activeProjectId={activeProject?.id} reviewProjectId={projectView}/>
    {notice && <div className="toast" role="status">{notice}</div>}
    {help && <Modal title="From the product to the code." onClose={() => setHelp(false)}><p className="modal-lead">The product universe starts with what people can do. Technical depth shows how those features are built.</p><div className="help-grid"><div><Orbit/><h3>Explore a feature</h3><p>Click a feature to focus the map on its capabilities. Click again to step into its code areas. All features and the breadcrumbs take you back out.</p></div><div><Layers3/><h3>Look beneath it</h3><p>Open Technical depth to explore identity, storage, models, and other shared code. Search there for a foundation name or browse its modules.</p></div><div><Link2/><h3>Read a connection</h3><p>A line means code in one feature references code assigned to another. Shared use of a foundation does not create a feature connection. Open the evidence beside a connection to see its source.</p></div><div><Code2/><h3>Go into the implementation</h3><p>“See the code” opens a feature’s module in Technical depth. Search works across features, folders, files, classes, and functions. Press ⌘K or Ctrl+K.</p></div></div><div className="coverage-note"><h3>How to read this map</h3><p>Feature boundaries start as suggestions from repository names and documentation, and can be reviewed and edited. Connections are measured from the local index. They show code relationships, not the order of a user journey or a running request.</p><p>Each repository has its own editable feature map. Open Review feature map to change it. Unmapped code stays available in Technical depth; tests and operational tooling do not create product connections. Assign code to features using paths and evidence from your repository; a container folder can hold several features.</p>{(catalog?.coverage || graph?.coverage) && <Coverage coverage={catalog?.coverage || graph?.coverage}/>}<p>Open the repository menu to check for changes or rebuild the graph. Turn on automatic updates there to rebuild when source files change. Your reviewed feature groups are preserved. Source previews read the current files; stored relationships come from the last completed index.</p></div></Modal>}
    {proof && <Modal title="Why are they connected?" onClose={() => setProof(null)}><div className="proof-route"><span>{proof.from?.name || 'Source'}</span><ArrowRight size={18}/><span>{proof.to?.name || proof.node?.name || 'Target'}</span></div><p className="modal-lead">{`${proof.from?.name || 'This area'} uses parts of ${proof.to?.name || 'the connected area'}. These examples show where their code meets.`}</p><div className="proof-count"><GitBranch size={15}/>{number(proof.weight)} code references{proof.breakdown && <span>{Object.entries(proof.breakdown).filter(([, value]) => value).map(([key, value]) => `${number(value)} ${key}`).join(' · ')}</span>}</div><div className="proof-samples">{(proof.evidence || []).map((evidence, i) => <div key={i} className="proof-sample"><div><span>FROM</span><code>{evidence.path}:{evidence.line}</code>{evidence.targetPath && <><span>TO</span><code>{evidence.targetPath}{evidence.targetLine ? `:${evidence.targetLine}` : ''}</code></>}</div><button onClick={() => showSource({ id: `file:${evidence.path}`, path: evidence.path, name: 'Connection evidence', evidenceLine: evidence.line })}>Read reference<ArrowUpRight size={13}/></button></div>)}</div><p className="source-note">Showing sampled source locations. These are static code links; they do not imply a user flow.</p></Modal>}
    {source && <Modal title={source.node?.name || 'Source'} onClose={closeSource} wide><div className="source-meta"><FileCode2 size={14}/><span>{source.path || source.node?.path}</span><span>READ ONLY</span></div>{source.loading ? <p className="source-loading"><Loader2 className="spin"/>Reading the file…</p> : source.error ? <p className="small-error">{source.error}</p> : <div className="source-code">{(source.lines || []).map((line, i) => <div className={source.startLine + i === source.highlightLine ? 'highlight' : ''} key={i}><span>{source.startLine + i}</span><code>{line || ' '}</code></div>)}</div>}<p className="source-note">{source.truncated ? 'Showing an excerpt around this definition. ' : ''}A window into the indexed file.</p></Modal>}
  </div>;
}
function SearchResult({ node, onClick }) { return <button className="search-result" onClick={() => onClick(node)}><NodeIcon node={node} size={15}/><span><strong>{node.name}</strong><small>{isProduct(node) ? kindName(node) : `${node.path}${node.line ? `:${node.line}` : ''}`}</small></span></button>; }
function Relationship({ item, onClick, onEvidence }) { return <div className="relationship-wrap"><button className="relationship" onClick={() => onClick(item.node)} title={item.description || item.kind}><span className="relationship-dot" style={{ background: colorFor(item.node) }}/><span>{item.node.name}</span><small>{compact(item.weight)}</small><ArrowUpRight size={12}/></button>{item.evidence?.length > 0 && <button className="evidence-button" aria-label={`Why connected to ${item.node.name}`} title="See the connection evidence" onClick={onEvidence}><Code2 size={12}/></button>}</div>; }
function Coverage({ coverage }) { return <p className="coverage-counts">{coverage.mappedFiles != null && `${number(coverage.mappedFiles)} files mapped. `}{coverage.unmappedFiles != null && `${number(coverage.unmappedFiles)} files not assigned yet. `}{coverage.excludedFiles != null && `${number(coverage.excludedFiles)} files kept out of the product map.`}</p>; }
function Modal({ title, onClose, children, wide }) {
  const ref = useRef(null);
  useEffect(() => { const previous = document.activeElement; ref.current?.focus(); return () => previous?.focus(); }, []);
  function trap(event) {
    if (event.key !== 'Tab') return;
    const focusable = [...ref.current.querySelectorAll('button, input, select, a[href], [tabindex="0"]')];
    if (!focusable.length) return;
    if (event.shiftKey && (document.activeElement === focusable[0] || document.activeElement === ref.current)) { event.preventDefault(); focusable.at(-1).focus(); }
    else if (!event.shiftKey && document.activeElement === focusable.at(-1)) { event.preventDefault(); focusable[0].focus(); }
  }
  return <div className="modal-backdrop" onClick={onClose}><div className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref} onKeyDown={trap} onClick={event => event.stopPropagation()}><div className="modal-header"><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={20}/></button></div>{children}</div></div>;
}
createRoot(document.getElementById('root')).render(<App/>);
