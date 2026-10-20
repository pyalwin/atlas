import {primaryPath, workflowLayout} from './workflow-layout.mjs';
import React, {useState} from 'react';

function Evidence({items = [], onSource}) {
  return items.length ? <details className="journey-evidence"><summary>Why does Atlas say this?</summary><p>This explanation comes from the project’s source. It describes expected behavior, not an observed run.</p>{items.map((item, index) => <button key={index} onClick={() => onSource({id: `file:${item.path}`, name: item.path, kind: 'file', evidenceLine: item.line})}>{item.path}:{item.line}</button>)}</details> : null;
}
function Detail({detail, onSource}) {
  return <details className="journey-detail"><summary>{detail.title}</summary><p>{detail.description}</p>{detail.details?.map((child, index) => <Detail key={index} detail={child} onSource={onSource}/>)}<Evidence items={detail.evidence} onSource={onSource}/></details>;
}

export function FunctionalityExplorer({journeys, onSource}) {
  const [selectedId, setSelectedId] = useState(journeys[0]?.id);
  const [selectedStep, setSelectedStep] = useState(null);
  const [expanded, setExpanded] = useState([]);
  const [example, setExample] = useState(null);
  const journey = journeys.find(item => item.id === selectedId) || journeys[0];
  if (!journey) return null;
  const layout = workflowLayout(journey, expanded);
  const selected = journey.steps.find(step => step.id === selectedStep);
  const main = primaryPath(journey);
  const hasAlternatives = selected?.choices?.some(choice => !main.includes(choice.next));
  const selectJourney = id => {setSelectedId(id); setSelectedStep(null); setExpanded([]); setExample(null);};
  const selectStep = id => {setSelectedStep(id); setExample(null);};
  return <section className="functionality-explorer" aria-label="Functionality explorer">
    <header><span className="eyebrow">HOW THE PRODUCT WORKS</span><h2>{journey.title}</h2><p>{journey.goal}</p></header>
    {journeys.length > 1 && <nav className="workflow-tabs" aria-label="Workflows">{journeys.map(item => <button key={item.id} aria-pressed={journey.id === item.id} onClick={() => selectJourney(item.id)}>{item.title}</button>)}</nav>}
    <div className="workflow-diagram" aria-label="Interactive workflow diagram">
      <svg viewBox={`0 0 ${layout.width} ${layout.height}`} role="group" aria-label="Workflow steps; select a step to see what it does">
        <defs><marker id="workflow-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#638b85"/></marker></defs>
        {layout.links.map((link, index) => {const from = layout.positions.get(link.from), to = layout.positions.get(link.to); const alternate = !main.includes(link.to); return <g key={index}>
          <path d={`M ${from.x+165} ${from.y+37} C ${from.x+190} ${from.y+37}, ${to.x-25} ${to.y+37}, ${to.x} ${to.y+37}`} fill="none" stroke={example?.steps.some((id, index) => id === link.from && example.steps[index + 1] === link.to) ? '#b2e6c6' : '#638b85'} strokeWidth="1.5" strokeDasharray={alternate ? '4 4' : undefined} markerEnd="url(#workflow-arrow)"/>
          {alternate && <text x={(from.x+165+to.x)/2} y={(from.y+to.y)/2+22} fill="#b2c4b9" fontSize="10" textAnchor="middle">Alternative</text>}
        </g>;})}
        {layout.nodes.map(step => {const point = layout.positions.get(step.id);return <foreignObject key={step.id} x={point.x} y={point.y} width="166" height="90"><button className={`workflow-node ${selectedStep === step.id ? 'selected' : ''} ${example?.steps.includes(step.id) ? 'scenario-step' : ''}`} aria-pressed={selectedStep === step.id} onClick={() => selectStep(step.id)}><span>{step.diagramLabel || step.title}</span><small>{main.includes(step.id) ? 'Explore step' : 'Alternative outcome'}</small></button></foreignObject>;})}
      </svg>
    </div>
    <p className="overview-caption">Select a step to look closer. This diagram explains expected behavior; it does not run the product.</p>
    {selected ? <div className="workflow-step-explanation" aria-live="polite"><div className="journey-header"><h3>{selected.title}</h3><button onClick={() => setSelectedStep(null)}>Close explanation</button></div><p>{selected.result}</p>
      {selected.benefit && <p className="workflow-benefit"><strong>Why this helps</strong> {selected.benefit}</p>}
      {hasAlternatives && <button className="workflow-expand" aria-expanded={expanded.includes(selected.id)} onClick={() => setExpanded(previous => previous.includes(selected.id) ? previous.filter(id => id !== selected.id) : [...previous, selected.id])}>{expanded.includes(selected.id) ? 'Hide alternatives' : 'Show alternative outcomes'}</button>}
      {selected.details?.map((detail, index) => <Detail key={index} detail={detail} onSource={onSource}/>)}
      <Evidence items={selected.evidence} onSource={onSource}/>
    </div> : <p className="workflow-invitation">Start with any step. The diagram stays visible while you explore.</p>}
    {journey.scenarios?.length > 0 && <details className="journey-scenarios"><summary>Explore behavior examples</summary><p>Expected outcomes from the source, not executed tests.</p><div className="workflow-scenario-buttons">{journey.scenarios.map((scenario, index) => <button key={index} aria-pressed={example === scenario} onClick={() => {setExample(scenario); setExpanded(scenario.steps); setSelectedStep(null);}}>{scenario.title}</button>)}</div>{example && <article><h4>{example.title}</h4><dl><dt>Starting situation</dt><dd>{example.given}</dd><dt>Action</dt><dd>{example.when}</dd><dt>Expected result</dt><dd>{example.then}</dd></dl><Evidence items={example.evidence} onSource={onSource}/></article>}</details>}
  </section>;
}
