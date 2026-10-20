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
