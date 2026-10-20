import React, {useEffect, useMemo, useState} from 'react';
import {workflowMermaid} from './mermaid-workflow.mjs';

let serial = Promise.resolve();
let nextId = 0;
function render(source, light) {
  const task = serial.catch(() => {}).then(async () => {
    const {default: mermaid} = await import('mermaid');
    mermaid.initialize({startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true,
      layout: 'dagre', theme: 'base', fontFamily: 'system-ui, sans-serif',
      themeVariables: {darkMode: !light, primaryColor: light ? '#e0eee8' : '#18352e',
        primaryTextColor: light ? '#183c33' : '#e0eee8', primaryBorderColor: light ? '#6c9a87' : '#79ad98',
        lineColor: light ? '#526e64' : '#9ab9ad', secondaryColor: light ? '#eef2f8' : '#202c3a',
        tertiaryColor: light ? '#fff5df' : '#39321e', edgeLabelBackground: light ? '#f4f7f9' : '#101b22'},
      flowchart: {htmlLabels: false, useMaxWidth: true, curve: 'basis'}});
    return (await mermaid.render(`atlas-flow-${++nextId}`, source)).svg;
  });
  serial = task;
  return task;
}

export function MermaidWorkflow({workflow, onSelect}) {
  const source = useMemo(() => workflowMermaid(workflow), [workflow]);
  const [theme, setTheme] = useState(document.documentElement.dataset.theme);
  const [result, setResult] = useState({});
  const [copied, setCopied] = useState(false);
  const [zoom, setZoom] = useState(1);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(document.documentElement.dataset.theme));
    observer.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let cancelled = false;
    setResult({});
    render(source, theme === 'light').then(svg => {if (!cancelled) setResult({svg});})
      .catch(() => {if (!cancelled) setResult({error: true});});
    return () => {cancelled = true;};
  }, [source, theme]);
  useEffect(() => setCopied(false), [source]);
  return <div className="mermaid-workflow">
    <div className="mermaid-controls" aria-label="Diagram size"><button onClick={() => setZoom(value => Math.max(1, value - .25))} disabled={zoom === 1} aria-label="Zoom out diagram">−</button><button onClick={() => setZoom(1)}>Fit diagram</button><button onClick={() => setZoom(value => Math.min(3, value + .25))} disabled={zoom === 3} aria-label="Zoom in diagram">+</button></div>
    {result.svg ? <div className="mermaid-chart" role="img" aria-label={`${workflow.title}: workflow diagram`} onClick={event => {
        const match = event.target.closest?.('.node')?.id.match(/^flowchart-N(\d+)-/);
        if (match && workflow.steps[Number(match[1])]) onSelect(workflow.steps[Number(match[1])].id);
      }} style={{'--diagram-zoom': zoom}} dangerouslySetInnerHTML={{__html: result.svg}}/>
      : result.error ? <p role="status">The chart could not be drawn. Explore the steps below or switch to the interactive view.</p> : <p role="status">Drawing the workflow…</p>}
    <nav className="mermaid-step-links" aria-label="Explore diagram steps">{workflow.steps.map((step, index) => <button key={step.id} onClick={() => onSelect(step.id)}>{index + 1}. {step.diagramLabel || step.title}</button>)}</nav>
    <details className="mermaid-source"><summary>Use this diagram in documentation</summary><p>The chart and the explanations use the same workflow. Copy the Mermaid text into your documentation.</p><button onClick={async () => {try {await navigator.clipboard.writeText(source); setCopied(true);} catch {setCopied(false);}}}>{copied ? 'Copied' : 'Copy Mermaid'}</button><pre><code>{source}</code></pre></details>
  </div>;
}
