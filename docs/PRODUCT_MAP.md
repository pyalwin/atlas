# Product maps

Each Atlas project has its own product map. Atlas suggests groups from the indexed repository, then lets you review their names, explanations, evidence, and code paths in the project editor. No repository-specific groups or descriptions are bundled.

The product view groups code by purpose. The technical view retains the repository's folders, files, definitions, and indexed references. Editing a map changes the product projection without modifying or re-parsing source files.

## JSON format

A map is a version 1 JSON object:

```json
{
  "version": 1,
  "features": [],
  "foundations": [],
  "excludedPaths": [],
  "notes": []
}
```

Features describe product behavior. Foundations describe shared support. Both use these fields:

| Field | Purpose |
| --- | --- |
| `id` | Stable identifier using lowercase letters, numbers, dashes, or underscores. |
| `name` | Displayed name. |
| `summary` | Explanation grounded in the repository. |
| `paths` | One or more literal repository-relative file or directory prefixes. |
| `evidence` | Optional repository-relative documentation or source paths supporting the explanation. |
| `summarySource` | Description origin, such as `suggested` or `reviewed`. |

A feature may include a `capabilities` array whose entries use the same fields. Capabilities divide selected parts of their feature; a feature can also contain code outside its named capabilities.

`excludedPaths` lists prefixes omitted from the product projection. `notes` contains explanatory strings. Unassigned and excluded files remain available in the technical view when the source index includes them.

## Assignment rules

1. Excluded prefixes take precedence over feature and foundation prefixes. Atlas also excludes common test and operational paths from product projection; the current defaults are defined in `atlas_index/product.py`.
2. Paths match exactly or at a `/` boundary. A prefix `src/component` matches `src/component/file.py`, but not `src/component_helpers.py`.
3. The longest matching prefix wins. A specific feature path can override a broader foundation path. Different owners cannot claim the same prefix.
4. Definitions inherit their source file's assignment.
5. Capability paths must fall inside their feature's paths. Peer capabilities cannot overlap.
6. Unmatched files remain unclassified; Atlas does not assume that they are shared infrastructure.

Feature IDs appear in API responses as `product:<feature-id>`, capability IDs as `capability:<feature-id>:<capability-id>`, and foundation IDs as `foundation:<foundation-id>`.

## Connections and evidence

A map declares groups, not edges. Connections are projected from imports, resolved calls, and inheritance found by the source index. Each connection retains sampled source and target locations for inspection.

A reference establishes a static code relationship. It does not establish runtime usage, request order, ownership, deployment boundaries, or complete change impact. Two features using the same foundation do not receive an inferred connection to each other.

## Reviewing and maintaining a map

Read the relevant source and documentation before changing a description. Use narrow paths when a shared folder contains several responsibilities, and keep evidence with the explanation it supports.

Saving reviewed edits validates path and capability relationships and refreshes the product projection. Check coverage and source evidence to see whether the grouping is useful. The editor does not establish that a named code path exists; verify paths against the repository.

Refreshing the index preserves the saved map. Requesting new suggestions creates a separate candidate for comparison. Maps and projection caches live in the project's local data directory and are ignored by Git. See the [README](../README.md) for storage locations and the local API.

## Agent-authored feature guides

Agent candidates follow the same version 1 ownership schema, with additional required documentation. Legacy deterministic maps remain valid without these fields. `mcp/feature-guide.json` is the generation brief returned by `get_summary_context`; the server enforces its structural requirements for candidate submissions.

The code map is optional supporting navigation. The primary product guide explains behavior and usage first. The older visual metaphor maps to the existing structure as follows:

| Universe element | Meaning in Atlas |
| --- | --- |
| Galaxy | The repository/product, described by top-level `project` |
| Solar system and its sun | A coherent feature, its purpose and implementation starting points |
| Planets | Distinct capabilities within that feature |
| Moons | Assigned implementation areas; drill down to files and definitions |
| Constellations | Cross-area relationships backed by indexed source references |
| Shared foundations | Infrastructure used by multiple systems, without invented ownership |

A sun represents the feature's purpose, not necessarily one all-important function. A feature can have several entry points (for example an HTTP route, a UI action and a background job). Avoid choosing a large utility or folder merely because it is prominent. Constellations are connections, not another ownership parent. Capability nesting and file ownership remain subject to the existing path rules.

Top-level `project` contains `name`, `summary` and `documentation`. Every feature, capability and foundation in an agent candidate contains:

