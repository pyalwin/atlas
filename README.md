<div align="center">

<img src="public/atlas.svg" alt="Atlas logo" width="96" height="96" />

# Atlas

**Your code, connected.**

Turn a local Git checkout into a three-dimensional code universe — from product features down to folders, files, classes, and functions, with every connection backed by file-and-line evidence.

[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Node.js](https://img.shields.io/badge/node-20.19%2B%20%7C%2022.12%2B-5FA04E?logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![React](https://img.shields.io/badge/react-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![Three.js](https://img.shields.io/badge/three.js-r180-000000?logo=threedotjs&logoColor=white)](https://threejs.org/)
[![Vite](https://img.shields.io/badge/vite-7-646CFF?logo=vite&logoColor=white)](https://vite.dev/)
<br />
[![Local-first](https://img.shields.io/badge/local--first-no%20telemetry-83E8CB)](#-privacy-and-local-storage)
[![No LLM calls](https://img.shields.io/badge/LLM%20calls-none-0B161D)](#-how-it-works)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/pyalwin/atlas)](https://github.com/pyalwin/atlas/commits/main)
[![GitHub stars](https://img.shields.io/github/stars/pyalwin/atlas?style=flat&logo=github)](https://github.com/pyalwin/atlas/stargazers)

[Quick start](#-quick-start) ·
[Features](#-features) ·
[PR review](#-review-a-pull-request) ·
[Language coverage](#-language-coverage) ·
[CLI](#-cli) ·
[API](#-local-api) ·
[Contributing](#-development) ·
[License](#-license)

</div>

---

## ✨ Features

- **Product universe** — Atlas suggests a feature map from repository evidence. Review it, rename and regroup areas, then follow features into the code that implements them.
- **Evidence, not guesses** — every connection is grouped from real source references with the referring line and its target one click away.
- **Technical depth** — browse folders, files, classes, and functions; search anything with <kbd>Cmd</kbd>/<kbd>Ctrl</kbd>+<kbd>K</kbd>.
- **Guided PR review** — compare a GitHub pull request or two local refs through five lenses: Change, Files, Tests, Questions, and Verdict, with CI results for the exact head commit.
- **Multiple projects** — each saved project has its own index, product map, cache, and update settings.
- **Private by design** — Atlas reads code locally. It does not import the repository, execute its application, call an LLM, or send telemetry.

## 🚀 Quick start

**Requirements**

| Tool | Version |
| --- | --- |
| Git | A local working directory to explore |
| Python | 3.12 or newer (one that understands your repository's syntax) |
| Node.js | 20.19+ within the 20.x line, or 22.12+, with npm |

```sh
git clone https://github.com/pyalwin/atlas.git
cd atlas
npm install
npm run dev
```

Open **http://127.0.0.1:4317**, open the repository menu in the top-right corner, and add a local checkout by its absolute folder path. No existing index is required: the server indexes the repository in the background and prepares a suggested feature map. Saved projects remain available after restarting Atlas.

`npm run dev` starts the UI on port `4317` and the local API on port `4318`; <kbd>Ctrl</kbd>+<kbd>C</kbd> stops both. The launcher finds a compatible Python — set `ATLAS_PYTHON` to choose one explicitly:

```sh
export ATLAS_PYTHON=/absolute/path/to/python3
npm run dev
```

<details>
<summary><b>Run the production build</b></summary>

```sh
npm run build
npm run serve
```

Then open **http://127.0.0.1:4318**. The server binds to localhost.

</details>

## 🧭 How it works

```text
local checkout ──▶ indexer (Python stdlib + Babel) ──▶ SQLite source graph
                                                          │
                     reviewed product map ◀── suggestions ┤
                                                          ▼
                              local API (127.0.0.1:4318) ──▶ React + Three.js UI
```

1. **Index** — Git's tracked and non-ignored files are parsed into definitions and references.
2. **Suggest** — a candidate product map groups code into features, capabilities, and foundations.
3. **Review** — you confirm names, descriptions, and paths; the reviewed map survives every refresh.
4. **Explore** — the product projection and technical graph are served to the UI with evidence for each edge.

Explanations come from source docstrings, parser facts, diffs, and static references — **no model calls**.

## 🗺️ Review the product map

The first map is a suggestion based on repository evidence. Review names, descriptions, code paths, and supporting references in the project editor before treating those groups as product boundaries. Rename, regroup, and explain areas, then save a reviewed map.

- **Features** describe product behavior and can span several folders.
- **Capabilities** divide selected parts of a feature.
- **Foundations** group shared support such as storage or identity.

Paths are literal repository-relative prefixes with segment boundaries. The longest matching prefix wins, so a specific feature path can override a broad foundation path. Capability paths must stay within their feature, and peer capability paths cannot overlap.

Refreshing the source index preserves your reviewed map. **New suggestions** creates a separate candidate for comparison without replacing it. Saving map edits updates the product projection without re-parsing the repository.

Unassigned files remain available in the technical view. Two features using the same foundation receive no inferred connection: every displayed feature connection groups actual indexed source references. See [`docs/PRODUCT_MAP.md`](docs/PRODUCT_MAP.md) for the map format and assignment rules.

## 🔭 Explore

| Action | How |
| --- | --- |
| Navigate | Start in the product universe, select a feature, open its capabilities or implementation areas |
| Go deeper | Switch to technical depth for repository structure and shared foundations |
| Move | Drag to orbit, scroll to zoom, use the fit control to frame the current space |
| Search | <kbd>Cmd</kbd>/<kbd>Ctrl</kbd>+<kbd>K</kbd> for names and paths; breadcrumbs move outward |
| Inspect | Field notes show explanations, source references, and dependencies; the Connections table is a list view |
| Verify | Connection evidence opens the referring line and its target (read-only excerpts) |
| Focus | Pause drifting for a still view — lists and tables work without WebGL |

Large technical spaces show a bounded selection of children and links; search can find items outside it. Product groupings and spatial positions aid navigation — they do not establish ownership, deployment boundaries, or execution order.

## 🔍 Review a pull request

Choose **PR review** in the top bar, then enter a GitHub pull request URL or number for the selected repository — or compare two local Git refs, such as `main` and a feature branch.

Review starts in the code universe. A navigation dock moves through five lenses, and selecting a file opens its diff alongside the map:

| Lens | What you get |
| --- | --- |
| **1. Change** | Purpose and impact: a concise summary and a constellation of changed definitions and sampled calls. Before, Changes, and After share stable positions. |
| **2. Files** | File explanations and an explanation beside each diff block. Add local inline comments or turn a suggested concern into a draft; unresolved comments feed the verdict. |
| **3. Tests** | Written scenarios, assertions, gaps to assess, and GitHub Actions results for the pinned head commit. Validation evidence is recorded separately from test sufficiency. |
| **4. Questions** | Source-grounded choices and short reflections that check understanding of changed behavior. |
| **5. Verdict** | A local recommendation from reviewed files, open comments, understanding, and recorded validation. Failed or pending CI needs attention. Nothing is submitted to GitHub. |

<details>
<summary><b>Scope, CI evidence, and caching details</b></summary>

**Comparison scope.** Atlas compares the merge base with the proposed head commit, matching a pull request's scope even when its target branch has moved ahead. It builds separate snapshot indexes, leaving the working tree and the project's ordinary index intact. Uncommitted changes are not included; removed code and connections remain reviewable from the base snapshot.

**Loading PRs.** GitHub PRs use the selected checkout's GitHub `origin` and download the required refs. A PR from a different repository must be opened in that repository's Atlas project. Local comparisons need both refs and enough shared history to resolve a merge base. In shallow checkouts, PR loading fetches up to 200 commits of history; fetch more if the common ancestor is older.

**Explanations.** Generated from the compared source, diff, and static references without a model call. Authored intent is distinguished from observed changes. Connections show references, not proven execution order; unsupported languages fall back to diff evidence. Changed tests are not presented as passed tests, measured coverage, or proof of correctness. Atlas never runs repository code during review.

**The universe** focuses on implementation and connected code, then minimizes in Tests, Questions, and Verdict. Test files remain in the Tests view and file navigator but do not appear as worlds in the impact map. The graph covers direct neighbors of changed files, not every transitive consequence. Parser coverage, skipped files, and bounded results are disclosed.

**CI evidence.** Read-only GitHub API requests fetch workflow runs and check runs for the exact head SHA. The repository must match the project's GitHub origin. Atlas tries the public API and can fall back to existing `gh` authentication; unavailable access, rate limits, and incomplete results are disclosed. It does not rerun workflows, download logs or coverage artifacts, inspect required-check rules, or substitute results from a separate merge commit. Passing CI does not establish coverage or sufficiency; skipped checks stay visible, and the reviewer explicitly chooses whether to use passing results as validation evidence.

**Author-agent panel.** Prepares a question with the review's exact revisions and file context for copying. No agent is connected and nothing is sent automatically.

**Caching.** Completed reviews and snapshot indexes are cached under the project's `reviews/` directory, and returning to PR review restores the latest completed scope. Reviewer answers and notes are scoped in the browser to the project, review, exact commits, and guide version; file checkmarks are saved per comparison on the device. A new comparison does not inherit approval. Inline comments remain local drafts.

</details>

## 🔄 Keep a project current

Use the project's refresh action to rebuild its index. The old graph stays readable while rebuilding; a completed index replaces it atomically.

Automatic updates are **off by default**. When enabled, Atlas periodically checks local source paths, sizes, and modification times, waits for changes to settle, and queues a full background reindex (not incremental parsing). A single worker serializes indexing, suggestion generation, and change checks across projects.

Change checks detect added, modified, and deleted source files. An adopted legacy index has no historical fingerprint, so freshness stays unknown until its first refresh; Atlas does not rebuild a large adopted repository just because the app starts. The graph reflects index time, while source previews read the current working copy — an edit can move a stored reference line until the next index.

## 🌐 Language coverage

| Language | Indexed structure and references |
| --- | --- |
| **Python** (`.py`, `.pyi`) | Classes, functions, methods, and nested definitions; resolved local imports; conservative direct calls and inheritance. Repository root and `src/` are import roots. |
| **JavaScript / TypeScript** (incl. JSX / TSX) | Named definitions and relative ES-module imports via the bundled Babel parser. Calls, package aliases, and CommonJS `require` are not resolved. |
| **Other source, config, and docs** | Files, paths, languages, and line counts. No semantic relationships are claimed. |

The status response reports actual per-language coverage and parse warnings. Unparseable files stay visible without definitions or references; if the JavaScript parser is unavailable, those files fall back to file-level coverage.

> [!NOTE]
> Python resolution is deliberately conservative. Dynamic imports, wildcard targets, arbitrary instance dispatch, callbacks, dependency injection, and reassigned or ambiguous bindings can remain unresolved. Lambda and comprehension bodies are omitted, and external libraries are not graph nodes. **Missing edges do not establish independence.**

## 🔒 Privacy and local storage

Atlas has no remote graph service and no telemetry. Indexes and maps stay local and are ignored by Git; installing npm dependencies is the only registry access.

The registry is `data/projects.json`. Each project keeps its files under `data/projects/<project-id>/`, where the ID is a stable hash of the canonical checkout path (adding the same checkout again reuses it):

| File | Purpose |
| --- | --- |
| `atlas.db` | SQLite source graph |
| `product-map.json` | Saved suggested or reviewed map |
| `suggested-map.json` | Separately requested replacement candidate, when present |
| `product-projection.json` | Cached feature connections and proof samples |
| `source-fingerprint.json` | Source-file sizes and modification times for change detection |

If an earlier `data/atlas.db` exists, Atlas adopts it in place without copying or reindexing, using a `product-map.json` beside it when available or discovering a suggested map otherwise.

> [!IMPORTANT]
> Indexing excludes dependency/build/tool directories, selected generated paths, lockfiles, `.env` files, secret-named files, symlinks, binary or non-UTF-8 files, and files over 2 MB. These filename rules are **not** a content-based secret scanner. The graph and cache contain repository-derived paths, names, summaries, and evidence. Exact rules live in [`atlas_index/indexer.py`](atlas_index/indexer.py).

## 💻 CLI

The UI manages saved projects. The CLI can also add or refresh a managed project without replacing another project's index:

```sh
npm run index -- /absolute/path/to/repository
```

Use a separate registry and project-data directory:

```sh
node scripts/python.mjs -m atlas_index serve --data-dir /absolute/path/to/atlas-data --port 4318
```

Run `npm run dev:ui` in another terminal for the development UI, or build `dist/` first and use the same server for UI and API.

<details>
<summary><b>Standalone index</b></summary>

```sh
node scripts/python.mjs -m atlas_index index /absolute/path/to/repository --db /absolute/path/to/atlas.db
node scripts/python.mjs -m atlas_index serve --db /absolute/path/to/atlas.db --port 4318
```

The server adopts an explicitly supplied index without copying it. A custom standalone index uses a `product-map.json` beside it when available, or queues map discovery from the existing graph. Add repositories through the UI for the complete project workflow.

</details>

## 🔌 Local API

All write requests require an `application/json` object body — including `{}` for actions without parameters. Request size is bounded. Host and Origin checks apply to reads and writes; local browser CORS preflights are supported.

<details>
<summary><b>Endpoint reference</b></summary>

**Projects**

| Endpoint | Purpose |
| --- | --- |
| `GET /api/projects` | Saved projects and `activeProjectId` |
| `POST /api/projects` with `{path,name?}` | Validate a local Git checkout, register it, and queue its first index |
| `POST /api/projects/:id/index` | Queue a background refresh |
| `POST /api/projects/:id/activate` | Select the active project |
| `PATCH /api/projects/:id` with `{name?,autoUpdate?}` | Rename a project or change automatic updates |
| `GET /api/projects/:id/changes` | Check freshness; `checking:true` means the serialized worker is busy |
| `GET /api/projects/:id/map` | Saved `map`, separate `suggestion`, and project state |
| `PUT /api/projects/:id/map` with `{map}` | Validate and atomically save a reviewed map |
| `POST /api/projects/:id/suggestions` | Queue a new candidate without replacing the saved map |

**Technical graph**

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | Index counts, language coverage, warnings, and project metadata; 409 before a first index |
| `GET /api/graph?parent=…` | Technical children and aggregated relationships |
| `GET /api/node?id=…` | Technical details, references, and breadcrumbs |
| `GET /api/search?q=…` | Search indexed names and paths |
| `GET /api/source?id=…&line=…` | Current bounded source excerpt; evidence line is optional |

**Product map**

| Endpoint | Purpose |
| --- | --- |
| `GET /api/product/graph?parent=…` | Product universe, capabilities, or implementation roots |
| `GET /api/product/node?id=…` | Product description, direct connections, foundations, and source roots |
| `GET /api/product/status` | Mapped, excluded, and unassigned file coverage |

**Review**

| Endpoint | Purpose |
| --- | --- |
| `GET /api/review/options` | Available Git refs, comparison defaults, and GitHub repository identity |
| `POST /api/review` with `{pr}` or `{base,head}` | Queue a PR or local-ref comparison for the selected project |
| `GET /api/review` | Latest review job and completed comparison, including changed files and impact graph |
| `GET /api/review/guide?review=…` | Cached, versioned review brief, impact evidence, file explanations, test evidence, and questions |
| `GET /api/review/ci?review=…` | Read-only GitHub workflow and check results for the exact head SHA; `review` is required |
| `GET /api/review/diff?path=…` | Bounded diff for one changed file |
| `GET /api/review/source?path=…&side=base\|head&line=…` | Source evidence from a compared commit |

**Notes**

- Append `project=<id>` to graph, status, product, search, source, and review requests to select a project explicitly; otherwise the active project is used.
- Project jobs expose `queued`, `running`, `complete`, or `error` through the project record; review jobs are reported by the review endpoint.
- Pass `review=<id>` to guide, diff, and source requests to stay tied to an immutable comparison while another job runs. CI requests require it and reject a missing or mismatched scope.
- Technical edge weights count distinct evidence locations. Product edge weights count distinct source-target-kind relationships, with `referenceSites` counting their evidence locations. Product responses include relationship-type counts and up to five proof samples.
- Projection caches are invalidated by source-index timestamp, map content hash, and projection version.

</details>

## 🧪 Development

```sh
npm test        # Python unittest suite + Node review tests
npm run build   # production UI bundle in dist/
```

Tests use temporary repositories to cover parsing, projection evidence, project isolation, queued success and failure, reviewed-map preservation, freshness checks, cache invalidation, and local API restrictions. Python code uses only the standard library; JavaScript/TypeScript extraction uses the installed Babel parser through Node.js.

<details>
<summary><b>Project layout</b></summary>

```text
atlas_index/   Python indexer, product map, review engine, and local API server
src/           React UI and the Three.js universe
scripts/       Dev launcher, Python discovery, and the Babel scanner
tests/         Python unittest and Node test suites
docs/          Product map format
```

</details>

## 📄 License

Atlas is released under the [MIT License](LICENSE). Third-party dependencies keep their own licenses (MIT, ISC, and BSD-3-Clause).

---

<div align="center">
<sub>Built by <a href="https://github.com/pyalwin">@pyalwin</a> · Atlas reads your code locally and never sends it anywhere.</sub>
</div>
