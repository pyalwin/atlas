import React from 'react';
import { ArrowUpRight, Check, CircleHelp, GitBranch, Loader2, RefreshCw, TriangleAlert } from 'lucide-react';

const labels = { passed: 'Checks passed', failed: 'Checks need attention', pending: 'Checks are running', none: 'No checks found for this commit', unavailable: 'GitHub checks unavailable' };
function safeURL(value) {
  try { const url = new URL(value); return url.protocol === 'https:' && url.hostname === 'github.com' ? url.href : null; } catch { return null; }
}
export function ReviewCI({ data, loading, error, onRefresh, onRecord, recorded }) {
  const rows = data?.runs?.length ? data.runs : data?.checks || [];
  const skipped = rows.filter(run => run.conclusion === 'skipped').length;
  const successes = rows.filter(run => run.conclusion === 'success').length;
  return <section className="ru-ci" aria-label="GitHub Actions results">
    <header><span className="ru-overline"><GitBranch size={13}/>GITHUB ACTIONS</span><button className="ru-link" onClick={onRefresh} disabled={loading} aria-label="Refresh GitHub checks"><RefreshCw size={13}/>{loading ? 'Checking' : 'Refresh'}</button></header>
    <h4>{loading && !data ? <><Loader2 size={17} className="pr-spin"/>Reading this commit’s checks…</> : <>{data?.status === 'passed' ? <Check size={17}/> : data?.status === 'failed' ? <TriangleAlert size={17}/> : <CircleHelp size={17}/>} {labels[data?.status] || 'Connect check results'}</>}</h4>
    {data && <p className="ru-fine-print">Head {data.headSha?.slice(0, 8)}{data.checkedAt ? ` · Checked ${new Date(data.checkedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}</p>}
    {error && <p className="ru-ci-error" role="status">{error}</p>}
    {!!rows.length && <p className="ru-ci-summary">{successes} passed{skipped > 0 && <span className="skipped"> · {skipped} skipped</span>} · reported for this commit</p>}
    {rows.length > 0 && <details className="ru-details" open={data.status === 'failed' || data.status === 'pending' ? true : undefined}><summary>{rows.length} {data.runs?.length ? 'workflow runs' : 'check results'}</summary><div className="ru-ci-runs">{rows.map((run, index) => <div key={`${run.id}:${index}`}><span className={`ru-ci-dot ${run.conclusion || run.status}`}/>{safeURL(run.url) ? <a href={safeURL(run.url)} target="_blank" rel="noreferrer">{run.name}<ArrowUpRight size={12}/></a> : <span>{run.name}</span>}<small>{(run.conclusion || run.status || 'unknown').replaceAll('_', ' ')}</small></div>)}</div></details>}
    {data?.checks?.length > 0 && data?.runs?.length > 0 && <details className="ru-details"><summary>{data.checks.length} check results</summary><div className="ru-ci-runs">{data.checks.map((check,index)=><div key={`${check.id}:${index}`}><span className={`ru-ci-dot ${check.conclusion || check.status}`}/>{safeURL(check.url) ? <a href={safeURL(check.url)} target="_blank" rel="noreferrer">{check.name}<ArrowUpRight size={12}/></a> : <span>{check.name}</span>}<small>{(check.conclusion || check.status || 'unknown').replaceAll('_', ' ')}</small></div>)}</div></details>}
    {!!data?.limitations?.length && <details className="ru-details"><summary>What these results establish</summary>{data.limitations.map((item,index)=><p className="ru-fine-print" key={index}>{item}</p>)}</details>}
    {data?.status === 'passed' && data.complete !== false && <button className="ru-link" onClick={onRecord} disabled={loading}><Check size={14}/>{recorded ? 'Results recorded for this review' : 'Use these results as validation evidence'}</button>}
  </section>;
}
