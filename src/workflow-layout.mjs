export function primaryPath(journey) {
  if (journey.mainPath?.length) return journey.mainPath;
  const nodes = new Map(journey.steps.map(step => [step.id, step]));
  const path = [], seen = new Set();
  let id = journey.start;
  while (id && nodes.has(id) && !seen.has(id)) {path.push(id); seen.add(id); id = nodes.get(id).choices?.[0]?.next;}
  return path;
}

export function workflowLayout(journey, expanded = []) {
  const main = primaryPath(journey);
  const visible = new Set(main);
  const nodes = new Map(journey.steps.map(step => [step.id, step]));
  let changed = true;
  while (changed) {
    changed = false;
    for (const id of expanded) {
      if (!visible.has(id)) continue;
      for (const choice of nodes.get(id)?.choices || []) if (!visible.has(choice.next)) {visible.add(choice.next); changed = true;}
    }
  }
  // Use the full DAG for ranking so a shortcut can never pull a later node backwards.
  const ranks = new Map([[journey.start, 0]]);
  const incoming = new Map(journey.steps.map(step => [step.id, 0]));
  for (const step of journey.steps) for (const choice of step.choices || []) incoming.set(choice.next, (incoming.get(choice.next) || 0) + 1);
  const queue = journey.steps.filter(step => incoming.get(step.id) === 0).map(step => step.id);
  for (let i = 0; i < queue.length; i++) for (const choice of nodes.get(queue[i])?.choices || []) {
    ranks.set(choice.next, Math.max(ranks.get(choice.next) || 0, (ranks.get(queue[i]) || 0) + 1));
    incoming.set(choice.next, incoming.get(choice.next) - 1);
    if (!incoming.get(choice.next)) queue.push(choice.next);
  }
  const levels = [...new Set([...visible].map(id => ranks.get(id) || 0))].sort((a,b)=>a-b);
  const positions = new Map();
  levels.forEach((rank, column) => {
    const items = [...visible].filter(id => ranks.get(id) === rank).sort((a,b)=>Number(!main.includes(a))-Number(!main.includes(b)));
    items.forEach((id, row) => positions.set(id, {x: 24 + column * 205, y: 45 + row * 150}));
  });
  const links = journey.steps.flatMap(step => (step.choices || []).filter(choice => visible.has(step.id) && visible.has(choice.next)).map(choice => ({from: step.id, to: choice.next, label: choice.label})));
  return {nodes: journey.steps.filter(step => visible.has(step.id)), positions, links, width: Math.max(400, levels.length * 205 + 24), height: Math.max(180, ...[...positions.values()].map(point => point.y + 115))};
}
