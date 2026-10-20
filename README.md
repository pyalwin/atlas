# Atlas

Atlas turns a local Git checkout into a three-dimensional code universe. Start with suggested product features, review their explanations and boundaries, then follow them into folders, files, classes, and functions. Connections come from references found in the source, with file and line evidence you can open.

Each saved project has its own index, product map, cache, and update settings. Atlas reads code locally; it does not import the repository, execute its application, or call an LLM.

## Requirements and startup

- A local Git working directory.
- Python 3.12 or newer. Use a Python version that understands your repository's syntax.
- Node.js 20.19+ within the 20.x line, or 22.12+, and npm.

From this folder:

```sh
npm install
npm run dev
```

Open [Atlas](http://127.0.0.1:4317), open the repository menu in the top-right corner, and add a local checkout by its absolute folder path. No existing index is required. The server indexes the repository in the background and prepares a suggested feature map. Saved projects remain available after restarting Atlas.

The development command starts the UI on port 4317 and the local API on port 4318. Ctrl+C stops both. The launcher finds a compatible Python; set `ATLAS_PYTHON` to choose one explicitly:

```sh
export ATLAS_PYTHON=/absolute/path/to/python3
npm run dev
```

For the built UI:

```sh
npm run build
npm run serve
```

Then open [the built Atlas UI](http://127.0.0.1:4318). The server binds to localhost.

## Review the product map

The first map is a suggestion based on repository evidence. Review names, descriptions, code paths, and supporting references in the project editor before treating those groups as useful product boundaries. You can rename, regroup, and explain areas, then save a reviewed map.

A feature can span several folders. Capabilities divide selected parts of a feature; foundations group shared support such as storage or identity. Paths use literal repository-relative prefixes with segment boundaries. The longest matching prefix wins, so a specific feature path can override a broad foundation path. Capability paths must stay within their feature, and peer capability paths cannot overlap.

Refreshing the source index preserves the map you reviewed. **New suggestions** creates a separate candidate for comparison; it does not replace the saved map. Saving map edits updates the product projection without parsing the repository again.

Unassigned files remain available in the technical view. Two features using the same foundation receive no inferred connection to each other: every displayed feature connection groups actual indexed source references.

## Explore

- Start in the product universe, select a feature, and open its capabilities or implementation areas.
- Switch to technical depth to browse the repository structure and shared foundations. Foundation names are searchable there; the product panels focus on features and their connections.
- Drag to orbit, scroll to zoom, and use the fit control to bring the current space into view.
- Use breadcrumbs to move outward; search names and paths with Cmd+K or Ctrl+K.
- Open field notes for explanations, source references, and dependencies. The Connections table provides a list view.
- Open connection evidence to inspect the referring line and its target. Source excerpts are read-only.
- Pause drifting for a still view. Lists and connection tables remain useful without WebGL.

Large technical spaces show a bounded selection of children and links. Search can find items outside that selection. Product groupings and spatial positions help navigation; they do not establish ownership, deployment boundaries, or execution order.

## Keep a project current

Use the project's refresh action to rebuild its index. The old graph stays readable while an existing project is rebuilding; a completed index replaces it atomically.

Automatic updates are **off by default**. Enabling them checks local source paths, sizes, and modification times periodically, waits for changes to settle, and queues a full background reindex. This is a full rebuild, not incremental parsing. A single worker serializes indexing, suggestion generation, and background change checks across projects.

Change checks detect added, modified, and deleted source files. An adopted legacy index has no historical file fingerprint, so freshness stays unknown until its first refresh establishes a reliable baseline. Atlas does not rebuild a large adopted repository just because the app starts.

The graph reflects index time. Source previews read the current working copy, so an edit can move a stored reference line until the next index.

## Language coverage

| Language | Indexed structure and references |
| --- | --- |
| Python (`.py`, `.pyi`) | Classes, functions, methods, and nested definitions; resolved local imports; conservative direct calls and inheritance. Repository root and `src/` are recognized import roots. |
| JavaScript / TypeScript, including JSX / TSX | Named definitions and relative ES-module imports through the bundled Babel parser. Calls, package aliases, and CommonJS `require` are not resolved. |
| Other supported source, configuration, and documentation formats | Files, paths, languages, and line counts. No semantic relationships are claimed. |

The status response reports actual per-language coverage and parse warnings. A file that cannot be parsed can remain visible without its definitions or references. If the JavaScript parser is unavailable, those files remain at file-level coverage and the status reports that limitation.

Python resolution is deliberately conservative. Dynamic imports, wildcard targets, arbitrary instance dispatch, callbacks, dependency injection, and reassigned or ambiguous bindings can remain unresolved. Lambda and comprehension bodies are omitted. External libraries are not graph nodes. Missing edges do not establish independence.

Technical summaries use source docstrings or parser facts. Suggested and reviewed product descriptions retain their evidence and origin. Atlas makes no model calls to generate the map or explanations.

## Local storage

The registry is `data/projects.json`. New projects keep separate files under `data/projects/<project-id>/`:

| File | Purpose |
| --- | --- |
| `atlas.db` | SQLite source graph. |
| `product-map.json` | Saved suggested or reviewed map. |
| `suggested-map.json` | Separately requested replacement candidate, when present. |
| `product-projection.json` | Cached feature connections and proof samples. |
| `source-fingerprint.json` | Source-file sizes and modification times for change detection. |

Project IDs are stable hashes of canonical checkout paths. Adding the same checkout again reuses its saved project. Indexes and maps remain local and are ignored by Git.

If the earlier `data/atlas.db` exists, Atlas adopts it in place without copying or reindexing it. An existing `product-map.json` beside that index is used when available; otherwise Atlas discovers a suggested map from the indexed repository. See [PRODUCT_MAP.md](docs/PRODUCT_MAP.md) for the map format and assignment rules.

Indexing uses Git's tracked and non-ignored file list, including non-ignored untracked files. It excludes dependency/build/tool directories, selected generated paths, lockfiles, `.env` files, secret-named files, symlinks, binary or non-UTF-8 files, and files over 2 MB. These filename rules are not a content-based secret scanner. Exact rules are in `atlas_index/indexer.py`.

The graph and cache contain repository-derived paths, names, summaries, and evidence. Atlas has no remote graph service or telemetry. Installing npm dependencies uses the package registry.

## CLI

The UI manages saved projects. The CLI can also add or refresh a managed project without replacing another project's index:

```sh
npm run index -- /absolute/path/to/repository
```

For a separate registry and project-data directory:

```sh
node scripts/python.mjs -m atlas_index serve --data-dir /absolute/path/to/atlas-data --port 4318
```

Use `npm run dev:ui` in another terminal for the development UI, or build `dist/` first and use the same server for both UI and API.

For a standalone index:

```sh
node scripts/python.mjs -m atlas_index index /absolute/path/to/repository --db /absolute/path/to/atlas.db
node scripts/python.mjs -m atlas_index serve --db /absolute/path/to/atlas.db --port 4318
```

The server adopts an explicitly supplied index without copying it. A custom standalone index uses a `product-map.json` beside that index when available, or queues map discovery from the existing graph. Add repositories through the UI for the complete project workflow.

## Local API

All write requests require an `application/json` object body, including `{}` for actions without parameters. Request size is bounded. Host and Origin checks apply to local API reads and writes; local browser CORS preflights are supported.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/projects` | Saved projects and `activeProjectId`. |
| `POST /api/projects` with `{path,name?}` | Validate a local Git checkout, register it, and queue its first index. |
| `POST /api/projects/:id/index` | Queue a background refresh. |
| `POST /api/projects/:id/activate` | Select the active project. |
| `PATCH /api/projects/:id` with `{name?,autoUpdate?}` | Rename a project or change automatic updates. |
| `GET /api/projects/:id/changes` | Check freshness; `checking:true` means the serialized worker is busy. |
| `GET /api/projects/:id/map` | Saved `map`, separate `suggestion`, and project state. |
| `PUT /api/projects/:id/map` with `{map}` | Validate and atomically save a reviewed map. |
| `POST /api/projects/:id/suggestions` | Queue a new candidate without replacing the saved map. |
| `GET /api/status` | Index counts, language coverage, warnings, and selected project metadata. Returns 409 before a first index is available. |
| `GET /api/graph?parent=…` | Technical children and aggregated relationships. |
| `GET /api/node?id=…` | Technical details, references, and breadcrumbs. |
| `GET /api/search?q=…` | Search indexed names and paths. |
| `GET /api/source?id=…&line=…` | Current bounded source excerpt; evidence line is optional. |
| `GET /api/product/graph?parent=…` | Product universe, capabilities, or implementation roots. |
| `GET /api/product/node?id=…` | Product description, direct connections, foundations, and source roots. |
| `GET /api/product/status` | Mapped, excluded, and unassigned file coverage. |

Append `project=<id>` to graph, status, product, search, and source requests to select a saved project explicitly. Otherwise they use the active project. Background jobs expose `queued`, `running`, `complete`, or `error` states through the project record.

Technical edge weights count distinct evidence locations. Product edge weights count distinct source-target-kind relationships; `referenceSites` counts their evidence locations. Product responses include relationship-type counts and up to five source-and-target proof samples. Projection caches are invalidated by source-index timestamp, map content hash, and projection version.

## Development checks

```sh
npm test
npm run build
```

Tests use temporary repositories to cover parsing, projection evidence, project isolation, queued success and failure, reviewed-map preservation, freshness checks, cache invalidation, and local API restrictions. Python code uses the standard library; JavaScript/TypeScript extraction uses the installed Babel parser through Node.js.
