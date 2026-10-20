import React from 'react';
import {FunctionalityExplorer} from './FunctionalityExplorer.jsx';

function Proof({items, onSource}) {
  return <details className="reader-proof"><summary>Source behind this explanation</summary>{items.map((item, index) => <div key={index}><button onClick={() => onSource({id: `file:${item.path}`, name: item.path, kind: 'file', evidenceLine: item.line})}>{item.path}:{item.line}</button>{item.reason && <p>{item.reason}</p>}</div>)}</details>;
}

export function ReaderGuide({guide, onSource}) {
  if (!guide) return null;
  return <section className="reader-guide" aria-label="Product documentation">
    <details className="reader-outcome"><summary>Who this helps and what you can achieve</summary><span className="eyebrow">WHO THIS HELPS</span><p>{guide.audience}</p><h3>What you can achieve</h3><p>{guide.outcome.text}</p><Proof items={guide.outcome.evidence} onSource={onSource}/></details>
    {guide.prerequisites.length > 0 && <details className="reader-section"><summary>Before you start</summary>{guide.prerequisites.map((item, index) => <div key={index}><p>{item.text}</p><Proof items={item.evidence} onSource={onSource}/></div>)}</details>}
    <FunctionalityExplorer key={JSON.stringify(guide.workflows)} journeys={guide.workflows} onSource={onSource}/>
    {guide.rules.length > 0 && <details className="reader-section"><summary>Rules and limits that matter</summary>{guide.rules.map((item, index) => <div key={index}><p>{item.text}</p><Proof items={item.evidence} onSource={onSource}/></div>)}</details>}
    {guide.glossary.length > 0 && <details className="reader-section"><summary>Words used in this guide</summary><dl>{guide.glossary.map(item => <div key={item.term}><dt>{item.term}</dt><dd>{item.meaning}<Proof items={item.evidence} onSource={onSource}/></dd></div>)}</dl></details>}
  </section>;
}
