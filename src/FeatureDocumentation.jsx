import {ReaderGuide} from './ReaderGuide.jsx';
import React from 'react';

const sections = {purpose: 'Purpose and boundaries', behavior: 'How it behaves', implementation: 'How the code works', dependencies: 'Connections and dependencies', testing: 'Tests and verification', limitations: 'Limitations and open questions'};

export function FeatureDocumentation({node, onSource}) {
  const documentation = node?.documentation || {};
  const entries = Array.isArray(node?.entryPoints) ? node.entryPoints : [];
  if (!Object.keys(documentation).length && !entries.length && !node?.readerGuide) return null;
  return <section className="feature-documentation" aria-label="Feature guide">
    {node.documentationFreshness === 'needs-refresh' && <p role="status">This guide describes an earlier index. Recheck the source and update it before relying on its behavior claims.</p>}
    {node.readerGuide && <ReaderGuide guide={node.readerGuide} onSource={onSource}/>}
    <details className="implementation-guide" open={!node.readerGuide}><summary>Implementation and verification</summary>
    {entries.length > 0 && <div className="feature-entry-points"><h3>Start in the code</h3>{entries.map((entry, index) => <div key={`${entry.path}:${entry.line}:${index}`}><button onClick={() => onSource({id: `file:${entry.path}`, name: entry.path, kind: 'file', evidenceLine: entry.line})}>{entry.path}:{entry.line}</button><p>{entry.reason}</p></div>)}</div>}
    {Object.entries(sections).filter(([key]) => typeof documentation[key] === 'string').map(([key, label]) => <details key={key} open={key === 'purpose' || key === 'behavior'}><summary>{label}</summary>{documentation[key].split(/\n\s*\n/).map((paragraph, index) => <p key={index}>{paragraph}</p>)}</details>)}
    </details>
  </section>;
}