- A `summary` of 80–4000 characters that explains the behavior in prose.
- `documentation` with six text sections: `purpose`, `behavior`, `implementation`, `dependencies`, `testing`, and `limitations`. Each section is 40–8000 characters and should use enough paragraphs to explain the area to someone unfamiliar with the code.
- At least one `evidence` object with `path` and `line` identifying an indexed file and a valid line in its current source. Add `reason` to explain what it supports.
- Real `paths` whose prefixes contain indexed files.

Each feature also needs `entryPoints`, a list of `{path, line, reason}` objects identifying where a reader should start. Entry points must belong to the feature's assigned paths, and `reason` must explain their relevance in at least 20 characters. Capabilities and foundations may supply entry points too.

The documentation sections should explain:

1. **Purpose:** the audience, outcome and boundary against adjacent features.
2. **Behavior:** inputs, outputs, state changes, error cases and invariants.
3. **Implementation:** the observed flow through entry points, modules and storage.
4. **Dependencies:** actual callers, imports and shared foundations, with assumptions identified.
5. **Testing:** relevant test files and assertions, what is missing, and whether tests were run.
6. **Limitations:** ambiguity, evidence gaps, edge cases and questions needing review.

These checks reject missing guides and invalid references; they cannot prove that a feature boundary or explanation is correct. Length is a minimum safeguard, not a quality score. Agents must inspect source and tests, not pad text to pass validation. Unsupported conclusions belong in limitations rather than authoritative prose.

The UI displays the guide and clickable entry points in Field notes. Reviewers can edit the documentation in the map editor. Generated guides retain their index timestamp; after a reindex, guides tied to an older index show a refresh notice. Atlas does not automatically rewrite them or claim they are always current.

## Product overview

Agent candidates must include `project.overview`:

- `what`: 80–8000 characters explaining the product and its audience.
- `usage`: interface entries with `name`, `description`, optional `commands`, and `evidence`. Explain actual MCP, CLI, API or UI entry points that exist.
- `workflow`: ordered entries with `title`, `description`, optional boolean `optional`, and `evidence`. Explain how inputs become useful outputs; label optional stages and feedback paths.
- `tools`: actual tool/command groups with `name`, `description` and `evidence`. Do not invent tool names from a generic category such as “find.”
- `connections`: entries with declared feature IDs `from` and `to`, an explanatory `description`, and `evidence`. These are authored explanations, not additional indexed graph edges. A genuinely independent single-feature product may have no connections.

Each list is bounded to 100 entries; descriptions need at least 40 characters. All citations are checked against indexed files and current line ranges. Validate interface commands against actual entry scripts and package configuration, not README claims alone.

The default product screen displays this overview. A generated candidate can be previewed at `?project=PROJECT_ID&map=candidate`, including feature drill-down, without saving or marking it reviewed. Product graph/node/status requests accept `map=candidate` for this read-only selection. Candidate projection caches remain inside the project data directory. The saved map remains separate.

## Functionality walkthroughs

`project.overview.journeys` is an optional list of 1–20 user-goal walkthroughs. The primary audience is nontechnical: describe actions and expected outcomes in ordinary language, and place source references behind evidence disclosures. Detailed technical product documentation stays available separately.

Each journey has `id`, `title`, `goal`, `start` (a step ID), `steps` and optional `scenarios`. Every step has `id`, `title`, `action`, `result`, `evidence` and optional `choices` (`label`, `next`) and `details`. Details have `title`, `description`, `evidence` and may contain nested `details`, up to four nested levels. Branches must describe behavior observed in source, not an invented screen or configuration prompt. User choices explore hypothetical situations; they do not operate the product.

Step IDs must be unique, every step must be reachable, and paths must end rather than cycle. The UI keeps the chosen path visible and lets readers replace later steps by choosing a different situation earlier in the path. Details expand progressively without requiring code navigation.

Scenarios have `title`, `given`, `when`, `then`, `evidence` and `steps`, a complete valid path from the journey’s start to a terminal outcome. These are generated behavior expectations, not test executions or evidence of passing tests. Validate paths, citations and source-supported outcomes before submission. Do not infer unobserved failure handling, permissions or user-visible messages.

The explorer renders a diagram by default. Optional `mainPath` must follow declared choices from the start to a terminal outcome; `diagramLabel` provides short node text, and `benefit` explains the concrete reason for a stage. Expanding a node reveals supported alternative branches. Longer explanations, evidence and behavior scenarios are secondary. Quantitative efficiency claims require independent supporting measurements.
