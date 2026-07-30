# web-replicate

A Claude Code plugin that documents a web app in **reconstruction-grade** detail —
so it can be *rebuilt*, not just diagnosed. It enters the app as a real user, drives
each end-to-end journey in a headless browser, and captures everything observable
(rendered markup, stylesheets/scripts/asset graph, Web Storage, console, and a full
sanitized request/response log), then infers the backend contract (endpoints,
request/response shapes, auth scheme, data models) and writes a rebuild blueprint.

Sibling of [`web-qa`](https://github.com/mrskwiw/web-qa) — same instrument, aimed at
replication instead of judgment.

## Install

```bash
/plugin marketplace add mrskwiw/mrskwiw-plugins
/plugin install web-replicate@mrskwiw-plugins
```

## What it produces

- **`blueprint.md`** — the human reconstruction spec (tech stack, per-page frontend, user paths, inferred backend).
- **`backend.json`** — just the inferred contract, ready to feed a codegen step.
- **`blueprint.json`** — the full assembled record, beside the raw captures (`pages/`, `bodies/`, `screenshots/`, `assets/`).

## Engine subcommands

Run from `skills/web-replicate/` after `pip install -r requirements.txt && python -m playwright install chromium`:

| Command | Purpose |
|---|---|
| `capture` | One page → reconstruction-grade `PageCapture` (markup, assets, storage, network, **design tokens** — CSS variables, color palette, fonts, spacing, radii, shadows, breakpoints). |
| `trace` | Drive an ordered user path in one persistent (optionally authenticated) context; per-step network deltas are the backend evidence. |
| `verify-auth` | The one **active** check — probe already-observed endpoints with/without the session to turn the *inferred* auth column into a *verified* one (read-only by default). |
| `blueprint` | Render `blueprint.md` / `blueprint.json` / `backend.json` from an assembled results object. |

## Design

Two halves with a stable JSON seam: a deterministic Python **engine** (the hands —
captures evidence, makes no judgments) and the **Claude Code agent** running
`SKILL.md` (the reasoning — infers intent, writes the blueprint). Capture is
**passive** (only what normal use loads); `verify-auth` is the single, opt-in,
read-only active exception, for a target you own.

## Layout

```
.claude-plugin/plugin.json     plugin manifest
skills/web-replicate/
├── SKILL.md                   the agent workflow
├── engine/                    deterministic capture engine (capture/trace/verify-auth/blueprint)
├── requirements.txt           playwright + click
└── tests/                     pytest suite
```
