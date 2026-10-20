import {FunctionalityExplorer} from './FunctionalityExplorer.jsx';
import React, {useEffect, useState} from 'react';

function Sources({items = [], onSource}) {
  return items.length ? <div className="overview-sources">{items.map((item, index) => <button key={index} onClick={() => onSource({id: `file:${item.path}`, name: item.path, kind: 'file', evidenceLine: item.line})}>{item.path}:{item.line}</button>)}</div> : null;
}

export function ProductOverview({project, onSource}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError('');
    fetch(`/api/projects/${encodeURIComponent(project.id)}/map`, {signal: controller.signal}).then(async response => {
      if (!response.ok) throw new Error('Could not load the product guide.');
      const loaded = await response.json();
      if (new URLSearchParams(location.search).get('map') !== 'candidate' && !loaded.map?.project?.overview && loaded.suggestion?.project?.overview) {
        location.replace(`/?project=${encodeURIComponent(project.id)}&map=candidate`);
        return;
      }
      setData(loaded);
    }).catch(error => { if (error.name !== 'AbortError') setError(error.message); });
    return () => controller.abort();
  }, [project.id]);
  if (error) return <p role="alert">{error}</p>;
  if (!data) return <p className="empty-copy">Loading the product guide…</p>;
  const requested = new URLSearchParams(location.search).get('map');
  const candidate = requested === 'candidate' || (!data.map?.project?.overview && Boolean(data.suggestion?.project?.overview));
  const map = candidate ? data.suggestion : data.map;
  const overview = map?.project?.overview;
  const features = map?.features || [];
  const featureName = id => features.find(feature => feature.id === id)?.name || id;
  const href = id => `/?${new URLSearchParams({project: project.id, ...(candidate ? {map: 'candidate'} : {}), ...(id ? {space: `product:${id}`} : {})})}`;
  if (!overview) return <section className="product-overview"><h1>Understand {project.name}</h1><p>The source index is ready. A product explanation still needs to be written: what this project does, how to use it, and how its workflow fits together.</p><p>Ask your connected agent to read the source and generate the product overview through Atlas. Folder suggestions alone are not product documentation.</p></section>;
  return <article className="product-overview">
    {map?.generation?.indexedAt && map.generation.indexedAt !== data.project?.indexedAt && <p className="overview-draft" role="status">This guide describes an older source index. Recheck its explanations against current code.</p>}
    {candidate && <div className="overview-draft" role="status">Generated draft · awaiting review. This preview does not replace your saved map.</div>}
    {data.suggestion?.project?.overview && <nav className="overview-versions" aria-label="Guide version"><a href={`/?project=${project.id}`}>Product guide</a><a href={`/?project=${project.id}&map=candidate`}>Generated draft</a></nav>}
    <header><span className="eyebrow">UNDERSTAND THE PRODUCT</span><h1>What is {map.project.name}?</h1><p className="overview-lead">{overview.what}</p></header>
    {overview.journeys?.length > 0 && <FunctionalityExplorer key={`${project.id}:${candidate}:${map.generation?.createdAt || "saved"}`} journeys={overview.journeys} onSource={onSource}/>}
    <details className="overview-background" open={!overview.journeys?.length}><summary>Read the product guide and technical details</summary>
    <section><h2>How do I use it?</h2><div className="overview-usage">{overview.usage.map((item, index) => <div key={index}><h3>{item.name}</h3><p>{item.description}</p>{item.commands?.map((command, i) => <pre key={i}><code>{command}</code></pre>)}<Sources items={item.evidence} onSource={onSource}/></div>)}</div></section>
    <section><h2>How does it work?</h2><ol className="overview-flow">{overview.workflow.map((step, index) => <li key={index}><span className="overview-step">{index + 1}</span><div><h3>{step.title}{step.optional && <small>Optional</small>}</h3><p>{step.description}</p><Sources items={step.evidence} onSource={onSource}/></div></li>)}</ol><p className="overview-caption">This is a source-grounded explanation of the workflow, not a captured execution trace.</p></section>
    <section><h2>What can I ask it to do?</h2><div className="overview-tool-list">{overview.tools.map((tool, index) => <div key={index}><h3>{tool.name}</h3><p>{tool.description}</p><Sources items={tool.evidence} onSource={onSource}/></div>)}</div></section>
    <section><h2>How do the parts connect?</h2><div className="overview-relationships">{overview.connections.map((connection, index) => <div key={index}><h3><a href={href(connection.from)}>{featureName(connection.from)}</a><span aria-label="to"> → </span><a href={href(connection.to)}>{featureName(connection.to)}</a></h3><p>{connection.description}</p><Sources items={connection.evidence} onSource={onSource}/></div>)}</div></section>
    <section><h2>Explore the implementation</h2><div className="overview-feature-list">{features.map(feature => <a key={feature.id} href={href(feature.id)}><h3>{feature.name}</h3><p>{feature.summary}</p><span>Open feature →</span></a>)}</div></section>
    </details>
  </article>;
}
