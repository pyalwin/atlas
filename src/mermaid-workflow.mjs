// Diagrams are derived from the validated workflow; agents never supply executable Mermaid.
export function mermaidLabel(value) {
  return String(value ?? '').replace(/[&<>"#\\`|{}\[\];%\u0000-\u001f]/g,
    character => `#${character.codePointAt(0)};`);
}

export function workflowMermaid(workflow) {
  const steps = workflow.steps || [];
  const ids = new Map(steps.map((step, index) => [step.id, `N${index}`]));
  const lines = ['flowchart LR'];
  for (const step of steps) {
    const label = `"${mermaidLabel(step.diagramLabel || step.title)}"`;
    const shape = step.kind === 'decision' || (step.choices?.length || 0) > 1
      ? `{${label}}` : !step.choices?.length ? `([${label}])` : `[${label}]`;
    lines.push(`  ${ids.get(step.id)}${shape}`);
  }
  for (const step of steps) {
    for (const choice of step.choices || []) {
      if (!ids.has(choice.next)) throw new Error('Workflow contains an unknown destination.');
      lines.push(`  ${ids.get(step.id)} -->|"${mermaidLabel(choice.label)}"| ${ids.get(choice.next)}`);
    }
  }
  return lines.join('\n');
}
