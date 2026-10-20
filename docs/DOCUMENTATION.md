# Writing product documentation for Atlas

The purpose of an Atlas guide is to help someone understand what they can do, what happens, and why. Start with the product's language. Source paths are evidence behind the explanation.

## Discover, trace, explain, review

1. Establish the product's audience, principal jobs and vocabulary from documentation **and the real entry points**. A code search service has users, operations and rules even if it has no screens. “Find the code needed for a change” is a job; “Tools folder” is not.
2. Trace each job from its trigger through guards, transformations, state changes and outputs. Read the implementation and relevant tests. Keep a ledger of claim → source path and line → what the source proves. Distinguish documentation intent from implemented behavior.
3. Write the outcome first, then explain the main route and important alternatives. Use short active sentences and concrete results. Define unfamiliar product words once. Put implementation details behind disclosures.
4. Verify every workflow route, rule and prerequisite against current source. Confirm defaults and optional behavior. Describe gaps in `documentation.limitations`. Never infer runtime order from import links, or test success from the existence of a test.
5. Submit an unreviewed candidate tied to the index timestamp. Preserve reviewed identities and ownership. A schema validator checks shape and source locations; the author must check meaning.

This procedure and the complete field example are delivered to agents by `get_summary_context` in `generationGuide.authoringProtocol` and `generationGuide.contract`. Atlas makes no model calls: the connected agent session does the investigation and writing.

## Reader guide contract

New generated candidates require `readerGuide` on the project and each feature. Capabilities and foundations can include a guide when they have distinct workflows. Existing saved maps remain readable without one.

| Field | Meaning |
| --- | --- |
| `audience` | Who uses this product or feature; at most 500 characters. |
| `outcome` | `{text, evidence}` explaining the achievable result. |
| `prerequisites` | Up to 12 `{text, evidence}` requirements. Empty when none are established. |
| `rules` | Up to 20 `{text, evidence}` constraints or invariants the reader needs to know. |
| `glossary` | Up to 30 `{term, meaning, evidence}` definitions using the product's actual vocabulary. Terms are unique. |
| `workflows` | One to eight source-backed journeys. Each has `trigger`, `mainPath` and at most 30 steps. Prefer three to eight meaningful steps. |

Evidence uses repository-relative `{path, line, reason?}` objects. Current generated candidates validate that the indexed source exists and the line is in range. Cite what actually supports the claim; a valid line alone does not establish truth. Do not fabricate prerequisites or rules to fill the arrays.

Workflows retain the journey contract in [PRODUCT_MAP.md](PRODUCT_MAP.md): stable IDs, goal, start, steps, choices, details, evidence and optional given/when/then scenarios. Each reader-guide step adds a short `diagramLabel` (at most 80 characters), with optional `kind` (`action`, `decision`, `outcome`). A decision has at least two supported choices; an outcome terminates its route. Label choice edges with the actual condition or result. Every route must be reachable and end. The explicit `mainPath` identifies the normal route; supported alternatives remain part of the same workflow.

For example, describe a failed lookup as “No matching code found → try another search” only when the handler supports that outcome. Do not assume embeddings or model generation are required because their modules exist. Do not claim that reduced payloads prove measured token savings.

## Diagrams and prose use one model

Atlas generates Mermaid flowcharts from the structured workflow. Authors do not submit raw Mermaid or a second disconnected chart. Decisions, branches and outcomes come from the same steps used by the interactive explanation. Readers can copy the generated Mermaid into other documentation.

The renderer loads on demand, renders locally, uses strict security and text labels, and follows the current theme. Labels are escaped; source IDs are replaced with generated diagram IDs. No author-supplied click directives, links or configuration are executed. Accessible step buttons expose explanations and evidence even if chart rendering fails. Readers can also expand the original interactive diagram step by step.

## Updating documentation

Treat a change to a function body as potentially affecting behavior even when its signature and imports stay identical. Re-read the affected workflow, rules and test assertions. Preserve stable IDs and the saved reviewed map; submit a separate candidate. Incremental analysis planning is a separate future improvement, not implemented by this contract.
