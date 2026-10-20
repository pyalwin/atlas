<div align="center">

<img src="public/atlas.svg" alt="Atlas logo" width="96" height="96" />

# Atlas

**Code is growing faster. Understanding it should keep up.**

[![CI](https://github.com/pyalwin/atlas/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/pyalwin/atlas/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Local-first](https://img.shields.io/badge/local--first-no%20telemetry-83E8CB)](#your-code-stays-with-you)
[![GitHub stars](https://img.shields.io/github/stars/pyalwin/atlas?style=flat&logo=github)](https://github.com/pyalwin/atlas/stargazers)
<br />
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](#get-started)
[![Node.js](https://img.shields.io/badge/node-22.12%2B%20%7C%2024%2B-5FA04E?logo=nodedotjs&logoColor=white)](#get-started)

[What you can do](#what-you-can-do) · [Get started](#get-started) · [Your code stays with you](#your-code-stays-with-you)

</div>

Coding agents make it easier to produce more code, faster. But someone still needs to understand how it fits together, what a change affects, and whether it should ship. As repositories grow, that becomes harder to see from folders and diffs alone.

Atlas helps you navigate that growing codebase and review changes with context. Start with what the product does, follow a feature into its implementation, and explore how the pieces connect. Open a pull request to see the changed code alongside the parts it touches, then inspect the source behind each connection.

Our aim is to make every change easier to understand and review, so reviewers can follow what's happening in the code they approve—even when much of it was written by an agent.

## What you can do

- **Find your way through a growing project.** Explore features, folders, files, classes, and functions in an interactive map.
- **See a change in context.** Review a pull request or compare branches alongside nearby dependencies, rather than piecing everything together from a diff.
- **Check the evidence.** Follow connections to their source lines, inspect changed tests, and keep review notes locally.
- **Build shared understanding.** Refine suggested feature names and descriptions as you learn the project, and keep those edits when the code changes.

The first map is a starting point. Review its suggestions as you learn the project. Atlas finds static code relationships; it won't capture every connection that happens at runtime.

## Get started

You'll need Git, Node.js 22.12+ or 24+, and Python 3.12+. On macOS or Linux:

```sh
git clone https://github.com/pyalwin/atlas.git
cd atlas
node scripts/setup.mjs --client codex --repository /path/to/your/project
```

Use `claude` or `vscode` instead of `codex` for those clients. Setup prepares Atlas and connects it to the project you choose.

Reload your client or start a new session in that project, accept its normal trust prompt, and ask:

> Use Atlas to explore this repository and return its UI link.

Atlas opens a local web app for your project. Start with a feature, follow it into the code, or press **⌘K / Ctrl+K** to search. You can also ask your coding assistant to read the source and suggest better feature descriptions, then review them in Atlas.

### Use it on its own

If you'd rather explore directly in your browser, run these commands from the Atlas folder:

```sh
npm install
npm run dev
```

Open [Atlas](http://127.0.0.1:4317), choose the repository menu, and add your project's folder.

## Your code stays with you

Atlas reads and indexes your checkout without running your application. It has no telemetry and makes no model calls itself. When connected through MCP, its maps and index live in your project's ignored `.atlas/` folder. If you ask a coding assistant to generate descriptions, that assistant uses its own session and data settings.

For agents and technical contributors, see [AGENTS.md](AGENTS.md).

[MIT License](LICENSE) · Built by [@pyalwin](https://github.com/pyalwin)
